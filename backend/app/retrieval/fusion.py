"""Reciprocal Rank Fusion for semantic and keyword chunk rankings."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.retrieval.base import SearchCandidate
from app.schemas.retrieval import RetrievedChunk


@dataclass
class _Fused:
    candidate: SearchCandidate
    semantic_rank: int | None = None
    keyword_rank: int | None = None

    def score(self, constant: int) -> float:
        value = 0.0
        if self.semantic_rank is not None:
            value += 1.0 / (constant + self.semantic_rank)
        if self.keyword_rank is not None:
            value += 1.0 / (constant + self.keyword_rank)
        return value


def reciprocal_rank_fusion(
    semantic: list[SearchCandidate] | tuple[SearchCandidate, ...],
    keyword: list[SearchCandidate] | tuple[SearchCandidate, ...],
    *,
    constant: int,
    top_k: int | None = None,
    limit: int | None = None,
) -> list[RetrievedChunk]:
    """Fuse unique chunk ids.

    Duplicate ids within one list keep their first rank. Ties are resolved by
    semantic rank, keyword rank, then lexical chunk UUID.
    """
    if constant < 1:
        raise ValueError("RRF constant must be a positive integer")
    selected_limit = top_k if top_k is not None else limit
    if selected_limit is None or selected_limit < 1:
        raise ValueError("top_k must be a positive integer")
    fused: dict[UUID, _Fused] = {}
    for rank, candidate in _unique_ranked(semantic):
        current = fused.get(candidate.chunk_id)
        if current is None:
            current = _Fused(candidate=candidate)
            fused[candidate.chunk_id] = current
        current.semantic_rank = rank
        current.candidate = _merge(current.candidate, candidate)
    for rank, candidate in _unique_ranked(keyword):
        current = fused.get(candidate.chunk_id)
        if current is None:
            current = _Fused(candidate=candidate)
            fused[candidate.chunk_id] = current
        current.keyword_rank = rank
        current.candidate = _merge(current.candidate, candidate)

    ordered = sorted(
        fused.values(),
        key=lambda item: (
            -item.score(constant),
            min(
                rank
                for rank in (item.semantic_rank, item.keyword_rank)
                if rank is not None
            ),
            str(item.candidate.chunk_id),
        ),
    )
    return [_result(item, constant) for item in ordered[:selected_limit]]


def _unique_ranked(candidates):
    seen: set[UUID] = set()
    for rank, candidate in enumerate(candidates, start=1):
        if candidate.chunk_id in seen:
            continue
        seen.add(candidate.chunk_id)
        yield rank, candidate


def _merge(left: SearchCandidate, right: SearchCandidate) -> SearchCandidate:
    """Prefer PostgreSQL-like populated values while retaining both scores."""
    metadata = dict(left.metadata or {})
    metadata.update(right.metadata or {})
    return SearchCandidate(
        bid_id=right.bid_id,
        document_id=right.document_id,
        chunk_id=right.chunk_id,
        file_name=right.file_name or left.file_name,
        text=right.text or left.text,
        page_number=right.page_number if right.page_number is not None else left.page_number,
        section_title=right.section_title or left.section_title,
        chunk_type=right.chunk_type or left.chunk_type,
        semantic_score=(
            right.semantic_score
            if right.semantic_score is not None
            else left.semantic_score
        ),
        keyword_score=(
            right.keyword_score if right.keyword_score is not None else left.keyword_score
        ),
        metadata=metadata,
    )


def _result(item: _Fused, constant: int) -> RetrievedChunk:
    candidate = item.candidate
    return RetrievedChunk(
        bid_id=candidate.bid_id,
        document_id=candidate.document_id,
        chunk_id=candidate.chunk_id,
        file_name=candidate.file_name,
        text=candidate.text,
        page_number=candidate.page_number,
        section_title=candidate.section_title,
        chunk_type=candidate.chunk_type,
        semantic_score=candidate.semantic_score,
        keyword_score=candidate.keyword_score,
        fusion_score=item.score(constant),
        metadata=candidate.metadata or {},
    )
