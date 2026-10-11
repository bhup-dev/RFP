"""Prepare one validated hybrid-search request."""

from __future__ import annotations

from dataclasses import dataclass

from app.retrieval.filters import SearchFilters, request_filters
from app.schemas.retrieval import RetrievalRequest


@dataclass(frozen=True)
class PreparedQuery:
    """Normalized query text, limits, and common filters."""

    text: str
    top_k: int
    semantic_candidates: int
    keyword_candidates: int
    filters: SearchFilters


def prepare_query(request: RetrievalRequest) -> PreparedQuery:
    """Convert the Pydantic request into an immutable service input."""
    return PreparedQuery(
        text=request.query,
        top_k=request.top_k,
        semantic_candidates=max(request.top_k, request.semantic_candidates),
        keyword_candidates=max(request.top_k, request.keyword_candidates),
        filters=request_filters(request),
    )
