"""Load persisted chunks, embed them, and upsert Qdrant points.

PostgreSQL stays the source of truth. This service does not parse files,
rebuild chunks, or search the index. Unchanged text is embedded again and
upserted to the same point id. There is no stored index hash to compare.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.bid import Bid
from app.db.models.chunk import DocumentChunk
from app.db.models.document import Document
from app.ingestion.indexing.embedding_pipeline import EmbeddingPipeline, IndexChunk, TextEmbedder
from app.ingestion.indexing.qdrant_indexer import QdrantIndexer, VectorIndex
from app.schemas.common import ProcessingStatus
from app.schemas.indexing import IndexMessage, IndexingSummary
from app.services.qdrant_service import CollectionDimensionError

logger = logging.getLogger(__name__)


class IndexingNotFound(LookupError):
    """The document or bid is not in PostgreSQL."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class ChunkSource(Protocol):
    """Read chunks and update one document's ingestion status."""

    async def get_document(self, document_id: UUID) -> Document | None:
        """Return the document row, or None."""

    async def get_bid(self, bid_id: UUID) -> Bid | None:
        """Return the bid row, or None."""

    async def document_ids_for_bid(self, bid_id: UUID) -> list[UUID]:
        """Return document ids for a bid, in a stable order."""

    async def load_batch(self, document_id: UUID, *, offset: int, limit: int) -> list[IndexChunk]:
        """Return one page of chunks ordered by chunk index."""

    async def set_status(self, document_id: UUID, status: str) -> None:
        """Persist ingestion status for one document."""


@dataclass
class _Counts:
    requested: int = 0
    embedded: int = 0
    upserted: int = 0
    skipped: int = 0
    failed: int = 0
    warnings: list[IndexMessage] = field(default_factory=list)
    errors: list[IndexMessage] = field(default_factory=list)

    def summary(
        self,
        *,
        bid_id: UUID | None,
        document_id: UUID | None,
        status: str | None,
    ) -> IndexingSummary:
        return IndexingSummary(
            bid_id=bid_id,
            document_id=document_id,
            chunks_requested=self.requested,
            chunks_embedded=self.embedded,
            points_upserted=self.upserted,
            chunks_skipped=self.skipped,
            chunks_failed=self.failed,
            warnings=list(self.warnings),
            errors=list(self.errors),
            ingestion_status=status,
        )


