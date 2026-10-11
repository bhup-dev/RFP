"""Pure deterministic metrics. Missing labels are never silently scored."""

import math
from datetime import UTC, datetime
from typing import Any, Iterable

from app.evaluation.schemas import MetricResult, MetricStatus


def exact_match_rate(expected: list[Any], actual: list[Any]) -> MetricResult:
    _same_length(expected, actual)
    if not expected:
        return _unavailable("exact_match_rate", "No labeled examples")
    matches = sum(left == right for left, right in zip(expected, actual, strict=True))
    return _metric("exact_match_rate", matches, len(expected))


def field_level_accuracy(
    expected: list[dict[str, Any]], actual: list[dict[str, Any]]
) -> MetricResult:
    _same_length(expected, actual)
    labeled = [
        (key, value, observed.get(key))
        for labels, observed in zip(expected, actual, strict=True)
        for key, value in labels.items()
    ]
    if not labeled:
        return _unavailable("field_level_accuracy", "No field labels")
    correct = sum(expected_value == actual_value for _, expected_value, actual_value in labeled)
    return _metric("field_level_accuracy", correct, len(labeled))


def set_precision_recall_f1(
    expected: Iterable[str], actual: Iterable[str], *, prefix: str
) -> list[MetricResult]:
    truth, predicted = set(expected), set(actual)
    if not truth and not predicted:
        explanation = "No positive labels or predictions"
        return [
            _unavailable(f"{prefix}_{name}", explanation)
            for name in ("precision", "recall", "f1")
        ]
    true_positive = len(truth & predicted)
    precision = (
        None if not predicted else true_positive / len(predicted)
    )
    recall = None if not truth else true_positive / len(truth)
    f1 = (
        None
        if precision is None or recall is None
        else (
            0.0
            if precision + recall == 0
            else 2 * precision * recall / (precision + recall)
        )
    )
    return [
        _value_metric(
            f"{prefix}_precision",
            precision,
            len(predicted),
            true_positive,
            "No predicted positives" if precision is None else None,
        ),
        _value_metric(
            f"{prefix}_recall",
            recall,
            len(truth),
            true_positive,
            "No ground-truth positives" if recall is None else None,
        ),
        _value_metric(
            f"{prefix}_f1",
            f1,
            max(len(truth), len(predicted)),
            explanation="Precision or recall is unavailable" if f1 is None else None,
        ),
    ]


def missing_value_accuracy(
    expected: list[Any], actual: list[Any]
) -> MetricResult:
    _same_length(expected, actual)
    if not expected:
        return _unavailable("missing_value_accuracy", "No labeled examples")
    correct = sum(
        (left is None) == (right is None)
        for left, right in zip(expected, actual, strict=True)
    )
    return _metric("missing_value_accuracy", correct, len(expected))


def completeness(values: list[Any]) -> MetricResult:
    if not values:
        return _unavailable("field_completeness", "No fields were evaluated")
    populated = sum(value is not None for value in values)
    return _metric("field_completeness", populated, len(values))


def datetime_correctness(
    expected: list[str | None], actual: list[str | None]
) -> MetricResult:
    """Compare aware ISO-8601 instants in UTC; naive timestamps are unscorable."""
    _same_length(expected, actual)
    judged = []
    for left, right in zip(expected, actual, strict=True):
        normalized_left = _aware_utc(left)
        normalized_right = _aware_utc(right)
        if normalized_left is None:
            continue
        judged.append(normalized_left == normalized_right)
    if not judged:
        return _unavailable(
            "datetime_correctness",
            "No timezone-aware ISO-8601 ground-truth values",
        )
    return _metric("datetime_correctness", sum(judged), len(judged))


def classification_accuracy(
    expected: list[str], actual: list[str], *, name: str
) -> MetricResult:
    _same_length(expected, actual)
    if not expected:
        return _unavailable(name, "No classification labels")
    correct = sum(left == right for left, right in zip(expected, actual, strict=True))
    return _metric(name, correct, len(expected))


def binary_label_metrics(
    expected: list[str],
    actual: list[str],
    *,
    positive_labels: set[str],
    prefix: str,
) -> list[MetricResult]:
    _same_length(expected, actual)
    truth = {
        str(index)
        for index, label in enumerate(expected)
        if label in positive_labels
    }
    predicted = {
        str(index)
        for index, label in enumerate(actual)
        if label in positive_labels
    }
    return set_precision_recall_f1(truth, predicted, prefix=prefix)


def citation_quality(records: list[dict[str, bool]]) -> list[MetricResult]:
    if not records:
        return [
            _unavailable(name, "No citation judgments")
            for name in (
                "citation_validity_rate",
                "citation_to_bid_correctness",
                "citation_to_document_correctness",
                "evidence_support_rate",
                "unsupported_claim_rate",
                "citation_completeness",
                "invalid_citation_rejection_rate",
            )
        ]
    claim_records = [item for item in records if item.get("material_claim", True)]
    invalid = [item for item in records if not item.get("valid", False)]
    return [
        _boolean_rate(records, "valid", "citation_validity_rate"),
        _boolean_rate(records, "bid_matches", "citation_to_bid_correctness"),
        _boolean_rate(records, "document_matches", "citation_to_document_correctness"),
        _boolean_rate(records, "supports_claim", "evidence_support_rate"),
        _boolean_rate(
            records,
            "supports_claim",
            "unsupported_claim_rate",
            invert=True,
        ),
        _boolean_rate(claim_records, "has_citation", "citation_completeness"),
        _boolean_rate(
            invalid,
            "rejected",
            "invalid_citation_rejection_rate",
        ),
    ]


