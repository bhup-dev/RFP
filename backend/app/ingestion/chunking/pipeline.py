"""Build chunks from parser output and replace one document's chunk set."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models.chunk import DocumentChunk
from app.ingestion.chunking.base import (
    ChunkDocument,
    ChunkDraft,
    chunk_identity,
    column_section_title,
    content_hash,
    same_chunk_set,
)
from app.ingestion.chunking.metadata import chunk_metadata
from app.ingestion.chunking.table_chunker import TableChunker
from app.ingestion.chunking.text_chunker import TextChunker
from app.schemas.chunking import ChunkingSummary, ChunkMessage
from app.schemas.parsing import ParsedDocument, ParsedSection, ParseStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SaveCounts:
    """Rows inserted, or rows left untouched because they already matched."""

    created: int
    skipped: int


class ChunkStore(Protocol):
    """Persistence for one document's chunks."""

    async def save(self, document_id: UUID, drafts: list[ChunkDraft]) -> SaveCounts:
        """Insert drafts, or leave the stored rows when they already match."""


class ChunkingPipeline:
    """Create text and table chunks. This does not embed them or update ingestion status."""

    def __init__(
        self,
        store: ChunkStore,
        *,
        chunk_size: int,
        chunk_overlap: int,
        table_chunk_max_rows: int,
        chunk_min_text_length: int,
    ) -> None:
        self._store = store
        self._text = TextChunker(chunk_size, chunk_overlap, chunk_min_text_length)
        self._tables = TableChunker(table_chunk_max_rows)

    @classmethod
    def from_settings(cls, store: ChunkStore, settings: Settings) -> ChunkingPipeline:
        return cls(
            store,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            table_chunk_max_rows=settings.table_chunk_max_rows,
            chunk_min_text_length=settings.chunk_min_text_length,
        )

    def build(self, source: ChunkDocument, parsed: ParsedDocument) -> tuple[list[ChunkDraft], list[ChunkMessage]]:
        """Turn parser sections into ordered drafts. Sections are not merged."""
        drafts: list[ChunkDraft] = []
        warnings: list[ChunkMessage] = [
            ChunkMessage(code=item.code, message=item.message, page_number=item.page_number)
            for item in parsed.warnings
        ]
        for section, heading_path in zip(parsed.sections, _heading_paths(parsed.sections), strict=True):
            drafts.extend(self._text_drafts(source, section, heading_path, len(drafts)))
            drafts.extend(self._table_drafts(source, section, heading_path, len(drafts)))
        for draft in drafts:
            warnings.extend(draft.warnings)
        return drafts, _unique_messages(warnings)

    async def persist(self, source: ChunkDocument, parsed: ParsedDocument) -> ChunkingSummary:
        """Store chunks for this document. A failed parse does not delete existing chunks."""
        if parsed.status == ParseStatus.FAILED or (parsed.errors and not _has_content(parsed)):
            errors = [
                ChunkMessage(code=item.code, message=item.message, page_number=item.page_number)
                for item in parsed.errors
            ] or [
                ChunkMessage(code="unreadable_parse", message="parser output cannot be chunked")
            ]
            logger.error(
                "Chunking skipped document_id=%s path=%s errors=%s",
                source.document_id,
                source.relative_path,
                len(errors),
            )
            return ChunkingSummary(document_id=source.document_id, errors=errors)

        drafts, warnings = self.build(source, parsed)
        counts = await self._store.save(source.document_id, drafts)
        text_created = 0 if counts.created == 0 else sum(1 for draft in drafts if draft.chunk_type == "text")
        table_created = 0 if counts.created == 0 else sum(1 for draft in drafts if draft.chunk_type == "table")
        logger.info(
            "Chunking complete document_id=%s path=%s created=%s skipped=%s warnings=%s",
            source.document_id,
            source.relative_path,
            counts.created,
            counts.skipped,
            len(warnings),
        )
        return ChunkingSummary(
            document_id=source.document_id,
            chunks_created=counts.created,
            text_chunks_created=text_created,
            table_chunks_created=table_created,
            chunks_skipped=counts.skipped,
            warnings=warnings,
        )

    def _text_drafts(
        self,
        source: ChunkDocument,
        section: ParsedSection,
        heading_path: list[str],
        index_start: int,
    ) -> list[ChunkDraft]:
        pieces = self._text.split(section.text)
        drafts: list[ChunkDraft] = []
        for offset, piece in enumerate(pieces):
            drafts.append(
                _draft(
                    source,
                    index=index_start + offset,
                    chunk_type="text",
                    text=piece,
                    page_number=section.page_number,
                    section_title=section.section_title,
                    heading_path=heading_path,
                    table_id=None,
                    anchor=section.anchor,
                    extra_warnings=(),
                )
            )
        return drafts

    def _table_drafts(
        self,
        source: ChunkDocument,
        section: ParsedSection,
        heading_path: list[str],
        index_start: int,
    ) -> list[ChunkDraft]:
        drafts: list[ChunkDraft] = []
        for table in section.tables:
            for piece in self._tables.split(table):
                drafts.append(
                    _draft(
                        source,
                        index=index_start + len(drafts),
                        chunk_type="table",
                        text=piece.text,
                        page_number=table.page_number if table.page_number is not None else section.page_number,
                        section_title=table.section_title or section.section_title,
                        heading_path=heading_path,
                        table_id=piece.table_id,
                        anchor=table.anchor or section.anchor,
                        extra_warnings=piece.warnings,
                    )
                )
        return drafts


