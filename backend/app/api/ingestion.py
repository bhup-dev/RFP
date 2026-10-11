"""Local ingestion endpoints. These routes do not authenticate callers.

Use them in local development or another trusted network. ``/scan`` registers
file metadata. ``/documents/{document_id}/parse`` reads a registered file.
``/documents/{document_id}/chunk`` stores text and table chunks. None of these
routes embed documents or write to Qdrant.
"""

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models.document import Document
from app.db.session import get_db_session
from app.ingestion.chunking.base import ChunkDocument
from app.ingestion.chunking.pipeline import ChunkingPipeline, SqlChunkRepository
from app.ingestion.exceptions import IngestionError
from app.ingestion.parse_stage import DocumentParseStage
from app.ingestion.parsers.base import RegisteredSource
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.registry import SqlDocumentRegistry
from app.schemas.chunking import ChunkingSummary
from app.schemas.ingestion import IngestionScanRequest, IngestionSummary
from app.schemas.parsing import ParsedDocument

router = APIRouter(prefix=f"{settings.api_prefix}/v1/ingestion", tags=["ingestion"])


def get_ingestion_pipeline(
    session: AsyncSession = Depends(get_db_session),
) -> IngestionPipeline:
    """Build a pipeline around the request's database transaction."""
    return IngestionPipeline(SqlDocumentRegistry(session), settings.ingestion_input_root)


@router.post("/scan", response_model=IngestionSummary)
async def scan_documents(
    body: IngestionScanRequest | None = None,
    pipeline: IngestionPipeline = Depends(get_ingestion_pipeline),
) -> IngestionSummary:
    """Register PDF and HTML files under the configured input root."""
    request = body or IngestionScanRequest()
    try:
        return await pipeline.scan(
            request.relative_directory,
            bid_identifier=request.bid_identifier,
        )
    except IngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def get_parse_stage() -> DocumentParseStage:
    """Parse files under the configured input root."""
    return DocumentParseStage(settings.ingestion_input_root)


async def get_registered_source(
    document_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> RegisteredSource:
    """Load registered metadata. The storage path stays relative."""
    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document is not registered")
    return RegisteredSource(
        document_id=document.id,
        relative_path=document.storage_path,
        file_name=document.file_name,
        document_type=document.document_type,
        file_hash=document.file_hash,
    )


@router.post("/documents/{document_id}/parse", response_model=ParsedDocument)
async def parse_registered_document(
    source: RegisteredSource = Depends(get_registered_source),
    stage: DocumentParseStage = Depends(get_parse_stage),
) -> ParsedDocument:
    """Extract text and tables from one registered PDF or HTML file."""
    return await asyncio.to_thread(stage.parse, source)


async def get_chunk_document(
    document_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> ChunkDocument:
    """Load the registered fields chunk metadata is allowed to copy."""
    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document is not registered")
    return ChunkDocument(
        document_id=document.id,
        bid_id=document.bid_id,
        file_name=document.file_name,
        file_type=document.document_type,
        relative_path=document.storage_path,
        document_date=document.document_date,
        addendum_number=document.addendum_number,
    )


def get_chunk_pipeline(
    session: AsyncSession = Depends(get_db_session),
) -> ChunkingPipeline:
    """Chunk into the request's database transaction."""
    return ChunkingPipeline.from_settings(SqlChunkRepository(session), settings)


@router.post("/documents/{document_id}/chunk", response_model=ChunkingSummary)
async def chunk_registered_document(
    record: ChunkDocument = Depends(get_chunk_document),
    stage: DocumentParseStage = Depends(get_parse_stage),
    pipeline: ChunkingPipeline = Depends(get_chunk_pipeline),
) -> ChunkingSummary:
    """Parse a registered file and store chunks. The document is not embedded."""
    source = RegisteredSource(
        document_id=record.document_id,
        relative_path=record.relative_path,
        file_name=record.file_name,
        document_type=record.file_type,
    )
    parsed = await asyncio.to_thread(stage.parse, source)
    return await pipeline.persist(record, parsed)
