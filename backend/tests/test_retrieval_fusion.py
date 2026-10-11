"""Reciprocal-rank fusion is deterministic and preserves source scores."""

from uuid import UUID

import pytest

from app.retrieval.base import SearchCandidate
from app.retrieval.fusion import reciprocal_rank_fusion

BID = UUID("11111111-1111-1111-1111-111111111111")
DOCUMENT = UUID("22222222-2222-2222-2222-222222222222")


def _candidate(value: int, *, semantic=None, keyword=None) -> SearchCandidate:
    return SearchCandidate(
        bid_id=BID,
        document_id=DOCUMENT,
        chunk_id=UUID(int=value),
        file_name="notice.pdf",
        text=f"chunk {value}",
        semantic_score=semantic,
        keyword_score=keyword,
        metadata={"chunk_index": value},
    )


def test_rrf_combines_ranks_and_deduplicates() -> None:
    shared = _candidate(1, semantic=0.91)
    semantic = (shared, _candidate(2, semantic=0.8))
    keyword = (_candidate(3, keyword=2.0), _candidate(1, keyword=1.0))

    results = reciprocal_rank_fusion(semantic, keyword, constant=60, limit=10)

    assert [result.chunk_id for result in results] == [
        UUID(int=1),
        UUID(int=3),
        UUID(int=2),
    ]
    assert results[0].fusion_score == pytest.approx(1 / 61 + 1 / 62)
    assert results[0].semantic_score == 0.91
    assert results[0].keyword_score == 1.0
    assert results[1].semantic_score is None
    assert results[1].keyword_score == 2.0


def test_rrf_ignores_duplicate_ids_inside_one_list() -> None:
    first = _candidate(1, semantic=0.9)
    duplicate = _candidate(1, semantic=0.1)
    results = reciprocal_rank_fusion(
        (first, duplicate),
        (),
        constant=60,
        limit=10,
    )
    assert len(results) == 1
    assert results[0].fusion_score == pytest.approx(1 / 61)
    assert results[0].semantic_score == 0.9


def test_rrf_empty_lists_and_ties_are_stable() -> None:
    assert reciprocal_rank_fusion((), (), constant=60, limit=8) == []
    results = reciprocal_rank_fusion(
        (_candidate(2, semantic=0.9),),
        (_candidate(1, keyword=0.9),),
        constant=60,
        limit=1,
    )
    assert [result.chunk_id for result in results] == [UUID(int=1)]
