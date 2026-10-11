"""Retrieval metrics use known relevance and serialize reproducibly."""

import json
from uuid import UUID

import pytest

from app.retrieval.evaluation import (
    EvaluationCase,
    EvaluationQuery,
    EvaluationRun,
    RelevantSource,
    compare_runs,
    evaluation_report,
    query_metrics,
)
from app.schemas.retrieval import RetrievedChunk

BID = UUID(int=100)
DOCUMENT = UUID(int=200)


def _result(value: int, file_name: str = "scope.pdf") -> RetrievedChunk:
    return RetrievedChunk(
        bid_id=BID,
        document_id=DOCUMENT,
        chunk_id=UUID(int=value),
        file_name=file_name,
        page_number=value,
        section_title="Scope",
        text=f"chunk {value}",
    )


def test_recall_precision_mrr_and_hit_rate() -> None:
    case = EvaluationCase(
        query="requirements",
        relevant=(
            RelevantSource(chunk_id=UUID(int=2)),
            RelevantSource(file_name="other.pdf", page_number=3),
        ),
    )
    metrics = query_metrics(
        case,
        [_result(1), _result(2), _result(3, "other.pdf")],
        k=2,
    )
    assert metrics["recall_at_k"] == 0.5
    assert metrics["precision_at_k"] == 0.5
    assert metrics["reciprocal_rank"] == 0.5
    assert metrics["hit_rate_at_k"] == 1.0


def test_miss_and_unknown_ground_truth_are_distinct() -> None:
    missed = query_metrics(
        EvaluationCase("missing", (RelevantSource(chunk_id=UUID(int=9)),)),
        [_result(1)],
        k=1,
    )
    unknown = query_metrics(EvaluationCase("unknown", ()), [], k=1)
    assert missed["recall_at_k"] == 0.0
    assert missed["hit_rate_at_k"] == 0.0
    assert unknown["known_ground_truth"] is False
    assert unknown["recall_at_k"] is None


def test_report_is_machine_readable_and_records_configuration() -> None:
    cases = [
        EvaluationCase("hit", (RelevantSource(chunk_id=UUID(int=1)),)),
        EvaluationCase("unknown", ()),
    ]
    report = evaluation_report(
        cases,
        [[_result(1)], []],
        k=5,
        configuration={
            "semantic_candidates": 30,
            "keyword_candidates": 30,
            "rrf_constant": 60,
            "filters": {"bid_id": str(BID)},
        },
    )
    assert report["aggregate"]["queries_with_ground_truth"] == 1
    assert report["aggregate"]["queries_without_ground_truth"] == 1
    assert report["aggregate"]["mrr"] == 1.0
    assert report["configuration"]["rrf_constant"] == 60
    json.dumps(report, sort_keys=True)


def test_report_requires_one_result_list_per_case() -> None:
    with pytest.raises(ValueError, match="one result list"):
        evaluation_report(
            [EvaluationCase("one", ())],
            [],
            k=1,
            configuration={},
        )


def test_comparison_keeps_baseline_and_enhancement_measurements_separate() -> None:
    query = EvaluationQuery(
        query_id="q1",
        query="Which warranty is required?",
        relevant=[RelevantSource(chunk_id=UUID(int=1), grade=2)],
    )
    comparison = compare_runs(
        [query],
        [
            EvaluationRun(
                name="baseline",
                retrieved={"q1": [_result(2), _result(1)]},
                latencies_ms={"q1": 10.0},
                candidate_counts={"q1": 20},
                configuration={"rewriting": False, "reranking": False},
            ),
            EvaluationRun(
                name="rewrite_and_rerank",
                retrieved={"q1": [_result(1), _result(2)]},
                latencies_ms={"q1": 25.0},
                candidate_counts={"q1": 30},
                configuration={"rewriting": True, "reranking": True},
            ),
        ],
        k=2,
    )
    baseline, enhanced = comparison["runs"]
    assert baseline["configuration"]["name"] == "baseline"
    assert baseline["metrics"]["mrr"] == 0.5
    assert baseline["measurements"]["mean_latency_ms"] == 10.0
    assert enhanced["metrics"]["mrr"] == 1.0
    assert enhanced["measurements"]["mean_candidate_count"] == 30.0
    json.dumps(comparison, allow_nan=False)