class SqlChunkRepository:
    """Replace one document's chunks inside a savepoint.

    An unchanged set is left in place. A changed set is deleted and inserted
    together, so a flush failure restores the previous chunks. Other documents
    are outside the delete filter. Embeddings are not written.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, document_id: UUID, drafts: list[ChunkDraft]) -> SaveCounts:
        async with self._session.begin_nested():
            existing = list(
                await self._session.scalars(
                    select(DocumentChunk)
                    .where(DocumentChunk.document_id == document_id)
                    .order_by(DocumentChunk.chunk_index)
                )
            )
            if same_chunk_set(existing, drafts):
                return SaveCounts(created=0, skipped=len(existing))
            await self._session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
            for draft in drafts:
                self._session.add(_row(draft))
            await self._session.flush()
        return SaveCounts(created=len(drafts), skipped=0)


def _draft(
    source: ChunkDocument,
    *,
    index: int,
    chunk_type: str,
    text: str,
    page_number: int | None,
    section_title: str | None,
    heading_path: list[str],
    table_id: str | None,
    anchor: str | None,
    extra_warnings: tuple[ChunkMessage, ...],
) -> ChunkDraft:
    digest = content_hash(text)
    identity = chunk_identity(source.document_id, index, digest)
    stored_title, title_warning = column_section_title(section_title)
    warnings = list(extra_warnings)
    if title_warning is not None:
        warnings.append(title_warning)
    return ChunkDraft(
        id=identity,
        document_id=source.document_id,
        chunk_index=index,
        chunk_type=chunk_type,
        text=text,
        page_number=page_number,
        section_title=stored_title,
        content_hash=digest,
        metadata=chunk_metadata(
            source,
            chunk_id=str(identity),
            chunk_index=index,
            chunk_type=chunk_type,
            content_hash=digest,
            page_number=page_number,
            section_title=section_title,
            heading_path=heading_path,
            table_id=table_id,
            anchor=anchor,
        ),
        warnings=tuple(warnings),
    )


def _heading_paths(sections: list[ParsedSection]) -> list[list[str]]:
    stack: list[tuple[int, str]] = []
    paths: list[list[str]] = []
    for section in sections:
        if section.heading_level and section.section_title:
            while stack and stack[-1][0] >= section.heading_level:
                stack.pop()
            stack.append((section.heading_level, section.section_title))
            paths.append([title for _level, title in stack])
        elif section.section_title:
            paths.append([section.section_title])
        else:
            paths.append([])
    return paths


def _has_content(parsed: ParsedDocument) -> bool:
    return any(section.text.strip() or section.tables for section in parsed.sections)


def _unique_messages(messages: list[ChunkMessage]) -> list[ChunkMessage]:
    seen: set[tuple[str, str, int | None, str | None]] = set()
    unique: list[ChunkMessage] = []
    for message in messages:
        key = (message.code, message.message, message.page_number, message.table_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(message)
    return unique


def _row(draft: ChunkDraft) -> DocumentChunk:
    return DocumentChunk(
        id=draft.id,
        document_id=draft.document_id,
        chunk_index=draft.chunk_index,
        page_number=draft.page_number,
        section_title=draft.section_title,
        chunk_type=draft.chunk_type,
        text=draft.text,
        content_hash=draft.content_hash,
        chunk_metadata=draft.metadata,
    )
