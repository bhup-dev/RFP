"""Normalized metadata filters shared by PostgreSQL and Qdrant retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.schemas.retrieval import AppliedFilters, RetrievalRequest


@dataclass(frozen=True)
class SearchFilters:
    """Validated filters. Empty tuples mean that field is unconstrained."""

    bid_id: UUID | None = None
    document_ids: tuple[UUID, ...] = ()
    file_types: tuple[str, ...] = ()
    chunk_types: tuple[str, ...] = ()
    file_name: str | None = None

    def public(self) -> AppliedFilters:
        return AppliedFilters(
            bid_id=self.bid_id,
            document_ids=list(self.document_ids),
            file_types=list(self.file_types),
            chunk_types=list(self.chunk_types),
            file_name=self.file_name,
        )


def request_filters(request: RetrievalRequest) -> SearchFilters:
    """Create the one filter object passed to both backends."""
    return SearchFilters(
        bid_id=request.bid_id,
        document_ids=tuple(request.document_ids),
        file_types=tuple(request.file_types),
        chunk_types=tuple(request.chunk_types),
        file_name=request.file_name,
    )
