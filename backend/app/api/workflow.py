"""Synchronous end-to-end processing endpoints for local/trusted use."""

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_session_factory
from app.ingestion.chunking.pipeline import ChunkingPipeline, SqlChunkRepository
from app.ingestion.end_to_end_pipeline import EndToEndPipeline, SqlWorkflowSource
from app.ingestion.exceptions import IngestionError
from app.ingestion.indexing.indexing_service import IndexingService, SqlChunkSource
from app.ingestion.parse_stage import DocumentParseStage
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.registry import SqlDocumentRegistry
from app.schemas.workflow import (
    EndToEndRequest,
    EndToEndSummary,
    WorkflowDocumentResult,
)
from app.services.embedding_service import get_embedding_service
from app.services.qdrant_service import get_qdrant_service

router = APIRouter(
    prefix=f"{settings.api_prefix}/v1/ingestion",
    tags=["ingestion"],
)


async def get_workflow_session() -> AsyncIterator[AsyncSession]:
    """Yield a session; workflow checkpoints own commit boundaries."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            if session.in_transaction():
                await session.rollback()
            raise
        else:
            if session.in_transaction():
                await session.rollback()


def get_end_to_end_pipeline(
    session: AsyncSession = Depends(get_workflow_session),
) -> EndToEndPipeline:
    """Wire existing stages without duplicating their implementations."""
    return EndToEndPipeline(
        IngestionPipeline(
            SqlDocumentRegistry(session),
            settings.ingestion_input_root,
        ),
        SqlWorkflowSource(session, settings.ingestion_input_root),
        DocumentParseStage(settings.ingestion_input_root),
        ChunkingPipeline.from_settings(SqlChunkRepository(session), settings),
        IndexingService(
            SqlChunkSource(session),
            get_embedding_service(),
            get_qdrant_service(),
            batch_size=settings.embedding_batch_size,
            dimension=settings.embedding_dimension,
        ),
    )


@router.post("/process", response_model=EndToEndSummary)
async def process_directory(
    body: EndToEndRequest | None = None,
    pipeline: EndToEndPipeline = Depends(get_end_to_end_pipeline),
) -> EndToEndSummary:
    """Register and process a bid folder or the configured root."""
    request = body or EndToEndRequest()
    try:
        return await pipeline.process_directory(
            request.relative_directory,
            bid_identifier=request.bid_identifier,
        )
    except IngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/documents/{document_id}/process",
    response_model=WorkflowDocumentResult,
)
async def process_registered_document(
    document_id: UUID,
    pipeline: EndToEndPipeline = Depends(get_end_to_end_pipeline),
) -> WorkflowDocumentResult:
    """Resume one registered document from chunks or indexing state."""
    result = await pipeline.process_registered_document(document_id)
    if not result.relative_path and result.errors == ["document is not registered"]:
        raise HTTPException(status_code=404, detail="document is not registered")
    return result
