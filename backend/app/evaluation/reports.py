"""Human-readable rendering for structured evaluation reports."""

from app.evaluation.schemas import EvaluationReport, MetricStatus


def concise_summary(report: EvaluationReport) -> str:
    available = [
        item for item in report.metrics if item.status == MetricStatus.AVAILABLE
    ]
    lines = [
        f"Evaluation run: {report.evaluation_run_id}",
        f"Dataset: {report.dataset_name} v{report.dataset_version}",
        (
            "Cases: "
            f"{report.cases_completed} completed, {report.cases_failed} failed, "
            f"{report.cases_unavailable} unavailable"
        ),
        "Metrics:",
    ]
    lines.extend(
        f"  {item.name}: {item.value:.6f} (n={item.sample_count})"
        for item in available
        if item.value is not None
    )
    lines.append("Gates:")
    lines.extend(f"  {item.name}: {item.status.value}" for item in report.gates)
    if report.limitations:
        lines.append("Limitations:")
        lines.extend(f"  {item}" for item in report.limitations)
    return "\n".join(lines)
