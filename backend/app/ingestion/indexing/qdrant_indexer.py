"""Turn persisted chunks into Qdrant points and upsert them.

Point ids are the PostgreSQL chunk ids. A repeated upsert updates that point.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.ingestion.indexing.embedding_pipeline import EmbeddedChunk
from app.services.qdrant_service import ChunkPoint, CollectionDimensionError


class VectorIndex(Protocol):
    """The existing Qdrant service methods this indexer uses."""

    def ensure_collection(self) -> None:
        """Create the configured collection or reject a size mismatch."""

    def upsert(self, points: Sequence[ChunkPoint]) -> None:
        """Insert or replace points in the configured collection."""


class QdrantIndexer:
    """Build citation payloads and write them through the shared Qdrant service."""

    def __init__(self, index: VectorIndex) -> None:
        self._index = index

    def prepare(self) -> None:
        """Check the collection once. An existing collection is not recreated."""
        self._index.ensure_collection()

    def upsert(self, embedded: Sequence[EmbeddedChunk]) -> None:
        """Upsert one batch. The caller decides what a failure means."""
        if not embedded:
            return
        self._index.upsert([chunk_point(item) for item in embedded])


def chunk_point(item: EmbeddedChunk) -> ChunkPoint:
    """Copy source identifiers into a point. Missing official fields stay null."""
    chunk = item.chunk
    metadata = {
        "file_type": chunk.file_type,
        "heading_path": list(chunk.heading_path),
        "chunk_index": chunk.chunk_index,
        "table_id": chunk.table_id,
        "content_hash": chunk.content_hash,
        "document_date": None if chunk.document_date is None else chunk.document_date.isoformat(),
        "addendum_number": chunk.addendum_number,
        "anchor": chunk.anchor,
    }
    return ChunkPoint(
        bid_id=chunk.bid_id,
        document_id=chunk.document_id,
        chunk_id=chunk.chunk_id,
        vector=item.vector,
        file_name=chunk.file_name,
        text=chunk.text,
        page_number=chunk.page_number,
        section_title=chunk.section_title,
        chunk_type=chunk.chunk_type,
        metadata=metadata,
    )


__all__ = ["CollectionDimensionError", "QdrantIndexer", "VectorIndex", "chunk_point"]
