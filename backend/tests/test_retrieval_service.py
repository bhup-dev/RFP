"""Hybrid coordination and degraded fallback behavior."""

import asyncio
from uuid import UUID, uuid4

import pytest

from app.retrieval.base import (
    BackendResult,
    RetrievalBackendError,
    RetrievalUnavailable,
    SearchCandidate,
)
from app.retrieval.retrieval_service import RetrievalService
from app.retrieval.query_rewriter import QueryRewriter
from app.retrieval.reranker import Reranker
from app.schemas.retrieval import RetrievalRequest
from app.schemas.workflow import (
    CitationFailure,
    CitationValidationResult,
)

BID = UUID("11111111-1111-1111-1111-111111111111")
DOCUMENT = UUID("22222222-2222-2222-2222-222222222222")


def _candidate(value: int, *, semantic=None, keyword=None) -> SearchCandidate:
    return SearchCandidate(
        bid_id=BID,
        document_id=DOCUMENT,
        chunk_id=UUID(int=value),
        file_name="scope.pdf",
        text=f"requirement {value}",
        page_number=2,
        section_title="Scope",
        chunk_type="text",
        semantic_score=semantic,
        keyword_score=keyword,
        metadata={"heading_path": ["Scope"]},
    )


class Backend:
    def __init__(self, result=None, error=None) -> None:
        self.result = result or BackendResult(())
        self.error = error
        self.queries = []

    async def search(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.result


def test_successful_hybrid_search_fuses_and_limits() -> None:
    semantic = Backend(BackendResult((_candidate(1, semantic=0.9), _candidate(2, semantic=0.8))))
    keyword = Backend(BackendResult((_candidate(1, keyword=2.0), _candidate(3, keyword=1.0))))
    service = RetrievalService(semantic, keyword, rrf_constant=60)

    response = asyncio.run(
        service.search(
            RetrievalRequest(
                query="payment terms",
                bid_id=BID,
                document_ids=[DOCUMENT],
                top_k=2,
                semantic_candidates=5,
                keyword_candidates=6,
            )
        )
    )

    assert response.retrieval_mode.value == "hybrid"
    assert response.total_results == 2
    assert response.results[0].chunk_id == UUID(int=1)
    assert response.results[0].semantic_score == 0.9
    assert response.results[0].keyword_score == 2.0
    assert response.applied_filters.bid_id == BID
    assert response.applied_filters.document_ids == [DOCUMENT]
    assert semantic.queries[0].semantic_candidates == 5
    assert keyword.queries[0].keyword_candidates == 6


@pytest.mark.parametrize(
    ("failed", "mode"),
    [("semantic", "keyword_only"), ("keyword", "semantic_only")],
)
def test_one_backend_failure_is_labeled_degraded(failed: str, mode: str) -> None:
    semantic = Backend(BackendResult((_candidate(1, semantic=0.9),)))
    keyword = Backend(BackendResult((_candidate(2, keyword=1.0),)))
    backend = semantic if failed == "semantic" else keyword
    backend.error = RetrievalBackendError(failed, f"{failed} unavailable")

    response = asyncio.run(
        RetrievalService(semantic, keyword).search(
            RetrievalRequest(query="delivery", bid_id=BID)
        )
    )

    assert response.retrieval_mode.value == mode
    assert len(response.results) == 1
    assert "available backend" in response.warnings[0]
    assert response.applied_filters.bid_id == BID


def test_both_backends_failing_returns_service_error() -> None:
    semantic = Backend(error=RetrievalBackendError("semantic", "embedding unavailable"))
    keyword = Backend(error=RetrievalBackendError("keyword", "database unavailable"))
    with pytest.raises(RetrievalUnavailable) as raised:
        asyncio.run(
            RetrievalService(semantic, keyword).search(
                RetrievalRequest(query="delivery")
            )
        )
    assert raised.value.errors == ["embedding unavailable", "database unavailable"]


def test_degraded_fallback_can_be_disabled() -> None:
    semantic = Backend(error=RetrievalBackendError("semantic", "semantic unavailable"))
    keyword = Backend(BackendResult((_candidate(1, keyword=1.0),)))
    with pytest.raises(RetrievalUnavailable):
        asyncio.run(
            RetrievalService(
                semantic,
                keyword,
                allow_degraded=False,
            ).search(RetrievalRequest(query="delivery"))
        )


def test_query_schema_rejects_empty_long_and_invalid_limits() -> None:
    from pydantic import ValidationError

    for payload in (
        {"query": "   "},
        {"query": "x" * 2001},
        {"query": "ok", "top_k": 0},
        {"query": "ok", "semantic_candidates": 201},
        {"query": "ok", "keyword_candidates": 0},
    ):
        with pytest.raises(ValidationError):
            RetrievalRequest(**payload)

    request = RetrievalRequest(
        query="ok",
        document_ids=[uuid4()],
        file_types=["PDF", "pdf"],
        chunk_types=["TABLE"],
    )
    assert request.file_types == ["pdf"]
    assert request.chunk_types == ["table"]


class RewriteLLM:
    async def generate_response(self, prompt, **kwargs):
        return '{"queries": ["original warranty support obligations", "original question"]}'


class RerankProvider:
    def __init__(self) -> None:
        self.query = None

    def score(self, query, passages, *, batch_size):
        self.query = query
        return list(reversed(range(len(passages))))


class VariantBackend:
    def __init__(self, method: str) -> None:
        self.method = method
        self.queries = []
        self.shared = _candidate(
            10,
            semantic=0.8 if method == "semantic" else None,
            keyword=1.0 if method == "keyword" else None,
        )

    async def search(self, query):
        self.queries.append(query)
        unique = _candidate(
            20 + len(self.queries),
            semantic=0.7 if self.method == "semantic" else None,
            keyword=0.7 if self.method == "keyword" else None,
        )
        return BackendResult((self.shared, unique))


def test_rewriting_reuses_filters_deduplicates_and_reranks_original_query() -> None:
    semantic = VariantBackend("semantic")
    keyword = VariantBackend("keyword")
    provider = RerankProvider()
    service = RetrievalService(
        semantic,
        keyword,
        rewriter=QueryRewriter(
            RewriteLLM(),
            enabled=True,
            max_variants=2,
            timeout_seconds=1,
        ),
        reranker=Reranker(
            provider,
            enabled=True,
            candidate_limit=10,
            batch_size=4,
            timeout_seconds=1,
            max_input_characters=1000,
        ),
    )
    document_id = uuid4()
    request = RetrievalRequest(
        query="original question",
        bid_id=BID,
        document_ids=[document_id],
        file_types=["pdf"],
        chunk_types=["text"],
        top_k=3,
    )

    response = asyncio.run(service.search(request))

    # The generated duplicate is removed, so original plus one alternative run.
    assert [query.text for query in semantic.queries] == [
        "original question",
        "original warranty support obligations",
    ]
    assert [query.text for query in keyword.queries] == [
        "original question",
        "original warranty support obligations",
    ]
    for query in [*semantic.queries, *keyword.queries]:
        assert query.filters.bid_id == BID
        assert query.filters.document_ids == (document_id,)
        assert query.filters.file_types == ("pdf",)
        assert query.filters.chunk_types == ("text",)
    assert provider.query == "original question"
    assert response.rewriting_applied is True
    assert response.rewritten_query_count == 1
    assert response.reranking_applied is True
    assert response.candidate_count >= response.final_result_count
    assert response.final_result_count == len(response.results)
    assert response.retrieval_mode.value == "hybrid"
    assert len({item.chunk_id for item in response.results}) == len(response.results)
    assert all(item.reranker_score is not None for item in response.results)


def test_enhancements_can_be_enabled_independently() -> None:
    semantic = Backend(BackendResult((_candidate(1, semantic=0.9),)))
    keyword = Backend(BackendResult((_candidate(1, keyword=1.0),)))
    baseline = asyncio.run(
        RetrievalService(semantic, keyword).search(
            RetrievalRequest(query="WD22TB4", top_k=1)
        )
    )
    assert baseline.rewriting_applied is False
    assert baseline.reranking_applied is False
    assert baseline.results[0].reranker_score is None

    rewrite_only = asyncio.run(
        RetrievalService(
            semantic,
            keyword,
            rewriter=QueryRewriter(
                RewriteLLM(),
                enabled=True,
                max_variants=2,
                timeout_seconds=1,
            ),
        ).search(RetrievalRequest(query="original question", top_k=1))
    )
    assert rewrite_only.rewriting_applied is True
    assert rewrite_only.reranking_applied is False
    assert rewrite_only.results[0].reranker_score is None

    provider = RerankProvider()
    rerank_only = asyncio.run(
        RetrievalService(
            semantic,
            keyword,
            reranker=Reranker(
                provider,
                enabled=True,
                candidate_limit=5,
                batch_size=2,
                timeout_seconds=1,
                max_input_characters=1000,
            ),
        ).search(RetrievalRequest(query="WD22TB4", top_k=1))
    )
    assert rerank_only.rewriting_applied is False
    assert rerank_only.reranking_applied is True
    assert provider.query == "WD22TB4"


class CitationValidator:
    def __init__(self, rejected=()) -> None:
        self.rejected = set(rejected)
        self.calls = []

    async def validate(self, results):
        self.calls.append(results)
        failures = [
            CitationFailure(chunk_id=item.chunk_id, reason="chunk does not exist")
            for item in results
            if item.chunk_id in self.rejected
        ]
        return CitationValidationResult(
            checked=len(results),
            resolved=len(results) - len(failures),
            failures=failures,
        )


def test_unresolvable_citations_are_not_returned() -> None:
    stale = _candidate(1, semantic=0.9)
    valid = _candidate(2, keyword=1.0)
    validator = CitationValidator(rejected={stale.chunk_id})
    response = asyncio.run(
        RetrievalService(
            Backend(BackendResult((stale,))),
            Backend(BackendResult((valid,))),
            citation_validator=validator,
        ).search(RetrievalRequest(query="requirements"))
    )
    assert [item.chunk_id for item in response.results] == [valid.chunk_id]
    assert "Removed 1 result" in response.warnings[0]
    assert len(validator.calls) == 1
