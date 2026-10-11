"""Pydantic contracts for evaluation datasets, metrics, reports, and gates."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class EvaluationTask(str, Enum):
    EXTRACTION = "extraction"
    CITATION = "citation"
    ADDENDUM = "addendum"
    SPECIALIZED_AGENT = "specialized_agent"
    COMPARISON = "comparison"
    RETRIEVAL = "retrieval"
    WORKFLOW = "workflow"


class GroundTruthOrigin(str, Enum):
    SYNTHETIC_SOURCE_AUTHORED = "synthetic_source_authored"
    REAL_SOURCE_AUTHORED = "real_source_authored"


class EvaluationCase(BaseModel):
    case_id: str = Field(min_length=1)
    fixture_identifier: str = Field(min_length=1)
    task: EvaluationTask
    source_references: list[str] = Field(default_factory=list)
    input: dict[str, Any] = Field(default_factory=dict)
    expected: dict[str, Any]
    mock_output: dict[str, Any] | None = None
    ground_truth_origin: GroundTruthOrigin
    notes: str = Field(min_length=1)


class EvaluationDataset(BaseModel):
    dataset_name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = Field(min_length=1)
    cases: list[EvaluationCase] = Field(min_length=1)

    @model_validator(mode="after")
    def case_ids_are_unique(self) -> "EvaluationDataset":
        identifiers = [item.case_id for item in self.cases]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("evaluation case IDs must be unique")
        return self


class MetricStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class MetricResult(BaseModel):
    name: str
    status: MetricStatus
    value: float | None = None
    sample_count: int = Field(ge=0)
    numerator: int | float | None = None
    denominator: int | float | None = None
    explanation: str | None = None


class GateStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class RegressionGateResult(BaseModel):
    name: str
    status: GateStatus
    metric_name: str | None = None
    threshold: float | None = None
    observed: float | None = None
    explanation: str


class EvaluationReport(BaseModel):
    evaluation_run_id: str
    dataset_name: str
    dataset_version: str
    workflow_version: str
    execution_timestamp: datetime
    cases_attempted: int
    cases_completed: int
    cases_failed: int
    cases_skipped: int
    cases_unavailable: int
    metrics: list[MetricResult]
    gates: list[RegressionGateResult]
    significant_failures: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    case_references: list[str] = Field(default_factory=list)
    result_categories: dict[str, Any]
