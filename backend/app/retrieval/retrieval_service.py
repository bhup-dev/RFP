"""Coordinate semantic search, keyword search, fallback, and RRF."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import Protocol
from uuid import UUID

from app.retrieval.base import (
    BackendResult,
    RetrievalBackendError,
    RetrievalUnavailable,
    SearchCandidate,
)
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.query_rewriter import QueryRewriter, RewriteResult
from app.retrieval.query_service import PreparedQuery, prepare_query
from app.retrieval.reranker import Reranker, RerankResult
from app.schemas.retrieval import (
    RetrievalMode,
    RetrievalRequest,
    RetrievalResponse,
    RetrievedChunk,
)
from app.schemas.workflow import CitationValidationResult

logger = logging.getLogger(__name__)


class SearchBackend(Protocol):
    async def search(self, query: PreparedQuery) -> BackendResult:
        """Return ranked candidates or raise RetrievalBackendError."""


class CitationValidator(Protocol):
    async def validate(
        self,
        results: list[RetrievedChunk],
    ) -> CitationValidationResult:
        """Resolve candidate ids against PostgreSQL."""


class RetrievalService:
    """Optionally rewrite, retrieve every variant, fuse, and rerank."""

    def __init__(
        self,
        semantic: SearchBackend,
        keyword: SearchBackend,
        *,
        rrf_constant: int = 60,
        allow_degraded: bool = True,
        rewriter: QueryRewriter | None = None,
        reranker: Reranker | None = None,
        citation_validator: CitationValidator | None = None,
    ) -> None:
        if rrf_constant < 1:
            raise ValueError("rrf_constant must be positive")
        self._semantic = semantic
        self._keyword = keyword
        self._rrf_constant = rrf_constant
        self._allow_degraded = allow_degraded
        self._rewriter = rewriter
        self._reranker = reranker
        self._citation_validator = citation_validator

    async def search(self, request: RetrievalRequest) -> RetrievalResponse:
        rewrite = (
            await self._rewriter.rewrite(request.query)
            if self._rewriter is not None
            else RewriteResult((request.query,), applied=False)
        )
        pool_limit = request.top_k
        if self._reranker is not None and self._reranker.enabled:
            pool_limit = max(pool_limit, self._reranker.candidate_limit)
        prepared = [
            prepare_query(
                request.model_copy(
                    update={"query": text, "top_k": pool_limit}
                )
            )
            for text in rewrite.queries
        ]
        outcomes = await asyncio.gather(
            *[
                _run(name, backend, query)
                for query in prepared
                for name, backend in (
                    ("semantic", self._semantic),
                    ("keyword", self._keyword),
                )
            ]
        )
        semantic_runs = outcomes[0::2]
        keyword_runs = outcomes[1::2]
        semantic_results, semantic_errors = _split(semantic_runs)
        keyword_results, keyword_errors = _split(keyword_runs)
        failures: list[RetrievalBackendError] = []
        if not semantic_results:
            failures.append(
                semantic_errors[0]
                if semantic_errors
                else RetrievalBackendError("semantic", "semantic backend unavailable")
            )
        if not keyword_results:
            failures.append(
                keyword_errors[0]
                if keyword_errors
                else RetrievalBackendError("keyword", "keyword backend unavailable")
            )
        if len(failures) == 2:
            raise RetrievalUnavailable(
                [failure.public_message for failure in failures]
            )
        if failures and not self._allow_degraded:
            raise RetrievalUnavailable(
                [failure.public_message for failure in failures]
            )

        if semantic_results and keyword_results:
            mode = RetrievalMode.HYBRID
        elif semantic_results:
            mode = RetrievalMode.SEMANTIC_ONLY
        else:
            mode = RetrievalMode.KEYWORD_ONLY
        semantic_candidates = _merge_variants(semantic_results, "semantic")
        keyword_candidates = _merge_variants(keyword_results, "keyword")
        candidates = reciprocal_rank_fusion(
            semantic_candidates,
            keyword_candidates,
            constant=self._rrf_constant,
            top_k=pool_limit,
        )
        citation_warning: str | None = None
        if self._citation_validator is not None:
            try:
                validation = await self._citation_validator.validate(candidates)
            except Exception as exc:
                raise RetrievalUnavailable(
                    ["Citation validation is unavailable"]
                ) from exc
            if validation.failures:
                failed_ids = {failure.chunk_id for failure in validation.failures}
                candidates = [
                    candidate
                    for candidate in candidates
                    if candidate.chunk_id not in failed_ids
                ]
                citation_warning = (
                    f"Removed {len(validation.failures)} result(s) that did not "
                    "resolve to authoritative chunks"
                )
        reranked = (
            await self._reranker.rerank(
                request.query,
                candidates,
                top_k=request.top_k,
            )
            if self._reranker is not None
            else RerankResult(
                results=tuple(candidates[: request.top_k]),
                applied=False,
                candidate_count=len(candidates),
            )
        )
        warnings = [
            warning
            for result in (*semantic_results, *keyword_results)
            for warning in result.warnings
        ]
        if rewrite.warning:
            warnings.append(rewrite.warning)
        if citation_warning:
            warnings.append(citation_warning)
        warnings.extend(reranked.warnings)
        warnings.extend(
            f"{error.backend} retrieval failed; returned results from the available backend"
            for error in failures
        )
        if semantic_errors and semantic_results:
            warnings.append(
                "Some semantic query variants failed; successful variants were retained"
            )
        if keyword_errors and keyword_results:
            warnings.append(
                "Some keyword query variants failed; successful variants were retained"
            )
        logger.info(
            "Retrieval complete mode=%s variants=%s candidates=%s results=%s reranked=%s",
            mode.value,
            len(rewrite.queries),
            len(candidates),
            len(reranked.results),
            reranked.applied,
        )
        return RetrievalResponse(
            query=request.query,
            bid_id=request.bid_id,
            results=list(reranked.results),
            total_results=len(reranked.results),
            retrieval_mode=mode,
            applied_filters=prepared[0].filters.public(),
            warnings=warnings,
            diagnostics={
                "semantic_candidates_returned": len(semantic_candidates),
                "keyword_candidates_returned": len(keyword_candidates),
                "rrf_constant": self._rrf_constant,
                "query_variant_count": len(rewrite.queries),
            },
            rewriting_applied=rewrite.applied,
            rewritten_query_count=max(0, len(rewrite.queries) - 1),
            candidate_count=reranked.candidate_count,
            reranking_applied=reranked.applied,
            final_result_count=len(reranked.results),
        )


async def _run(
    name: str,
    backend: SearchBackend,
    query: PreparedQuery,
) -> tuple[BackendResult | None, RetrievalBackendError | None]:
    try:
        return await backend.search(query), None
    except RetrievalBackendError as exc:
        logger.error("%s retrieval failed (%s)", name, type(exc).__name__)
        return None, exc
    except Exception:
        logger.error("%s retrieval failed (unexpected error)", name)
        return None, RetrievalBackendError(name, f"{name} backend unavailable")


def _split(
    outcomes: list[tuple[BackendResult | None, RetrievalBackendError | None]],
) -> tuple[list[BackendResult], list[RetrievalBackendError]]:
    return (
        [result for result, _error in outcomes if result is not None],
        [error for _result, error in outcomes if error is not None],
    )


def _merge_variants(
    results: list[BackendResult],
    method: str,
) -> tuple[SearchCandidate, ...]:
    """Keep each chunk's best rank across query variants.

    Original-query rank wins ties because its result list is first. This
    produces one semantic and one keyword list for the existing two-list RRF.
    """
    best: dict[UUID, tuple[int, int, SearchCandidate]] = {}
    for variant_index, result in enumerate(results):
        seen: set[UUID] = set()
        for rank, candidate in enumerate(result.candidates, start=1):
            if candidate.chunk_id in seen:
                continue
            seen.add(candidate.chunk_id)
            current = best.get(candidate.chunk_id)
            if current is None or (rank, variant_index) < current[:2]:
                best[candidate.chunk_id] = (rank, variant_index, candidate)
            elif current is not None:
                best[candidate.chunk_id] = (
                    current[0],
                    current[1],
                    _merge_candidate_scores(current[2], candidate),
                )
    ordered = sorted(
        best.values(),
        key=lambda item: (item[0], item[1], str(item[2].chunk_id)),
    )
    _ = method
    return tuple(item[2] for item in ordered)


def _merge_candidate_scores(
    first: SearchCandidate,
    second: SearchCandidate,
) -> SearchCandidate:
    metadata = dict(first.metadata or {})
    metadata.update(second.metadata or {})
    semantic = [value for value in (first.semantic_score, second.semantic_score) if value is not None]
    keyword = [value for value in (first.keyword_score, second.keyword_score) if value is not None]
    return replace(
        first,
        semantic_score=max(semantic) if semantic else None,
        keyword_score=max(keyword) if keyword else None,
        metadata=metadata,
    )
