"""Contracts for a future extraction validator.

Listing a citation here does not mean the citation exists or supports the claim.
"""

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.citation import Citation
from app.schemas.common import AwareDatetime, ConfidenceScore, JsonValue
from app.schemas.extraction import ASSIGNMENT_FIELD_NAMES


class ValidationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class IssueSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class IssueType(str, Enum):
    MISSING_REQUIRED = "missing_required"
    INVALID_FORMAT = "invalid_format"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    UNRESOLVED_CITATION = "unresolved_citation"
    CONFLICTING_VALUES = "conflicting_values"
    ADDENDUM_RECONCILIATION = "addendum_reconciliation"


class ValidationIssue(BaseModel):
    """One problem found while checking an extracted field."""

    field_name: str = Field(min_length=1, max_length=128)
    issue_type: IssueType
    message: str = Field(min_length=1)
    citations: list[Citation] = Field(default_factory=list)
    severity: IssueSeverity = IssueSeverity.ERROR


class ValidationResult(BaseModel):
    """Overall validation outcome and the issues behind it."""

    status: ValidationStatus
    issues: list[ValidationIssue] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class FieldValidationOutcome(str, Enum):
    VALIDATED = "validated"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    CONFLICTING = "conflicting"
    NOT_FOUND = "not_found"
    NOT_STATED = "not_stated"
    NEEDS_REVIEW = "needs_review"


class ValidationRunStatus(str, Enum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class OverallValidationStatus(str, Enum):
    VALIDATED = "validated"
    NEEDS_REVIEW = "needs_review"
    BLOCKED = "blocked"


class ConfidenceCategory(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNAVAILABLE = "unavailable"


class AddendumCheckStatus(str, Enum):
    CHECKED = "checked"
    NOT_RUN = "not_run"


class LLMEvidenceVerdict(BaseModel):
    """Bounded semantic verifier output before chunk-reference validation."""

    model_config = ConfigDict(extra="forbid")

    status: FieldValidationOutcome
    directness: ConfidenceScore
    completeness: ConfidenceScore
    supporting_chunk_ids: list[UUID] = Field(default_factory=list, max_length=20)
    contradicting_chunk_ids: list[UUID] = Field(default_factory=list, max_length=20)
    reasons: list[str] = Field(min_length=1, max_length=10)
    recommended_action: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def support_status_has_evidence(self) -> "LLMEvidenceVerdict":
        if self.status in {
            FieldValidationOutcome.VALIDATED,
            FieldValidationOutcome.PARTIALLY_SUPPORTED,
        } and not self.supporting_chunk_ids:
            raise ValueError("supported semantic verdicts require supporting chunks")
        return self


class ValidatedField(BaseModel):
    field_name: str = Field(min_length=1, max_length=128)
    extracted_value: JsonValue = None
    validation_status: FieldValidationOutcome
    confidence_score: ConfidenceScore | None = None
    confidence_category: ConfidenceCategory
    supporting_citations: list[Citation] = Field(default_factory=list)
    contradicting_citations: list[Citation] = Field(default_factory=list)
    validation_reasons: list[str] = Field(default_factory=list)
    recommended_action: str
    extraction_run_id: UUID
    validation_run_id: UUID
    deterministic_checks: dict[str, JsonValue] = Field(default_factory=dict)


class ValidationSummary(BaseModel):
    total_fields: int = Field(ge=0)
    validated: int = Field(ge=0)
    partially_supported: int = Field(ge=0)
    unsupported: int = Field(ge=0)
    conflicting: int = Field(ge=0)
    not_found: int = Field(ge=0)
    not_stated: int = Field(default=0, ge=0)
    needs_review: int = Field(ge=0)
    high_confidence: int = Field(ge=0)
    medium_confidence: int = Field(ge=0)
    low_confidence: int = Field(ge=0)
    unavailable_confidence: int = Field(ge=0)
    critical_unresolved_conflicts: list[str] = Field(default_factory=list)
    fields_recommended_for_review: list[str] = Field(default_factory=list)


class ExtractionValidationReport(BaseModel):
    validation_run_id: UUID
    extraction_run_id: UUID
    bid_id: UUID
    status: ValidationRunStatus
    overall_status: OverallValidationStatus
    addendum_check_status: AddendumCheckStatus
    addendum_analysis_run_id: UUID | None = None
    thresholds: dict[str, float]
    fields: dict[str, ValidatedField]
    summary: ValidationSummary
    warnings: list[str] = Field(default_factory=list)
    started_at: AwareDatetime
    completed_at: AwareDatetime

    @model_validator(mode="after")
    def exact_assignment_fields(self) -> "ExtractionValidationReport":
        if tuple(self.fields) != ASSIGNMENT_FIELD_NAMES:
            raise ValueError("fields must contain the 20 assignment names in canonical order")
        return self
