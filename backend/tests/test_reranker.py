"""Cross-encoder reranking is bounded, lazy, and preserves citations."""

import asyncio
from uuid import uuid4

from app.retrieval.reranker import Reranker
from app.schemas.retrieval import RetrievedChunk


class Provider:
    def __init__(self, scores=None) -> None:
        self.scores = scores or []
        self.calls = []
        self.error: Exception | None = None

    def score(self, query, passages, *, batch_size):
        self.calls.append((query, list(passages), batch_size))
        if self.error:
            raise self.error
        return self.scores


def _chunk(value: int, *, text: str | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        bid_id=uuid4(),
        document_id=uuid4(),
        chunk_id=uuid4(),
        file_name="pricing.pdf",
        text=text or f"row {value}",
        page_number=4,
        section_title="Pricing",
        chunk_type="table",
        semantic_score=0.9 - value / 10,
        keyword_score=float(value),
        fusion_score=1 / (60 + value),
        metadata={"table_id": "table-1", "chunk_index": value},
    )


def _reranker(provider: Provider, **overrides) -> Reranker:
    values = {
        "enabled": True,
        "candidate_limit": 3,
        "batch_size": 2,
        "timeout_seconds": 1.0,
        "max_input_characters": 100,
    }
    values.update(overrides)
    return Reranker(provider, **values)


def test_mock_scores_set_distinct_field_and_order() -> None:
    candidates = [_chunk(1), _chunk(2), _chunk(3)]
    provider = Provider([0.2, 0.9, 0.4])
    result = asyncio.run(
        _reranker(provider).rerank(
            "Which product is required?",
            candidates,
            top_k=2,
        )
    )
    assert result.applied is True
    assert result.candidate_count == 3
    assert [item.reranker_score for item in result.results] == [0.9, 0.4]
    assert result.results[0].chunk_id == candidates[1].chunk_id
    assert result.results[0].semantic_score == candidates[1].semantic_score
    assert result.results[0].keyword_score == candidates[1].keyword_score
    assert result.results[0].fusion_score == candidates[1].fusion_score
    assert result.results[0].metadata["table_id"] == "table-1"
    assert provider.calls[0][0] == "Which product is required?"
    assert provider.calls[0][2] == 2


def test_candidate_limit_and_empty_input() -> None:
    candidates = [_chunk(index) for index in range(5)]
    provider = Provider([0.1, 0.2])
    result = asyncio.run(
        _reranker(provider, candidate_limit=2).rerank(
            "query",
            candidates,
            top_k=5,
        )
    )
    assert result.candidate_count == 2
    assert len(provider.calls[0][1]) == 2
    assert len(result.results) == 2

    empty_provider = Provider()
    empty = asyncio.run(
        _reranker(empty_provider).rerank("query", [], top_k=3)
    )
    assert empty.results == ()
    assert empty.applied is False
    assert empty_provider.calls == []


def test_disabled_reranking_does_not_load_or_score_model() -> None:
    provider = Provider([0.9])
    candidate = _chunk(1)
    result = asyncio.run(
        _reranker(provider, enabled=False).rerank(
            "query",
            [candidate],
            top_k=1,
        )
    )
    assert result.results == (candidate,)
    assert result.applied is False
    assert result.results[0].reranker_score is None
    assert provider.calls == []


def test_provider_failure_retains_rrf_order_without_fake_scores() -> None:
    provider = Provider()
    provider.error = RuntimeError("model files unavailable")
    candidates = [_chunk(1), _chunk(2)]
    result = asyncio.run(
        _reranker(provider).rerank("query", candidates, top_k=2)
    )
    assert result.results == tuple(candidates)
    assert result.applied is False
    assert all(item.reranker_score is None for item in result.results)
    assert result.warnings == (
        "Reranking failed; retained reciprocal-rank fusion order",
    )


def test_long_table_input_keeps_ends_and_returned_text_is_unchanged() -> None:
    text = "HEADER\n" + "x" * 200 + "\nLAST-ROW"
    candidate = _chunk(1, text=text)
    provider = Provider([0.7])
    result = asyncio.run(
        _reranker(provider, max_input_characters=50).rerank(
            "query",
            [candidate],
            top_k=1,
        )
    )
    model_text = provider.calls[0][1][0]
    assert len(model_text) == 50
    assert model_text.startswith("HEADER")
    assert model_text.endswith("LAST-ROW")
    assert result.results[0].text == text
    assert "returned chunk text is unchanged" in result.warnings[0]
