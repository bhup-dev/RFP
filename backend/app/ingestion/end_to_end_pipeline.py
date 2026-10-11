"""Coordinate existing registration, parsing, chunking, and indexing stages."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.chunk import DocumentChunk
from app.db.models.document import Document
from app.ingestion.chunking.base import ChunkDocument
from app.ingestion.exceptions import IngestionError
from app.ingestion.file_utils import file_sha256, resolve_registered_file
from app.ingestion.parsers.base import RegisteredSource
from app.schemas.chunking import ChunkingSummary
from app.schemas.indexing import IndexingSummary
from app.schemas.ingestion import IngestionOutcome, IngestionSummary
from app.schemas.parsing import ParseStatus, ParsedDocument
from app.schemas.workflow import EndToEndSummary, WorkflowDocumentResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkflowDocument:
    """Registered fields required by downstream stages."""

    document_id: UUID
    bid_id: UUID
    relative_path: str
    file_name: str
    file_type: str | None
    file_hash: str | None
    ingestion_status: str
    chunk_count: int
    chunk_document: ChunkDocument

    def parser_source(self) -> RegisteredSource:
        return RegisteredSource(
            document_id=self.document_id,
            relative_path=self.relative_path,
            file_name=self.file_name,
            document_type=self.file_type,
            file_hash=self.file_hash,
        )


class ScanStage(Protocol):
    async def scan(
        self,
        relative_directory: str | None = None,
        *,
        bid_identifier: str | None = None,
    ) -> IngestionSummary:
        """Register discovered files."""


class WorkflowSource(Protocol):
    async def get(self, document_id: UUID) -> WorkflowDocument | None:
        """Load one registered document and current chunk count."""

    def source_state(self, document: WorkflowDocument) -> tuple[bool, str | None]:
        """Verify source bytes still match the registered content hash."""

    async def checkpoint(self) -> None:
        """Commit registration before document-level processing begins."""


class ParserStage(Protocol):
    def parse(self, source: RegisteredSource) -> ParsedDocument:
        """Parse one registered file."""


class ChunkStage(Protocol):
    async def persist(
        self,
        source: ChunkDocument,
        parsed: ParsedDocument,
    ) -> ChunkingSummary:
        """Persist deterministic chunks."""


class IndexStage(Protocol):
    async def index_document(self, document_id: UUID) -> IndexingSummary:
        """Embed persisted chunks and upsert stable Qdrant points."""


class EndToEndPipeline:
    """Small coordinator around independently testable existing stages."""

    def __init__(
        self,
        scanner: ScanStage,
        source: WorkflowSource,
        parser: ParserStage,
        chunker: ChunkStage,
        indexer: IndexStage,
    ) -> None:
        self._scanner = scanner
        self._source = source
        self._parser = parser
        self._chunker = chunker
        self._indexer = indexer

    async def process_directory(
        self,
        relative_directory: str | None = None,
        *,
        bid_identifier: str | None = None,
    ) -> EndToEndSummary:
        """Register a bid folder or root and continue eligible documents."""
        registration = await self._scanner.scan(
            relative_directory,
            bid_identifier=bid_identifier,
        )
        await self._source.checkpoint()
        results: list[WorkflowDocumentResult] = []
        for item in registration.results:
            if (
                item.document_id is not None
                and item.outcome
                in {IngestionOutcome.REGISTERED, IngestionOutcome.DUPLICATE}
            ):
                result = await self.process_registered_document(item.document_id)
                result = result.model_copy(
                    update={"registration_outcome": item.outcome.value}
                )
            else:
                error = item.message or f"registration outcome: {item.outcome.value}"
                result = WorkflowDocumentResult(
                    document_id=item.document_id,
                    relative_path=item.relative_path,
                    registration_outcome=item.outcome.value,
                    errors=[error],
                )
            results.append(result)
        return _summary(registration.files_discovered, results)

    async def process_registered_document(
        self,
        document_id: UUID,
    ) -> WorkflowDocumentResult:
        """Resume a registered document from its safest known stage."""
        document = await self._source.get(document_id)
        if document is None:
            return WorkflowDocumentResult(
                document_id=document_id,
                relative_path="",
                errors=["document is not registered"],
            )
        current, problem = await asyncio.to_thread(
            self._source.source_state,
            document,
        )
        if not current:
            return _result(
                document,
                errors=[problem or "registered source is unavailable"],
            )

        if document.chunk_count > 0 and document.ingestion_status == "indexed":
            return _result(
                document,
                parsing="skipped",
                chunking="skipped",
                indexing="skipped",
                chunks_skipped=document.chunk_count,
                resumed_from="indexed",
                completed=True,
                warnings=[
                    "PostgreSQL indexed status was trusted; cross-system consistency "
                    "can be checked with the citation and point audit"
                ],
            )

        if document.chunk_count > 0:
            return await self._index_existing(document)

        parsed = await asyncio.to_thread(
            self._parser.parse,
            document.parser_source(),
        )
        if parsed.status == ParseStatus.FAILED:
            return _result(
                document,
                parsing="failed",
                errors=[message.message for message in parsed.errors]
                or ["document parsing failed"],
                warnings=[message.message for message in parsed.warnings],
            )
        chunked = await self._chunker.persist(document.chunk_document, parsed)
        if chunked.errors:
            return _result(
                document,
                parsing="completed",
                chunking="failed",
                errors=[message.message for message in chunked.errors],
                warnings=[message.message for message in chunked.warnings],
            )
        available = chunked.chunks_created + chunked.chunks_skipped
        if available == 0:
            return _result(
                document,
                parsing="completed",
                chunking="completed",
                errors=["document produced no chunks to index"],
                warnings=[message.message for message in chunked.warnings],
            )
        indexed = await self._indexer.index_document(document.document_id)
        return _index_result(
            document,
            indexed,
            parsing="completed",
            chunking="completed",
            chunks_created=chunked.chunks_created,
            chunks_skipped=chunked.chunks_skipped,
            warnings=[message.message for message in chunked.warnings],
        )

    async def _index_existing(
        self,
        document: WorkflowDocument,
    ) -> WorkflowDocumentResult:
        indexed = await self._indexer.index_document(document.document_id)
        return _index_result(
            document,
            indexed,
            parsing="skipped",
            chunking="skipped",
            chunks_skipped=document.chunk_count,
            resumed_from="chunks",
        )


class SqlWorkflowSource:
    """Read workflow state without exposing a storage path publicly."""

    def __init__(self, session: AsyncSession, input_root: Path) -> None:
        self._session = session
        self._input_root = input_root

    async def get(self, document_id: UUID) -> WorkflowDocument | None:
        document = await self._session.get(Document, document_id)
        if document is None:
            return None
        count = int(
            await self._session.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(DocumentChunk.document_id == document_id)
            )
            or 0
        )
        return WorkflowDocument(
            document_id=document.id,
            bid_id=document.bid_id,
            relative_path=document.storage_path,
            file_name=document.file_name,
            file_type=document.document_type,
            file_hash=document.file_hash,
            ingestion_status=document.ingestion_status,
            chunk_count=count,
            chunk_document=ChunkDocument(
                document_id=document.id,
                bid_id=document.bid_id,
                file_name=document.file_name,
                file_type=document.document_type,
                relative_path=document.storage_path,
                document_date=document.document_date,
                addendum_number=document.addendum_number,
            ),
        )

    def source_state(
        self,
        document: WorkflowDocument,
    ) -> tuple[bool, str | None]:
        try:
            path = resolve_registered_file(
                self._input_root,
                document.relative_path,
            )
            if document.file_hash and file_sha256(path) != document.file_hash:
                return (
                    False,
                    "source content differs from the registered hash; register the "
                    "changed source before processing",
                )
        except IngestionError as exc:
            return False, str(exc)
        return True, None

    async def checkpoint(self) -> None:
        await self._session.commit()


def _index_result(
    document: WorkflowDocument,
    indexed: IndexingSummary,
    *,
    parsing: str,
    chunking: str,
    chunks_created: int = 0,
    chunks_skipped: int = 0,
    resumed_from: str | None = None,
    warnings: list[str] | None = None,
) -> WorkflowDocumentResult:
    errors = [message.message for message in indexed.errors]
    all_warnings = list(warnings or [])
    all_warnings.extend(message.message for message in indexed.warnings)
    completed = indexed.ingestion_status == "indexed" and not errors
    return _result(
        document,
        parsing=parsing,
        chunking=chunking,
        indexing="completed" if completed else "failed",
        chunks_created=chunks_created,
        chunks_skipped=chunks_skipped,
        points_upserted=indexed.points_upserted,
        resumed_from=resumed_from,
        completed=completed,
        warnings=all_warnings,
        errors=errors,
    )


def _result(
    document: WorkflowDocument,
    **updates: object,
) -> WorkflowDocumentResult:
    return WorkflowDocumentResult(
        document_id=document.document_id,
        bid_id=document.bid_id,
        relative_path=document.relative_path,
        **updates,
    )


def _summary(
    discovered: int,
    results: list[WorkflowDocumentResult],
) -> EndToEndSummary:
    return EndToEndSummary(
        files_discovered=discovered,
        documents_completed=sum(item.completed for item in results),
        documents_failed=sum(bool(item.errors) for item in results),
        documents_skipped=sum(
            item.completed and item.indexing == "skipped" for item in results
        ),
        chunks_created=sum(item.chunks_created for item in results),
        points_upserted=sum(item.points_upserted for item in results),
        results=results,
    )
