"""Synchronous addendum analysis endpoint."""

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
from app.schemas.addendum import AddendumAnalysisResponse
from app.services.addendum_analysis_service import (
    AddendumAnalysisService,
    AddendumAnalysisUnavailable,
    AddendumBidNotFound,
    AddendumBidNotReady,
    InvalidAddendumResponse,
    NoAddendaAvailable,
    SqlAddendumStore,
)
from app.services.embedding_service import get_embedding_service
from app.services.llm_service import get_llm_service
from app.services.qdrant_service import get_qdrant_service

router = APIRouter(prefix=f"{settings.api_prefix}/v1/bids", tags=["addendum-analysis"])


async def get_addendum_session() -> AsyncIterator[AsyncSession]:
    """Yield a session; the analysis store commits lifecycle transitions."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            if session.in_transaction():
                await session.rollback()


def get_addendum_analysis_service(
    session: AsyncSession = Depends(get_addendum_session),
) -> AddendumAnalysisService:
    """Reuse baseline hybrid retrieval and the configured LLM provider."""
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
    return AddendumAnalysisService(
        SqlAddendumStore(session),
        retrieval,
        get_llm_service(),
        model_name=settings.llm_model,
        max_chunks_per_document=settings.addendum_max_chunks_per_document,
        related_top_k=settings.addendum_related_top_k,
        max_evidence_chunks=settings.addendum_max_evidence_chunks,
        max_output_tokens=settings.addendum_max_output_tokens,
    )


@router.post("/{bid_id}/analyze-addenda", response_model=AddendumAnalysisResponse)
async def analyze_addenda(
    bid_id: UUID,
    service: AddendumAnalysisService = Depends(get_addendum_analysis_service),
) -> AddendumAnalysisResponse:
    """Analyze explicitly classified amendment documents without replacing sources."""
    try:
        return await service.analyze(bid_id)
    except AddendumBidNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (AddendumBidNotReady, NoAddendaAvailable) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidAddendumResponse as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "invalid_addendum_response", "message": str(exc)},
        ) from exc
    except AddendumAnalysisUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "addendum_analysis_unavailable", "message": str(exc)},
        ) from exc
