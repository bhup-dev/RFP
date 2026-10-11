"""Shared chunk identity, hashing, and draft records."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from uuid import UUID, uuid5

from app.schemas.chunking import ChunkMessage

# Stable namespace so the same document, index, and content hash always map to one id.
CHUNK_NAMESPACE = uuid5(UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8"), "rfp-intelligence-chunk")
SECTION_TITLE_LIMIT = 512


@dataclass(frozen=True)
class ChunkDocument:
    """Registered document fields used to enrich chunks.

    ``bid_id`` is the database id. Folder names are not treated as solicitation numbers.
    """

    document_id: UUID
    bid_id: UUID | None
    file_name: str
    file_type: str | None
    relative_path: str
    document_date: date | None = None
    addendum_number: int | None = None


@dataclass(frozen=True)
class ChunkDraft:
    """One chunk ready to store. The text is the original source text, not a summary."""

    id: UUID
    document_id: UUID
    chunk_index: int
    chunk_type: str
    text: str
    page_number: int | None
    section_title: str | None
    content_hash: str
    metadata: dict[str, object]
    warnings: tuple[ChunkMessage, ...] = field(default_factory=tuple)


def content_hash(text: str) -> str:
    """SHA-256 of the exact chunk text that will be stored."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_identity(document_id: UUID, chunk_index: int, digest: str) -> UUID:
    """Predictable id. Document id, index, and content hash are all part of the name."""
    return uuid5(CHUNK_NAMESPACE, f"{document_id}:{chunk_index}:{digest}")


def column_section_title(title: str | None) -> tuple[str | None, ChunkMessage | None]:
    """Keep a long title in metadata. The 512-character column is left empty instead of cut."""
    if title is None or len(title) <= SECTION_TITLE_LIMIT:
        return title, None
    return None, ChunkMessage(
        code="section_title_too_long",
        message="section title exceeds the database column and was kept in metadata",
    )


def same_chunk_set(existing: list[object], drafts: list[ChunkDraft]) -> bool:
    """True when stored rows already match the drafts in order and content."""
    if len(existing) != len(drafts):
        return False
    for current, draft in zip(existing, drafts, strict=True):
        if (
            current.chunk_index != draft.chunk_index
            or current.content_hash != draft.content_hash
            or current.chunk_type != draft.chunk_type
            or current.text != draft.text
        ):
            return False
    return True
