import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.evaluation.dataset import DEFAULT_DATASET, load_dataset
from app.evaluation.gates import minimum_metric_gate
from app.evaluation.metrics import (
    binary_label_metrics,
    citation_quality,
    classification_accuracy,
    completeness,
    datetime_correctness,
    exact_match_rate,
    field_level_accuracy,
    missing_value_accuracy,
    set_precision_recall_f1,
    stage_outcome_rates,
    workflow_reliability,
)
from app.evaluation.runner import run_evaluation
from app.evaluation.schemas import GateStatus, MetricStatus
from app.observability.instrumentation import (
    NodeObservation,
    classify_error,
    instrument_node,
)


def test_exact_match_field_accuracy_missing_and_completeness():
    assert exact_match_rate(["A", "B"], ["A", "C"]).value == 0.5
    assert field_level_accuracy(
        [{"Bid Number": "A", "Payment Terms": None}],
        [{"Bid Number": "A", "Payment Terms": None}],
    ).value == 1.0
    assert missing_value_accuracy([None, "x"], [None, None]).value == 0.5
    assert completeness([None, "x", 0]).value == 2 / 3


def test_precision_recall_f1_and_zero_denominators_are_explicit():
    metrics = {
        item.name: item
        for item in set_precision_recall_f1(
            {"a", "b"}, {"b", "c"}, prefix="list"
        )
    }
    assert metrics["list_precision"].value == 0.5
    assert metrics["list_recall"].value == 0.5
    assert metrics["list_f1"].value == 0.5
    unavailable = set_precision_recall_f1([], [], prefix="empty")
    assert all(item.status == MetricStatus.UNAVAILABLE for item in unavailable)


def test_datetime_normalization_requires_timezone_and_compares_instants():
    metric = datetime_correctness(
        ["2027-01-15T14:00:00-05:00", "2027-01-15T14:00:00"],
        ["2027-01-15T19:00:00Z", "2027-01-15T14:00:00Z"],
    )
    assert metric.value == 1.0
    assert metric.sample_count == 1
    unavailable = datetime_correctness(
        ["2027-01-15T14:00:00"], ["2027-01-15T14:00:00"]
    )
    assert unavailable.status == MetricStatus.UNAVAILABLE


def test_citation_validity_is_separate_from_evidence_support():
    metrics = {
        item.name: item
        for item in citation_quality(
            [
                {
                    "material_claim": True,
                    "has_citation": True,
                    "valid": True,
                    "bid_matches": True,
                    "document_matches": True,
                    "supports_claim": False,
                    "rejected": False,
                },
                {
                    "material_claim": True,
                    "has_citation": True,
                    "valid": False,
                    "bid_matches": False,
                    "document_matches": True,
                    "supports_claim": False,
                    "rejected": True,
                }
            ]
        )
    }
    assert metrics["citation_validity_rate"].value == 0.5
    assert metrics["evidence_support_rate"].value == 0.0
    assert metrics["unsupported_claim_rate"].value == 1.0
    assert metrics["citation_completeness"].value == 1.0
    assert metrics["invalid_citation_rejection_rate"].value == 1.0


def test_conflict_and_comparison_classification_metrics():
    accuracy = classification_accuracy(
        ["conflict", "clarification"],
        ["conflict", "conflict"],
        name="addendum_accuracy",
    )
    assert accuracy.value == 0.5
    conflict = {
        item.name: item
        for item in binary_label_metrics(
            ["conflict", "clarification"],
            ["conflict", "conflict"],
            positive_labels={"conflict"},
            prefix="conflict",
        )
    }
    assert conflict["conflict_precision"].value == 0.5
    assert conflict["conflict_recall"].value == 1.0


