"""Hybrid search endpoint. Retrieval logic lives in reusable services."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db_session
from app.retrieval.base import RetrievalUnavailable
from app.retrieval.keyword_search import KeywordSearch
from app.retrieval.citations import CitationResolver
from app.retrieval.query_rewriter import QueryRewriter
from app.retrieval.reranker import Reranker, build_reranker_provider
from app.retrieval.retrieval_service import RetrievalService
from app.retrieval.semantic_search import SemanticSearch
from app.schemas.retrieval import RetrievalRequest, RetrievalResponse
from app.services.embedding_service import get_embedding_service
from app.services.llm_service import get_llm_service
from app.services.qdrant_service import get_qdrant_service

router = APIRouter(prefix=f"{settings.api_prefix}/v1", tags=["search"])


def get_retrieval_service(
    session: AsyncSession = Depends(get_db_session),
) -> RetrievalService:
    """Use the shared embedding, Qdrant, settings, and database session."""
    return RetrievalService(
        SemanticSearch(
            get_embedding_service(),
            get_qdrant_service(),
            settings.embedding_dimension,
        ),
        KeywordSearch(session),
        rrf_constant=settings.search_rrf_constant,
        allow_degraded=settings.search_allow_degraded,
        rewriter=QueryRewriter(
            get_llm_service(),
            enabled=settings.query_rewriting_enabled,
            max_variants=settings.query_rewrite_max_variants,
            timeout_seconds=settings.query_rewrite_timeout_seconds,
        ),
        reranker=Reranker(
            build_reranker_provider(
                settings.reranker_provider,
                settings.reranker_model,
            ),
            enabled=settings.reranking_enabled,
            candidate_limit=settings.rerank_candidate_limit,
            batch_size=settings.rerank_batch_size,
            timeout_seconds=settings.rerank_timeout_seconds,
            max_input_characters=settings.rerank_max_input_characters,
        ),
        citation_validator=CitationResolver(session),
    )


@router.post("/search", response_model=RetrievalResponse)
async def search(
    request: RetrievalRequest,
    service: RetrievalService = Depends(get_retrieval_service),
) -> RetrievalResponse:
    """Search chunks with optional bounded rewriting and cross-encoder reranking."""
    try:
        return await service.search(request)
    except RetrievalUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "retrieval_unavailable",
                "message": "No retrieval backend is available",
                "errors": exc.errors,
            },
        ) from exc
