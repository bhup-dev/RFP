"""Dependency-free retrieval metrics and dataset loading.

Relevant chunks match by chunk UUID when stable ids are known, or by source
file plus optional page/section constraints after re-chunking.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.retrieval import RetrievedChunk


class RelevantSource(BaseModel):
    """One verified relevant source. Grade zero is not relevant."""

    chunk_id: UUID | None = None
    file_name: str | None = None
    page_number: int | None = Field(default=None, ge=1)
    section_title: str | None = None
    grade: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def has_stable_match_key(self) -> "RelevantSource":
        if self.chunk_id is None and not self.file_name:
            raise ValueError("relevant source needs chunk_id or file_name")
        return self


class EvaluationQuery(BaseModel):
    """One query with verified ground truth or an explicit no-result expectation."""

    query_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    bid_identifier: str | None = None
    relevant: list[RelevantSource] = Field(default_factory=list)
    expect_no_results: bool = False

    @model_validator(mode="after")
    def ground_truth_is_unambiguous(self) -> "EvaluationQuery":
        if self.expect_no_results and self.relevant:
            raise ValueError("a no-result query cannot also have relevant sources")
        return self


@dataclass(frozen=True)
class QueryMetrics:
    query_id: str
    recall_at_k: float | None
    precision_at_k: float | None
    reciprocal_rank: float | None
    hit_at_k: float | None
    ndcg_at_k: float | None
    no_result_correct: bool | None


@dataclass(frozen=True)
class EvaluationRun:
    """Measured retrieval output for one enhancement configuration."""

    name: str
    retrieved: dict[str, list[RetrievedChunk]]
    latencies_ms: dict[str, float]
    candidate_counts: dict[str, int]
    configuration: dict[str, Any]


def evaluate(
    queries: list[EvaluationQuery],
    retrieved: dict[str, list[RetrievedChunk]],
    *,
    k: int,
    configuration: dict[str, Any],
) -> dict[str, Any]:
    """Return per-query and aggregate metrics as a JSON-compatible mapping.

    Queries without relevant sources and without ``expect_no_results`` have
    unknown ground truth. They are reported but excluded from averages.
    """
    if k < 1:
        raise ValueError("k must be positive")
    per_query = [_measure(query, retrieved.get(query.query_id, []), k) for query in queries]
    judged = [item for item in per_query if item.recall_at_k is not None]
    no_result = [item for item in per_query if item.no_result_correct is not None]
    report = {
        "configuration": {**configuration, "k": k},
        "query_count": len(queries),
        "judged_query_count": len(judged),
        "unknown_ground_truth_count": len(queries) - len(judged) - len(no_result),
        "no_result_query_count": len(no_result),
        "metrics": {
            "recall_at_k": _mean([item.recall_at_k for item in judged]),
            "precision_at_k": _mean([item.precision_at_k for item in judged]),
            "mrr": _mean([item.reciprocal_rank for item in judged]),
            "hit_rate_at_k": _mean([item.hit_at_k for item in judged]),
            "ndcg_at_k": _mean([item.ndcg_at_k for item in judged]),
            "no_result_accuracy": _mean(
                [1.0 if item.no_result_correct else 0.0 for item in no_result]
            ),
        },
        "queries": [
            {
                "query_id": item.query_id,
                "recall_at_k": item.recall_at_k,
                "precision_at_k": item.precision_at_k,
                "reciprocal_rank": item.reciprocal_rank,
                "hit_at_k": item.hit_at_k,
                "ndcg_at_k": item.ndcg_at_k,
                "no_result_correct": item.no_result_correct,
            }
            for item in per_query
        ],
    }
    json.dumps(report, allow_nan=False)
    return report


def load_dataset(path: Path) -> list[EvaluationQuery]:
    """Load JSON ``{\"queries\": [...]}`` or one query per JSONL line."""
    if path.suffix.lower() == ".jsonl":
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        body = json.loads(path.read_text(encoding="utf-8"))
        rows = body["queries"] if isinstance(body, dict) else body
    if not isinstance(rows, list):
        raise ValueError("evaluation dataset must contain a query list")
    return [EvaluationQuery.model_validate(row) for row in rows]


def compare_runs(
    queries: list[EvaluationQuery],
    runs: list[EvaluationRun],
    *,
    k: int,
) -> dict[str, Any]:
    """Compare measured baseline, rewrite, rerank, and combined runs.

    This function does not execute retrieval and never fills missing results.
    Callers must provide outputs and latency measured from actual runs.
    """
    reports: list[dict[str, Any]] = []
    query_ids = {query.query_id for query in queries}
    for run in runs:
        unknown = set(run.retrieved) - query_ids
        if unknown:
            raise ValueError("evaluation run contains unknown query ids")
        report = evaluate(
            queries,
            run.retrieved,
            k=k,
            configuration={"name": run.name, **run.configuration},
        )
        latencies = [
            value
            for query_id, value in run.latencies_ms.items()
            if query_id in query_ids
        ]
        candidate_counts = [
            value
            for query_id, value in run.candidate_counts.items()
            if query_id in query_ids
        ]
        report["measurements"] = {
            "mean_latency_ms": _mean(latencies),
            "mean_candidate_count": _mean(
                [float(value) for value in candidate_counts]
            ),
            "latency_query_count": len(latencies),
            "candidate_count_query_count": len(candidate_counts),
        }
        reports.append(report)
    comparison = {"k": k, "runs": reports}
    json.dumps(comparison, allow_nan=False)
    return comparison


def _measure(
    query: EvaluationQuery,
    results: list[RetrievedChunk],
    k: int,
) -> QueryMetrics:
    limited = results[:k]
    if query.expect_no_results:
        return QueryMetrics(
            query_id=query.query_id,
            recall_at_k=None,
            precision_at_k=None,
            reciprocal_rank=None,
            hit_at_k=None,
            ndcg_at_k=None,
            no_result_correct=not limited,
        )
    if not query.relevant:
        return QueryMetrics(query.query_id, None, None, None, None, None, None)

    seen_relevant: set[int] = set()
    grades: list[int] = []
    for result in limited:
        matched = _matched_index(result, query.relevant, seen_relevant)
        if matched is None:
            grades.append(0)
            continue
        seen_relevant.add(matched)
        grades.append(query.relevant[matched].grade)
    hits = sum(1 for grade in grades if grade > 0)
    relevant_keys = len(query.relevant)
    reciprocal_rank = next(
        (1.0 / rank for rank, grade in enumerate(grades, start=1) if grade > 0),
        0.0,
    )
    dcg = sum((2**grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(grades, 1))
    ideal_grades = sorted((item.grade for item in query.relevant), reverse=True)[:k]
    ideal = sum(
        (2**grade - 1) / math.log2(rank + 1)
        for rank, grade in enumerate(ideal_grades, 1)
    )
    return QueryMetrics(
        query_id=query.query_id,
        recall_at_k=hits / relevant_keys,
        precision_at_k=hits / k,
        reciprocal_rank=reciprocal_rank,
        hit_at_k=1.0 if hits else 0.0,
        ndcg_at_k=dcg / ideal if ideal else 0.0,
        no_result_correct=None,
    )


def _matched_index(
    result: RetrievedChunk,
    relevant: list[RelevantSource],
    seen: set[int],
) -> int | None:
    matches: list[tuple[int, int]] = []
    for index, expected in enumerate(relevant):
        if index in seen:
            continue
        if expected.chunk_id is not None:
            if result.chunk_id == expected.chunk_id:
                matches.append((expected.grade, index))
            continue
        if result.file_name != expected.file_name:
            continue
        if expected.page_number is not None and result.page_number != expected.page_number:
            continue
        if expected.section_title is not None and result.section_title != expected.section_title:
            continue
        matches.append((expected.grade, index))
    return max(matches, default=(0, -1))[1] if matches else None


def _mean(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None


@dataclass(frozen=True)
class EvaluationCase:
    """Compatibility input for direct, binary per-query metric calls."""

    query: str
    relevant: tuple[RelevantSource, ...]


def query_metrics(
    case: EvaluationCase,
    results: list[RetrievedChunk],
    *,
    k: int,
) -> dict[str, float | int | bool | None]:
    """Compute binary Recall@K, Precision@K, MRR, and Hit Rate@K."""
    if k < 1:
        raise ValueError("k must be positive")
    if not case.relevant:
        return {
            "known_ground_truth": False,
            "relevant_count": 0,
            "retrieved_count": min(k, len(results)),
            "recall_at_k": None,
            "precision_at_k": None,
            "reciprocal_rank": None,
            "hit_rate_at_k": None,
        }
    top = results[:k]
    seen: set[int] = set()
    grades: list[int] = []
    for result in top:
        index = _matched_index(result, list(case.relevant), seen)
        if index is None:
            grades.append(0)
        else:
            seen.add(index)
            grades.append(1)
    first_rank = next(
        (rank for rank, grade in enumerate(grades, start=1) if grade),
        None,
    )
    return {
        "known_ground_truth": True,
        "relevant_count": len(case.relevant),
        "retrieved_count": len(top),
        "recall_at_k": len(seen) / len(case.relevant),
        "precision_at_k": sum(grades) / k,
        "reciprocal_rank": 0.0 if first_rank is None else 1.0 / first_rank,
        "hit_rate_at_k": 0.0 if first_rank is None else 1.0,
    }


def evaluation_report(
    cases: list[EvaluationCase],
    retrieved: list[list[RetrievedChunk]],
    *,
    k: int,
    configuration: dict[str, Any],
) -> dict[str, Any]:
    """Aggregate legacy case inputs without scoring unknown ground truth."""
    if len(cases) != len(retrieved):
        raise ValueError("one result list is required for each evaluation case")
    per_query = [
        {"query": case.query, **query_metrics(case, results, k=k)}
        for case, results in zip(cases, retrieved, strict=True)
    ]
    known = [item for item in per_query if item["known_ground_truth"]]
    aggregate: dict[str, float | int | None] = {
        "queries": len(cases),
        "queries_with_ground_truth": len(known),
        "queries_without_ground_truth": len(cases) - len(known),
    }
    for output, source in (
        ("recall_at_k", "recall_at_k"),
        ("precision_at_k", "precision_at_k"),
        ("mrr", "reciprocal_rank"),
        ("hit_rate_at_k", "hit_rate_at_k"),
    ):
        aggregate[output] = (
            None
            if not known
            else sum(float(item[source]) for item in known) / len(known)
        )
    report = {
        "configuration": {"k": k, **configuration},
        "aggregate": aggregate,
        "queries": per_query,
    }
    json.dumps(report, allow_nan=False)
    return report
