"""Indexing endpoints. These routes do not authenticate callers.

Indexing is synchronous. The request embeds chunks and upserts Qdrant points
before it returns. There is no background queue. A large bid can take longer
than a short HTTP client timeout. Each provider call is limited to
``EMBEDDING_BATCH_SIZE`` chunks and ``REQUEST_TIMEOUT_SECONDS``.
"""

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_session_factory
from app.ingestion.indexing.indexing_service import IndexingNotFound, IndexingService, SqlChunkSource
from app.schemas.indexing import IndexingSummary
from app.services.embedding_service import get_embedding_service
from app.services.qdrant_service import get_qdrant_service

router = APIRouter(prefix=f"{settings.api_prefix}/v1/indexing", tags=["indexing"])


async def get_indexing_session() -> AsyncIterator[AsyncSession]:
    """Yield a session the indexing service commits as statuses change."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            if session.in_transaction():
                await session.rollback()
            raise
        else:
            if session.in_transaction():
                await session.rollback()


def get_indexing_service(
    session: AsyncSession = Depends(get_indexing_session),
) -> IndexingService:
    """Index through the shared embedding and Qdrant services."""
    return IndexingService(
        SqlChunkSource(session),
        get_embedding_service(),
        get_qdrant_service(),
        batch_size=settings.embedding_batch_size,
        dimension=settings.embedding_dimension,
    )


@router.post("/documents/{document_id}", response_model=IndexingSummary)
async def index_document(
    document_id: UUID,
    service: IndexingService = Depends(get_indexing_service),
) -> IndexingSummary:
    """Embed stored chunks for one document and upsert them."""
    try:
        return await service.index_document(document_id)
    except IndexingNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc


@router.post("/bids/{bid_id}", response_model=IndexingSummary)
async def index_bid(
    bid_id: UUID,
    service: IndexingService = Depends(get_indexing_service),
) -> IndexingSummary:
    """Embed stored chunks for every document on one bid."""
    try:
        return await service.index_bid(bid_id)
    except IndexingNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