def workflow_reliability(
    executions: list[dict[str, Any]],
) -> list[MetricResult]:
    if not executions:
        return [
            _unavailable(name, "No workflow executions")
            for name in (
                "workflow_completion_rate",
                "workflow_timeout_rate",
                "profile_generation_success_rate",
                "human_review_flag_rate",
                "workflow_median_latency_ms",
                "workflow_p95_latency_ms",
            )
        ]
    total = len(executions)
    durations = sorted(
        float(item["duration_ms"])
        for item in executions
        if item.get("duration_ms") is not None
    )
    metrics = [
        _metric(
            "workflow_completion_rate",
            sum(item.get("status") in {"completed", "completed_with_warnings", "needs_review"} for item in executions),
            total,
        ),
        _metric(
            "workflow_timeout_rate",
            sum(item.get("error_category") == "timeout" for item in executions),
            total,
        ),
        _metric(
            "profile_generation_success_rate",
            sum(bool(item.get("profile_generated")) for item in executions),
            total,
        ),
        _metric(
            "human_review_flag_rate",
            sum(bool(item.get("requires_human_review")) for item in executions),
            total,
        ),
    ]
    if durations:
        median = (
            durations[len(durations) // 2]
            if len(durations) % 2
            else (
                durations[len(durations) // 2 - 1]
                + durations[len(durations) // 2]
            )
            / 2
        )
        p95 = durations[max(0, math.ceil(0.95 * len(durations)) - 1)]
        metrics.extend(
            [
                _value_metric(
                    "workflow_median_latency_ms", median, len(durations)
                ),
                _value_metric("workflow_p95_latency_ms", p95, len(durations)),
            ]
        )
    else:
        metrics.extend(
            [
                _unavailable("workflow_median_latency_ms", "No latency observations"),
                _unavailable("workflow_p95_latency_ms", "No latency observations"),
            ]
        )
    return metrics


def stage_outcome_rates(
    observations: list[dict[str, Any]]
) -> list[MetricResult]:
    stages = sorted({str(item["node_name"]) for item in observations})
    metrics: list[MetricResult] = []
    for stage in stages:
        rows = [item for item in observations if item["node_name"] == stage]
        durations = sorted(
            float(item["duration_ms"])
            for item in rows
            if item.get("duration_ms") is not None
        )
        metrics.append(
            _metric(
                f"stage.{stage}.success_rate",
                sum(item["status"] in {"completed", "skipped"} for item in rows),
                len(rows),
            )
        )
        metrics.append(
            _metric(
                f"stage.{stage}.failure_rate",
                sum(item["status"] == "failed" for item in rows),
                len(rows),
            )
        )
        if durations:
            median = (
                durations[len(durations) // 2]
                if len(durations) % 2
                else (
                    durations[len(durations) // 2 - 1]
                    + durations[len(durations) // 2]
                )
                / 2
            )
            p95 = durations[max(0, math.ceil(0.95 * len(durations)) - 1)]
            metrics.extend(
                [
                    _value_metric(
                        f"stage.{stage}.median_latency_ms",
                        median,
                        len(durations),
                    ),
                    _value_metric(
                        f"stage.{stage}.p95_latency_ms",
                        p95,
                        len(durations),
                    ),
                ]
            )
    return metrics


def _aware_utc(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _boolean_rate(
    rows: list[dict[str, bool]],
    key: str,
    name: str,
    *,
    invert: bool = False,
) -> MetricResult:
    if not rows:
        return _unavailable(name, "No applicable judgments")
    count = sum(bool(item.get(key)) != invert for item in rows)
    return _metric(name, count, len(rows))


def _metric(name: str, numerator: int | float, denominator: int | float) -> MetricResult:
    if denominator == 0:
        return _unavailable(name, "Denominator is zero")
    return MetricResult(
        name=name,
        status=MetricStatus.AVAILABLE,
        value=numerator / denominator,
        sample_count=int(denominator),
        numerator=numerator,
        denominator=denominator,
    )


def _value_metric(
    name: str,
    value: float | None,
    sample_count: int,
    numerator: int | float | None = None,
    explanation: str | None = None,
) -> MetricResult:
    return MetricResult(
        name=name,
        status=(
            MetricStatus.UNAVAILABLE if value is None else MetricStatus.AVAILABLE
        ),
        value=value,
        sample_count=sample_count,
        numerator=numerator,
        explanation=explanation,
    )


def _unavailable(name: str, explanation: str) -> MetricResult:
    return MetricResult(
        name=name,
        status=MetricStatus.UNAVAILABLE,
        sample_count=0,
        explanation=explanation,
    )


def _same_length(expected: list[Any], actual: list[Any]) -> None:
    if len(expected) != len(actual):
        raise ValueError("expected and actual values must have equal length")
