"""Regression gates for verified metrics and critical invariants."""

from app.evaluation.schemas import (
    GateStatus,
    MetricResult,
    MetricStatus,
    RegressionGateResult,
)


def minimum_metric_gate(
    metric: MetricResult, *, threshold: float, name: str | None = None
) -> RegressionGateResult:
    gate_name = name or f"{metric.name}>={threshold}"
    if metric.status == MetricStatus.UNAVAILABLE or metric.value is None:
        return RegressionGateResult(
            name=gate_name,
            status=GateStatus.UNAVAILABLE,
            metric_name=metric.name,
            threshold=threshold,
            explanation="Metric is unavailable; the gate is not reported as passed",
        )
    passed = metric.value >= threshold
    return RegressionGateResult(
        name=gate_name,
        status=GateStatus.PASSED if passed else GateStatus.FAILED,
        metric_name=metric.name,
        threshold=threshold,
        observed=metric.value,
        explanation=(
            "Observed metric meets the verified threshold"
            if passed
            else "Observed metric is below the verified threshold"
        ),
    )


def invariant_gate(
    name: str, *, passed: bool, explanation: str
) -> RegressionGateResult:
    return RegressionGateResult(
        name=name,
        status=GateStatus.PASSED if passed else GateStatus.FAILED,
        observed=1.0 if passed else 0.0,
        threshold=1.0,
        explanation=explanation,
    )
