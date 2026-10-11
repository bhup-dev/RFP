"""Qdrant collection access for document-chunk embeddings.

Importing this module does not open a connection. The client is created on
first use. PostgreSQL remains the source of truth for chunk records.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PointStruct,
    VectorParams,
)

from app.config import Settings, settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChunkPoint:
    """One chunk embedding and the citation payload stored beside it.

    ``bid_id``, ``document_id``, and ``chunk_id`` are the PostgreSQL UUID
    primary keys. The Qdrant point id is ``chunk_id``, so a repeated upsert
    updates the same point.
    """

    bid_id: UUID
    document_id: UUID
    chunk_id: UUID
    vector: tuple[float, ...]
    file_name: str
    text: str
    page_number: int | None = None
    section_title: str | None = None
    chunk_type: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "bid_id", _as_uuid(self.bid_id))
        object.__setattr__(self, "document_id", _as_uuid(self.document_id))
        object.__setattr__(self, "chunk_id", _as_uuid(self.chunk_id))
        object.__setattr__(self, "vector", tuple(float(value) for value in self.vector))
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def point_id(self) -> str:
        return str(self.chunk_id)

    def to_payload(self) -> dict[str, Any]:
        """Serialize identifiers as strings for Qdrant's JSON payload."""
        payload = {
            "bid_id": str(self.bid_id),
            "document_id": str(self.document_id),
            "chunk_id": self.point_id,
            "file_name": self.file_name,
            "page_number": self.page_number,
            "section_title": self.section_title,
            "chunk_type": self.chunk_type,
            "text": self.text,
            "metadata": dict(self.metadata),
        }
        for key, value in payload["metadata"].items():
            if key not in payload:
                payload[key] = value
        return payload


@dataclass(frozen=True)
class VectorHit:
    """One semantic-search match."""

    point_id: str
    score: float
    payload: dict[str, Any]


@dataclass(frozen=True)
class IndexedPoint:
    """Stored point identity and payload used by consistency audits."""

    point_id: str
    payload: dict[str, Any]


class CollectionDimensionError(ValueError):
    """Configured EMBEDDING_DIMENSION does not match an existing collection."""


@dataclass(frozen=True)
class QdrantReadiness:
    """Connectivity and collection status. ``ready`` requires both."""

    connected: bool
    collection_ready: bool
    detail: str | None = None

    @property
    def ready(self) -> bool:
        return self.connected and self.collection_ready