def test_workflow_reliability_denominators_and_latency_percentiles():
    metrics = {
        item.name: item
        for item in workflow_reliability(
            [
                {
                    "status": "completed",
                    "duration_ms": 100,
                    "profile_generated": True,
                    "requires_human_review": False,
                    "error_category": None,
                },
                {
                    "status": "failed",
                    "duration_ms": 300,
                    "profile_generated": False,
                    "requires_human_review": True,
                    "error_category": "timeout",
                },
                {
                    "status": "needs_review",
                    "duration_ms": 200,
                    "profile_generated": True,
                    "requires_human_review": True,
                    "error_category": None,
                },
            ]
        )
    }
    assert metrics["workflow_completion_rate"].value == 2 / 3
    assert metrics["workflow_timeout_rate"].value == 1 / 3
    assert metrics["workflow_median_latency_ms"].value == 200
    assert metrics["workflow_p95_latency_ms"].value == 300
    assert metrics["workflow_completion_rate"].sample_count == 3


def test_stage_outcome_rates_include_failed_attempts():
    metrics = {
        item.name: item
        for item in stage_outcome_rates(
            [
                {"node_name": "extract", "status": "completed", "duration_ms": 10},
                {"node_name": "extract", "status": "failed", "duration_ms": 30},
            ]
        )
    }
    assert metrics["stage.extract.success_rate"].value == 0.5
    assert metrics["stage.extract.failure_rate"].value == 0.5
    assert metrics["stage.extract.median_latency_ms"].value == 20
    assert metrics["stage.extract.p95_latency_ms"].value == 30


def test_empty_metrics_and_unavailable_gate_never_claim_pass():
    metric = exact_match_rate([], [])
    assert metric.status == MetricStatus.UNAVAILABLE
    gate = minimum_metric_gate(metric, threshold=0.9)
    assert gate.status == GateStatus.UNAVAILABLE
    with pytest.raises(ValueError, match="equal length"):
        exact_match_rate([1], [])


def test_dataset_validation_and_report_serialization(tmp_path: Path):
    dataset = load_dataset(DEFAULT_DATASET)
    assert dataset.version == "1.0.0"
    assert all(
        item.ground_truth_origin.value == "synthetic_source_authored"
        for item in dataset.cases
    )
    report = run_evaluation(dataset)
    body = json.loads(report.model_dump_json())
    assert body["cases_attempted"] == len(dataset.cases)
    assert body["result_categories"]["live_llm_evaluation"]["status"] == "not_run"
    assert body["result_categories"]["ground_truth_evaluation"][
        "real_source_case_count"
    ] == 0
    assert all(item.status == GateStatus.PASSED for item in report.gates)

    malformed = tmp_path / "bad.json"
    malformed.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed"):
        load_dataset(malformed)
    with pytest.raises(ValueError, match="Live evaluation"):
        run_evaluation(dataset, mode="live")


class Observer:
    def __init__(self, error=False):
        self.observations = []
        self.error = error

    async def record(self, observation):
        if self.error:
            raise RuntimeError("telemetry unavailable")
        self.observations.append(observation)


def _state():
    return {
        "workflow_id": uuid4(),
        "bid_id": uuid4(),
        "stage_statuses": {"node": "pending"},
        "errors": [],
        "requires_human_review": False,
        "extraction_run_id": None,
    }


def test_node_instrumentation_records_duration_status_and_references():
    observer = Observer()

    async def node(state):
        return {
            "stage_statuses": {"node": "completed"},
            "extraction_run_id": uuid4(),
        }

    result = asyncio.run(
        instrument_node(
            "node", node, observer, operation_type="bid_workflow"
        )(_state())
    )
    assert result["stage_statuses"]["node"] == "completed"
    observation = observer.observations[0]
    assert observation.status == "completed"
    assert observation.duration_ms >= 0
    assert observation.run_references["extraction_run_id"] == str(
        result["extraction_run_id"]
    )


def test_instrumentation_failure_does_not_corrupt_business_node():
    observer = Observer(error=True)

    async def node(_state):
        return {"stage_statuses": {"node": "completed"}, "value": 42}

    result = asyncio.run(
        instrument_node(
            "node", node, observer, operation_type="bid_workflow"
        )(_state())
    )
    assert result["value"] == 42


def test_error_categorization_is_deterministic_and_sanitized_by_wrapper():
    assert classify_error(TimeoutError()) == "timeout"
    assert classify_error(ValueError("bad input")) == "validation_error"
    assert classify_error(json.JSONDecodeError("bad", "x", 0)) == "invalid_structured_output"
