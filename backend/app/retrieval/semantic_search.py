"""Query embedding and citation-safe Qdrant retrieval."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.ingestion.indexing.embedding_pipeline import TextEmbedder, vector_problem
from app.retrieval.base import BackendResult, RetrievalBackendError, SearchCandidate
from app.retrieval.query_service import PreparedQuery
from app.schemas.common import ensure_json_value
from app.services.provider_errors import ProviderConfigurationError, ProviderRequestError
from app.services.qdrant_service import VectorHit


class SemanticIndex(Protocol):
    """Filtered vector query supported by the existing Qdrant service."""

    def search(
        self,
        query_vector: Sequence[float],
        *,
        limit: int = 10,
        bid_id: UUID | str | None = None,
        document_ids: Sequence[UUID | str] = (),
        chunk_types: Sequence[str] = (),
        file_types: Sequence[str] = (),
        file_name: str | None = None,
    ) -> list[VectorHit]:
        """Return scored Qdrant hits."""


class SemanticSearch:
    """Embed a query once and search the current Qdrant collection."""

    def __init__(self, embedder: TextEmbedder, index: SemanticIndex, dimension: int) -> None:
        self._embedder = embedder
        self._index = index
        self._dimension = dimension

    async def search(self, query: PreparedQuery) -> BackendResult:
        try:
            vectors = await self._embedder.embed_texts([query.text])
        except (ProviderConfigurationError, ProviderRequestError, ValueError, TypeError) as exc:
            raise RetrievalBackendError("semantic", _embedding_message(exc)) from exc
        except (TimeoutError, OSError) as exc:
            raise RetrievalBackendError("semantic", "Query embedding is unavailable") from exc
        if len(vectors) != 1:
            raise RetrievalBackendError(
                "semantic",
                "Embedding provider returned an unexpected number of query vectors",
            )
        problem = vector_problem(vectors[0], self._dimension)
        if problem:
            raise RetrievalBackendError("semantic", problem)

        filters = query.filters
        try:
            hits = await asyncio.to_thread(
                self._index.search,
                vectors[0],
                limit=query.semantic_candidates,
                bid_id=filters.bid_id,
                document_ids=filters.document_ids,
                file_types=filters.file_types,
                chunk_types=filters.chunk_types,
                file_name=filters.file_name,
            )
        except (ValueError, TimeoutError, OSError) as exc:
            raise RetrievalBackendError("semantic", "Semantic index is unavailable") from exc
        except Exception as exc:
            raise RetrievalBackendError("semantic", "Semantic index is unavailable") from exc

        candidates: list[SearchCandidate] = []
        warnings: list[str] = []
        for hit in hits:
            candidate = _candidate(hit)
            if candidate is None:
                warnings.append(f"Skipped malformed semantic result {hit.point_id}")
                continue
            candidates.append(candidate)
        return BackendResult(candidates=tuple(candidates), warnings=tuple(warnings))


def _candidate(hit: VectorHit) -> SearchCandidate | None:
    payload = hit.payload
    try:
        bid_id = UUID(str(payload["bid_id"]))
        document_id = UUID(str(payload["document_id"]))
        chunk_id = UUID(str(payload["chunk_id"]))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if str(chunk_id) != str(hit.point_id):
        return None
    file_name = payload.get("file_name")
    text = payload.get("text")
    if not isinstance(file_name, str) or not file_name.strip():
        return None
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        score = float(hit.score)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(score):
        return None
    page_number = payload.get("page_number")
    if page_number is not None and (
        isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1
    ):
        return None
    section_title = payload.get("section_title")
    chunk_type = payload.get("chunk_type")
    if section_title is not None and not isinstance(section_title, str):
        return None
    if chunk_type is not None and not isinstance(chunk_type, str):
        return None
    metadata = _safe_metadata(payload)
    if metadata is None:
        return None
    return SearchCandidate(
        bid_id=bid_id,
        document_id=document_id,
        chunk_id=chunk_id,
        file_name=file_name,
        text=text,
        page_number=page_number,
        section_title=section_title,
        chunk_type=chunk_type,
        semantic_score=score,
        metadata=metadata,
    )


def _safe_metadata(payload: dict[str, object]) -> dict[str, object] | None:
    metadata = payload.get("metadata")
    merged = dict(metadata) if isinstance(metadata, dict) else {}
    core = {
        "bid_id",
        "document_id",
        "chunk_id",
        "file_name",
        "text",
        "page_number",
        "section_title",
        "chunk_type",
        "metadata",
    }
    merged.update({key: value for key, value in payload.items() if key not in core})
    try:
        checked = ensure_json_value(merged)
    except ValueError:
        return None
    return checked if isinstance(checked, dict) else None


def _embedding_message(exc: Exception) -> str:
    if isinstance(exc, ProviderConfigurationError | ProviderRequestError):
        return str(exc)
    return "Query embedding failed"