class QdrantService:
    """Sync Qdrant client wrapper. Network calls happen only when a method runs."""

    def __init__(
        self,
        client: QdrantClient | None = None,
        settings_obj: Settings | None = None,
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self._settings = settings_obj or settings

    @property
    def client(self) -> QdrantClient:
        """Return the Qdrant client, creating it on first use."""
        if self._client is None:
            self._client = QdrantClient(
                url=self._settings.qdrant_url,
                api_key=self._settings.qdrant_api_key,
                timeout=max(1, round(self._settings.request_timeout_seconds)),
                prefer_grpc=False,
                check_compatibility=False,
            )
        return self._client

    def close(self) -> None:
        """Close a client this service created. Injected clients are left open."""
        if self._client is not None and self._owns_client:
            self._client.close()
        if self._owns_client:
            self._client = None

    def collection_exists(self) -> bool:
        return self.client.collection_exists(self._settings.qdrant_collection_name)

    def ensure_collection(self) -> None:
        """Create the configured collection when it is missing.

        An existing collection is left unchanged. A size mismatch raises
        ``CollectionDimensionError`` and the collection is not recreated.
        """
        name = self._settings.qdrant_collection_name
        if self.client.collection_exists(name):
            self._require_existing_collection_dimension()
            return
        vectors = VectorParams(
            size=self._settings.embedding_dimension,
            distance=Distance.COSINE,
        )
        try:
            self.client.create_collection(collection_name=name, vectors_config=vectors)
        except UnexpectedResponse as exc:
            if exc.status_code == 409 and self.client.collection_exists(name):
                self._require_existing_collection_dimension()
                return
            logger.error(
                "Failed to create Qdrant collection %s (%s)",
                name,
                type(exc).__name__,
            )
            raise

    def readiness(self) -> QdrantReadiness:
        """Check that Qdrant accepts requests and the collection exists.

        Connection failures are logged without a traceback and returned in
        ``detail``. This method does not create the collection.
        """
        name = self._settings.qdrant_collection_name
        try:
            self.client.get_collections()
            exists = self.client.collection_exists(name)
        except Exception as exc:
            detail = self._redact(str(exc))
            logger.error("Qdrant readiness check failed: %s", detail)
            return QdrantReadiness(
                connected=False,
                collection_ready=False,
                detail=detail,
            )
        if not exists:
            return QdrantReadiness(
                connected=True,
                collection_ready=False,
                detail=f"Collection {name} does not exist",
            )
        try:
            self._require_existing_collection_dimension()
        except CollectionDimensionError as exc:
            return QdrantReadiness(
                connected=True,
                collection_ready=False,
                detail=str(exc),
            )
        return QdrantReadiness(connected=True, collection_ready=True)

    def upsert(self, points: Sequence[ChunkPoint]) -> None:
        """Insert or replace chunk points. The point id is the chunk UUID."""
        if not points:
            return
        structs = [
            PointStruct(
                id=point.point_id,
                vector=self._validate_vector(point.vector),
                payload=point.to_payload(),
            )
            for point in points
        ]
        self._require_existing_collection_dimension()
        self.client.upsert(
            collection_name=self._settings.qdrant_collection_name,
            points=structs,
            wait=True,
        )

    def search(
        self,
        query_vector: Sequence[float],
        *,
        limit: int = 10,
        bid_id: UUID | str | None = None,
        document_id: UUID | str | None = None,
        document_ids: Sequence[UUID | str] = (),
        chunk_type: str | None = None,
        chunk_types: Sequence[str] = (),
        file_types: Sequence[str] = (),
        file_name: str | None = None,
    ) -> list[VectorHit]:
        """Search by a query embedding. Filters are combined with AND."""
        if limit < 1:
            raise ValueError("limit must be at least 1")
        vector = self._validate_vector(query_vector)
        self._require_existing_collection_dimension()
        query_filter = build_chunk_filter(
            bid_id=bid_id,
            document_id=document_id,
            document_ids=document_ids,
            chunk_type=chunk_type,
            chunk_types=chunk_types,
            file_types=file_types,
            file_name=file_name,
        )
        response = self.client.query_points(
            collection_name=self._settings.qdrant_collection_name,
            query=vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
        hits: list[VectorHit] = []
        for point in response.points:
            payload = dict(point.payload or {})
            hits.append(
                VectorHit(
                    point_id=str(point.id),
                    score=float(point.score),
                    payload=payload,
                )
            )
        return hits

    def retrieve(self, point_ids: Sequence[UUID | str]) -> list[IndexedPoint]:
        """Fetch known points without vectors. The collection is never modified."""
        if not point_ids:
            return []
        self._require_existing_collection_dimension()
        normalized = [str(_as_uuid(value)) for value in point_ids]
        records = self.client.retrieve(
            collection_name=self._settings.qdrant_collection_name,
            ids=normalized,
            with_payload=True,
            with_vectors=False,
        )
        return [
            IndexedPoint(
                point_id=str(record.id),
                payload=dict(record.payload or {}),
            )
            for record in records
        ]

    def _require_existing_collection_dimension(self) -> None:
        """Reject a vector write when an existing collection uses another size.

        The collection is left unchanged. Create a new collection, or set
        EMBEDDING_DIMENSION to the size the collection already stores.
        """
        name = self._settings.qdrant_collection_name
        if not self.client.collection_exists(name):
            return
        actual = _dense_vector_size(self.client.get_collection(name))
        expected = self._settings.embedding_dimension
        if actual != expected:
            raise CollectionDimensionError(
                f"Qdrant collection {name!r} stores vectors of size {actual}, "
                f"but EMBEDDING_DIMENSION is {expected}. "
                f"Set EMBEDDING_DIMENSION to {actual} to use this collection, "
                "or set QDRANT_COLLECTION_NAME to a new name and create that "
                f"collection at size {expected}. The existing collection is left unchanged."
            )

    def _validate_vector(self, vector: Sequence[float]) -> list[float]:
        values = [float(value) for value in vector]
        expected = self._settings.embedding_dimension
        if len(values) != expected:
            raise ValueError(
                f"Embedding dimension {len(values)} does not match "
                f"configured dimension {expected}"
            )
        if any(not math.isfinite(value) for value in values):
            raise ValueError("Embedding values must be finite numbers")
        return values

    def _redact(self, message: str) -> str:
        secret = self._settings.qdrant_api_key
        if secret:
            return message.replace(secret, "***")
        return message


def build_chunk_filter(
    *,
    bid_id: UUID | str | None = None,
    document_id: UUID | str | None = None,
    document_ids: Sequence[UUID | str] = (),
    chunk_type: str | None = None,
    chunk_types: Sequence[str] = (),
    file_types: Sequence[str] = (),
    file_name: str | None = None,
) -> Filter | None:
    """Build an optional AND filter. Omitted fields are not constrained."""
    conditions: list[FieldCondition] = []
    if bid_id is not None:
        conditions.append(_match("bid_id", str(_as_uuid(bid_id))))
    if document_id is not None:
        conditions.append(_match("document_id", str(_as_uuid(document_id))))
    normalized_documents = [str(_as_uuid(value)) for value in document_ids]
    if normalized_documents:
        conditions.append(_match_any("document_id", normalized_documents))
    if chunk_type is not None:
        conditions.append(_match("chunk_type", chunk_type))
    if chunk_types:
        conditions.append(_match_any("chunk_type", list(chunk_types)))
    if file_types:
        conditions.append(_match_any("file_type", list(file_types)))
    if file_name is not None:
        conditions.append(_match("file_name", file_name))
    if not conditions:
        return None
    return Filter(must=conditions)


_service: QdrantService | None = None


def get_qdrant_service() -> QdrantService:
    """Return the process-wide service. Does not connect until a method is called."""
    global _service
    if _service is None:
        _service = QdrantService()
    return _service


def close_qdrant_client() -> None:
    """Close the shared client if one was created."""
    global _service
    if _service is None:
        return
    _service.close()
    _service = None


def _dense_vector_size(info: object) -> int:
    """Read the single dense vector size from a collection description."""
    try:
        vectors = info.config.params.vectors  # type: ignore[attr-defined]
    except AttributeError as exc:
        raise CollectionDimensionError(
            "Qdrant collection configuration has no dense vector size. "
            "Set EMBEDDING_DIMENSION to the size stored by the collection."
        ) from exc
    if hasattr(vectors, "size"):
        return int(vectors.size)
    if isinstance(vectors, dict) and vectors:
        sizes = {int(params.size) for params in vectors.values()}
        if len(sizes) == 1:
            return sizes.pop()
        raise CollectionDimensionError(
            "Qdrant collection has named vectors of different sizes. "
            "This application expects one dense vector size."
        )
    raise CollectionDimensionError(
        "Qdrant collection configuration has no dense vector size. "
        "Set EMBEDDING_DIMENSION to the size stored by the collection."
    )


def _match(key: str, value: str) -> FieldCondition:
    return FieldCondition(key=key, match=MatchValue(value=value))


def _match_any(key: str, values: list[str]) -> FieldCondition:
    return FieldCondition(key=key, match=MatchAny(any=values))


def _as_uuid(value: UUID | str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("Identifier must be a UUID") from exc