class IndexingService:
    """Index one document or every document on a bid."""

    def __init__(
        self,
        source: ChunkSource,
        embedder: TextEmbedder,
        index: VectorIndex,
        *,
        batch_size: int,
        dimension: int,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be a positive integer")
        self._source = source
        self._embeddings = EmbeddingPipeline(embedder, dimension)
        self._index = QdrantIndexer(index)
        self._batch_size = batch_size

    async def index_document(self, document_id: UUID) -> IndexingSummary:
        """Embed and upsert every chunk of one document."""
        document = await self._source.get_document(document_id)
        if document is None:
            raise IndexingNotFound("document is not registered")
        ready, blocked = await self._prepare([document_id])
        if blocked is not None:
            return blocked.summary(bid_id=document.bid_id, document_id=document_id, status=ready)
        counts = await self._index_document(document_id)
        status = _status_for(counts)
        await self._source.set_status(document_id, status)
        logger.info(
            "Indexing document_id=%s requested=%s embedded=%s upserted=%s skipped=%s failed=%s",
            document_id,
            counts.requested,
            counts.embedded,
            counts.upserted,
            counts.skipped,
            counts.failed,
        )
        return counts.summary(bid_id=document.bid_id, document_id=document_id, status=status)

    async def index_bid(self, bid_id: UUID) -> IndexingSummary:
        """Index each document on a bid. Other bids are not loaded."""
        bid = await self._source.get_bid(bid_id)
        if bid is None:
            raise IndexingNotFound("bid is not registered")
        document_ids = await self._source.document_ids_for_bid(bid_id)
        total = _Counts()
        if not document_ids:
            total.warnings.append(
                IndexMessage(code="no_documents", message="bid has no documents", document_id=None)
            )
            return total.summary(bid_id=bid_id, document_id=None, status=None)
        ready, blocked = await self._prepare(document_ids)
        if blocked is not None:
            return blocked.summary(bid_id=bid_id, document_id=None, status=ready)
        for document_id in document_ids:
            counts = await self._index_document(document_id)
            status = _status_for(counts)
            await self._source.set_status(document_id, status)
            _merge(total, counts)
        logger.info(
            "Indexing bid_id=%s documents=%s requested=%s upserted=%s failed=%s",
            bid_id,
            len(document_ids),
            total.requested,
            total.upserted,
            total.failed,
        )
        return total.summary(bid_id=bid_id, document_id=None, status=None)

    async def _prepare(self, document_ids: Sequence[UUID]) -> tuple[str | None, _Counts | None]:
        for document_id in document_ids:
            await self._source.set_status(document_id, ProcessingStatus.PROCESSING.value)
        try:
            await asyncio.to_thread(self._index.prepare)
        except CollectionDimensionError as exc:
            for document_id in document_ids:
                await self._source.set_status(document_id, ProcessingStatus.FAILED.value)
            counts = _Counts()
            counts.errors.append(
                IndexMessage(code="collection_incompatible", message=str(exc), document_id=document_ids[0])
            )
            return ProcessingStatus.FAILED.value, counts
        except Exception:
            logger.error("Qdrant collection check failed")
            for document_id in document_ids:
                await self._source.set_status(document_id, ProcessingStatus.FAILED.value)
            counts = _Counts()
            counts.errors.append(
                IndexMessage(
                    code="qdrant_unavailable",
                    message="Qdrant collection check failed",
                    document_id=document_ids[0],
                )
            )
            return ProcessingStatus.FAILED.value, counts
        return None, None

    async def _index_document(self, document_id: UUID) -> _Counts:
        counts = _Counts()
        offset = 0
        seen = 0
        while True:
            batch = await self._source.load_batch(
                document_id,
                offset=offset,
                limit=self._batch_size,
            )
            if not batch:
                break
            seen += len(batch)
            await self._index_batch(batch, counts)
            offset += len(batch)
            if len(batch) < self._batch_size:
                break
        if seen == 0:
            counts.warnings.append(
                IndexMessage(
                    code="no_chunks",
                    message="document has no chunks to index",
                    document_id=document_id,
                )
            )
        return counts

    async def _index_batch(self, batch: Sequence[IndexChunk], counts: _Counts) -> None:
        counts.requested += len(batch)
        usable = [chunk for chunk in batch if chunk.text.strip()]
        for chunk in batch:
            if chunk.text.strip():
                continue
            counts.skipped += 1
            counts.warnings.append(
                IndexMessage(
                    code="empty_chunk",
                    message="chunk text is empty",
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                )
            )
        if not usable:
            return
        embedded = await self._embeddings.embed(usable)
        counts.errors.extend(embedded.errors)
        counts.failed += len(embedded.errors)
        if not embedded.embedded:
            return
        counts.embedded += len(embedded.embedded)
        try:
            await asyncio.to_thread(self._index.upsert, embedded.embedded)
        except Exception:
            logger.error(
                "Qdrant upsert failed document_id=%s chunks=%s",
                usable[0].document_id,
                len(embedded.embedded),
            )
            counts.failed += len(embedded.embedded)
            counts.errors.extend(
                IndexMessage(
                    code="qdrant_upsert",
                    message="Qdrant upsert failed",
                    chunk_id=item.chunk.chunk_id,
                    document_id=item.chunk.document_id,
                )
                for item in embedded.embedded
            )
            return
        counts.upserted += len(embedded.embedded)


class SqlChunkSource:
    """Load chunks with the request session. Status updates are committed."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_document(self, document_id: UUID) -> Document | None:
        return await self._session.get(Document, document_id)

    async def get_bid(self, bid_id: UUID) -> Bid | None:
        return await self._session.get(Bid, bid_id)

    async def document_ids_for_bid(self, bid_id: UUID) -> list[UUID]:
        statement = select(Document.id).where(Document.bid_id == bid_id).order_by(Document.id)
        return list(await self._session.scalars(statement))

    async def load_batch(self, document_id: UUID, *, offset: int, limit: int) -> list[IndexChunk]:
        statement = (
            select(DocumentChunk, Document)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index, DocumentChunk.id)
            .offset(offset)
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).all()
        return [_index_chunk(chunk, document) for chunk, document in rows]

    async def set_status(self, document_id: UUID, status: str) -> None:
        document = await self._session.get(Document, document_id)
        if document is None:
            return
        document.ingestion_status = status
        await self._session.commit()


def _index_chunk(chunk: DocumentChunk, document: Document) -> IndexChunk:
    metadata = chunk.chunk_metadata if isinstance(chunk.chunk_metadata, dict) else {}
    title = metadata.get("section_title")
    section_title = title if isinstance(title, str) else chunk.section_title
    table_id = metadata.get("table_id")
    anchor = metadata.get("anchor")
    return IndexChunk(
        chunk_id=chunk.id,
        document_id=document.id,
        bid_id=document.bid_id,
        chunk_index=chunk.chunk_index,
        text=chunk.text,
        content_hash=chunk.content_hash,
        page_number=chunk.page_number,
        section_title=section_title,
        chunk_type=chunk.chunk_type,
        file_name=document.file_name,
        file_type=document.document_type,
        document_date=document.document_date,
        addendum_number=document.addendum_number,
        heading_path=tuple(_strings(metadata.get("heading_path"))),
        table_id=table_id if isinstance(table_id, str) else None,
        anchor=anchor if isinstance(anchor, str) else None,
    )


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _status_for(counts: _Counts) -> str:
    if counts.failed == 0:
        return ProcessingStatus.INDEXED.value
    if counts.upserted > 0:
        return ProcessingStatus.PARTIAL.value
    return ProcessingStatus.FAILED.value


def _merge(total: _Counts, part: _Counts) -> None:
    total.requested += part.requested
    total.embedded += part.embedded
    total.upserted += part.upserted
    total.skipped += part.skipped
    total.failed += part.failed
    total.warnings.extend(part.warnings)
    total.errors.extend(part.errors)
