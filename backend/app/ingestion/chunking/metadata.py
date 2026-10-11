"""Attach registered source fields to a chunk. Missing values stay null."""

from __future__ import annotations

from app.ingestion.chunking.base import ChunkDocument


def chunk_metadata(
    source: ChunkDocument,
    *,
    chunk_id: str,
    chunk_index: int,
    chunk_type: str,
    content_hash: str,
    page_number: int | None,
    section_title: str | None,
    heading_path: list[str],
    table_id: str | None,
    anchor: str | None,
) -> dict[str, object]:
    """JSON-ready metadata. Folder names are not copied into bid fields."""
    return {
        "bid_id": None if source.bid_id is None else str(source.bid_id),
        "document_id": str(source.document_id),
        "chunk_id": chunk_id,
        "file_name": source.file_name,
        "file_type": source.file_type,
        "relative_path": source.relative_path,
        "chunk_type": chunk_type,
        "page_number": page_number,
        "section_title": section_title,
        "heading_path": list(heading_path),
        "anchor": anchor,
        "table_id": table_id,
        "chunk_index": chunk_index,
        "content_hash": content_hash,
        "document_date": None if source.document_date is None else source.document_date.isoformat(),
        "addendum_number": source.addendum_number,
    }
