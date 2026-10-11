"""CLI and reusable runner for versioned deterministic evaluation datasets."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.config import settings
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
from app.evaluation.reports import concise_summary
from app.evaluation.schemas import (
    EvaluationDataset,
    EvaluationReport,
    EvaluationTask,
    MetricResult,
    MetricStatus,
)
from app.retrieval.evaluation import EvaluationQuery, RelevantSource, evaluate
from app.schemas.retrieval import RetrievedChunk


def run_evaluation(
    dataset: EvaluationDataset, *, mode: str = "mock"
) -> EvaluationReport:
    if mode != "mock":
        raise ValueError(
            "Live evaluation requires an explicit service adapter and is not configured"
        )
    completed = [item for item in dataset.cases if item.mock_output is not None]
    unavailable = [item for item in dataset.cases if item.mock_output is None]
    metrics: list[MetricResult] = []

    extraction = [
        item for item in completed if item.task == EvaluationTask.EXTRACTION
    ]
    expected_fields = [item.expected["fields"] for item in extraction]
    actual_fields = [item.mock_output["fields"] for item in extraction]  # type: ignore[index]
    flat_expected = [value for row in expected_fields for value in row.values()]
    flat_actual = [
        actual.get(key)
        for expected, actual in zip(expected_fields, actual_fields, strict=True)
        for key in expected
    ]
    metrics.extend(
        [
            exact_match_rate(flat_expected, flat_actual),
            field_level_accuracy(expected_fields, actual_fields),
            missing_value_accuracy(flat_expected, flat_actual),
            completeness(flat_actual),
        ]
    )
    date_expected, date_actual = [], []
    identifier_expected, identifier_actual = [], []
    for expected, actual in zip(expected_fields, actual_fields, strict=True):
        if "Due Date" in expected:
            date_expected.append(expected["Due Date"])
            date_actual.append(actual.get("Due Date"))
        for name in ("Bid Number", "Model_no", "Part_no"):
            if name in expected:
                identifier_expected.append(expected[name])
                identifier_actual.append(actual.get(name))
    metrics.append(datetime_correctness(date_expected, date_actual))
    identifier_metric = exact_match_rate(identifier_expected, identifier_actual)
    identifier_metric.name = "exact_identifier_match_rate"
    metrics.append(identifier_metric)

    citation_records = [
        record
        for item in completed
        if item.task in {EvaluationTask.CITATION, EvaluationTask.SPECIALIZED_AGENT}
        for record in item.mock_output.get("records", [])  # type: ignore[union-attr]
    ]
    metrics.extend(citation_quality(citation_records))

    addendum = [item for item in completed if item.task == EvaluationTask.ADDENDUM]
    addendum_expected = [
        label for item in addendum for label in item.expected["labels"]
    ]
    addendum_actual = [
        label for item in addendum for label in item.mock_output["labels"]  # type: ignore[index]
    ]
    metrics.append(
        classification_accuracy(
            addendum_expected,
            addendum_actual,
            name="addendum_classification_accuracy",
        )
    )
    metrics.extend(
        binary_label_metrics(
            addendum_expected,
            addendum_actual,
            positive_labels={"conflict"},
            prefix="conflict_detection",
        )
    )
    metrics.extend(
        binary_label_metrics(
            addendum_expected,
            addendum_actual,
            positive_labels={"modified_requirement", "removed_requirement"},
            prefix="modified_removed_detection",
        )
    )

    specialized = [
        item for item in completed if item.task == EvaluationTask.SPECIALIZED_AGENT
    ]
    metrics.extend(
        set_precision_recall_f1(
            [
                value
                for item in specialized
                for value in item.expected.get("finding_ids", [])
            ],
            [
                value
                for item in specialized
                for value in item.mock_output.get("finding_ids", [])  # type: ignore[union-attr]
            ],
            prefix="specialized_finding",
        )
    )

    comparisons = [
        item for item in completed if item.task == EvaluationTask.COMPARISON
    ]
    metrics.append(
        classification_accuracy(
            [
                value
                for item in comparisons
                for value in item.expected["classifications"]
            ],
            [
                value
                for item in comparisons
                for value in item.mock_output["classifications"]  # type: ignore[index]
            ],
            name="comparison_classification_accuracy",
        )
    )

    metrics.extend(_retrieval_metrics(completed))
    workflows = [
        execution
        for item in completed
        if item.task == EvaluationTask.WORKFLOW
        for execution in item.mock_output.get("executions", [])  # type: ignore[union-attr]
    ]
    metrics.extend(workflow_reliability(workflows))
    observations = [
        observation
        for item in completed
        if item.task == EvaluationTask.WORKFLOW
        for observation in item.mock_output.get("node_observations", [])  # type: ignore[union-attr]
    ]
    metrics.extend(stage_outcome_rates(observations))

    by_name = {item.name: item for item in metrics}
    gates = [
        minimum_metric_gate(
            by_name["exact_identifier_match_rate"],
            threshold=settings.evaluation_exact_identifier_min,
            name="exact_identifiers_unchanged",
        ),
        minimum_metric_gate(
            by_name["invalid_citation_rejection_rate"],
            threshold=settings.evaluation_invalid_citation_rejection_min,
            name="invalid_citations_rejected",
        ),
        minimum_metric_gate(
            by_name["conflict_detection_recall"],
            threshold=settings.evaluation_conflict_recall_min,
            name="known_critical_conflicts_retained",
        ),
    ]
    failures = [
        item.case_id
        for item in completed
        if item.mock_output is None
    ]
    origins = sorted({item.ground_truth_origin.value for item in dataset.cases})
    return EvaluationReport(
        evaluation_run_id=str(uuid4()),
        dataset_name=dataset.dataset_name,
        dataset_version=dataset.version,
        workflow_version=settings.langgraph_workflow_version,
        execution_timestamp=datetime.now(UTC),
        cases_attempted=len(dataset.cases),
        cases_completed=len(completed),
        cases_failed=len(failures),
        cases_skipped=0,
        cases_unavailable=len(unavailable),
        metrics=metrics,
        gates=gates,
        significant_failures=failures,
        limitations=[
            "All bundled cases are synthetic fixtures; results are not real-world accuracy.",
            "Mock mode evaluates deterministic fixture outputs and makes no LLM calls.",
            "No Bid1/Bid2 assignment documents were available for independent labeling.",
        ],
        case_references=[item.case_id for item in dataset.cases],
        result_categories={
            "unit_tests": {"status": "reported separately by pytest"},
            "integration_tests": {"status": "reported separately by pytest"},
            "ground_truth_evaluation": {
                "origins": origins,
                "real_source_case_count": sum(
                    item.ground_truth_origin.value == "real_source_authored"
                    for item in dataset.cases
                ),
            },
            "synthetic_fixture_evaluation": {
                "case_count": len(dataset.cases),
                "mode": mode,
            },
            "live_llm_evaluation": {"status": "not_run"},
        },
    )


def _retrieval_metrics(cases: list) -> list[MetricResult]:
    retrieval = [item for item in cases if item.task == EvaluationTask.RETRIEVAL]
    if not retrieval:
        return [
            MetricResult(
                name=name,
                status=MetricStatus.UNAVAILABLE,
                sample_count=0,
                explanation="No retrieval cases",
            )
            for name in (
                "retrieval_recall_at_k",
                "retrieval_precision_at_k",
                "retrieval_mrr",
                "retrieval_hit_rate_at_k",
                "retrieval_ndcg_at_k",
            )
        ]
    query_models, retrieved = [], {}
    k = min(int(item.input.get("k", 5)) for item in retrieval)
    for item in retrieval:
        query_models.append(
            EvaluationQuery(
                query_id=item.case_id,
                query=item.input["query"],
                relevant=[
                    RelevantSource(chunk_id=UUID(value))
                    for value in item.expected["relevant_chunk_ids"]
                ],
            )
        )
        retrieved[item.case_id] = [
            RetrievedChunk(
                bid_id=UUID(int=1),
                document_id=UUID(int=2),
                chunk_id=UUID(value),
                file_name="synthetic.txt",
                text=f"synthetic chunk {value}",
            )
            for value in item.mock_output["retrieved_chunk_ids"]
        ]
    report = evaluate(
        query_models,
        retrieved,
        k=k,
        configuration={"source": "app.retrieval.evaluation"},
    )
    names = {
        "recall_at_k": "retrieval_recall_at_k",
        "precision_at_k": "retrieval_precision_at_k",
        "mrr": "retrieval_mrr",
        "hit_rate_at_k": "retrieval_hit_rate_at_k",
        "ndcg_at_k": "retrieval_ndcg_at_k",
    }
    return [
        MetricResult(
            name=output,
            status=(
                MetricStatus.AVAILABLE
                if report["metrics"][source] is not None
                else MetricStatus.UNAVAILABLE
            ),
            value=report["metrics"][source],
            sample_count=report["judged_query_count"],
            explanation="Reused app.retrieval.evaluation.evaluate",
        )
        for source, output in names.items()
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run RFP quality evaluation")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--mode", choices=("mock", "live"), default="mock")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = run_evaluation(load_dataset(args.dataset), mode=args.mode)
    except ValueError as exc:
        parser.error(str(exc))
    rendered = report.model_dump_json(indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(concise_summary(report))
    if args.output:
        print(f"JSON report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
