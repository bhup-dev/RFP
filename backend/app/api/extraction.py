"""Structured extraction endpoint for ingested bids."""

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_session_factory
from app.retrieval.citations import CitationResolver
from app.retrieval.keyword_search import KeywordSearch
from app.retrieval.retrieval_service import RetrievalService
from app.retrieval.semantic_search import SemanticSearch
from app.schemas.extraction import ExtractionResponse
from app.services.embedding_service import get_embedding_service
from app.services.extraction_service import (
    BidNotFound,
    BidNotReady,
    ExtractionService,
    ExtractionUnavailable,
    InvalidExtractionResponse,
    SqlExtractionStore,
)
from app.services.llm_service import get_llm_service
from app.services.qdrant_service import get_qdrant_service

router = APIRouter(prefix=f"{settings.api_prefix}/v1/bids", tags=["extraction"])


async def get_extraction_session() -> AsyncIterator[AsyncSession]:
    """Yield a session; the extraction store commits each durable lifecycle step."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            if session.in_transaction():
                await session.rollback()


def get_extraction_service(
    session: AsyncSession = Depends(get_extraction_session),
) -> ExtractionService:
    """Build extraction from existing retrieval, provider, and persistence services."""
    retrieval = RetrievalService(
        SemanticSearch(
            get_embedding_service(),
            get_qdrant_service(),
            settings.embedding_dimension,
        ),
        KeywordSearch(session),
        rrf_constant=settings.search_rrf_constant,
        allow_degraded=settings.search_allow_degraded,
        citation_validator=CitationResolver(session),
    )
    return ExtractionService(
        SqlExtractionStore(session),
        retrieval,
        get_llm_service(),
        model_name=settings.llm_model,
        query_top_k=settings.extraction_query_top_k,
        max_evidence_chunks=settings.extraction_max_evidence_chunks,
        max_output_tokens=settings.extraction_max_output_tokens,
    )


@router.post("/{bid_id}/extract", response_model=ExtractionResponse)
async def extract_bid(
    bid_id: UUID,
    service: ExtractionService = Depends(get_extraction_service),
) -> ExtractionResponse:
    """Extract all assignment fields from validated chunks in one bid."""
    try:
        return await service.extract(bid_id)
    except BidNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BidNotReady as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidExtractionResponse as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "invalid_extraction_response",
                "message": str(exc),
            },
        ) from exc
    except ExtractionUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "extraction_unavailable",
                "message": str(exc),
            },
        ) from exc
