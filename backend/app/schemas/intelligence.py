"""Deterministic consolidated bid intelligence profile contracts."""

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.citation import Citation
from app.schemas.common import AwareDatetime, ConfidenceScore, JsonValue
from app.schemas.extraction import ASSIGNMENT_FIELD_NAMES


class ProfileFieldStatus(str, Enum):
    VALIDATED = "validated"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    CONFLICTING = "conflicting"
    NOT_FOUND = "not_found"
    NOT_STATED = "not_stated"
    NEEDS_REVIEW = "needs_review"
    UNVALIDATED = "unvalidated"


class IntelligenceProfileStatus(str, Enum):
    READY = "ready"
    READY_WITH_WARNINGS = "ready_with_warnings"
    NEEDS_REVIEW = "needs_review"
    INCOMPLETE = "incomplete"
    FAILED = "failed"


class ProfileAddendumChange(BaseModel):
    finding_id: UUID
    analysis_run_id: UUID
    classification: str
    topic: str
    original_value: JsonValue = None
    amended_value: JsonValue = None
    explanation: str | None = None
    severity: str | None = None
    original_sources: list[Citation] = Field(default_factory=list)
    addendum_sources: list[Citation] = Field(default_factory=list)


class IntelligenceProfileField(BaseModel):
    value: JsonValue = None
    validation_status: ProfileFieldStatus
    confidence_score: ConfidenceScore | None = None
    supporting_citations: list[Citation] = Field(default_factory=list)
    contradicting_citations: list[Citation] = Field(default_factory=list)
    extraction_notes: str | None = None
    review_recommendation: str
    addendum_changes: list[ProfileAddendumChange] = Field(default_factory=list)


class IntelligenceProfileMetadata(BaseModel):
    bid_id: UUID
    bid_identifier: str
    solicitation_number: str | None = None
    profile_id: UUID
    profile_version: int = Field(default=1, ge=1)
    generated_at: AwareDatetime
    extraction_run_id: UUID | None = None
    addendum_analysis_run_id: UUID | None = None
    validation_run_id: UUID | None = None
    source_document_count: int = Field(ge=0)
    source_document_ids: list[UUID] = Field(default_factory=list)
    status: IntelligenceProfileStatus
    review_required: bool
    warnings: list[str] = Field(default_factory=list)
    unresolved_issues: list[str] = Field(default_factory=list)


class BidIntelligenceProfile(BaseModel):
    metadata: IntelligenceProfileMetadata
    fields: dict[str, IntelligenceProfileField]

    @model_validator(mode="after")
    def exact_assignment_fields(self) -> "BidIntelligenceProfile":
        if tuple(self.fields) != ASSIGNMENT_FIELD_NAMES:
            raise ValueError("fields must contain the 20 assignment names in canonical order")
        return self


class IntelligenceWorkflowStageStatus(str, Enum):
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


class IntelligenceWorkflowStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"


class IntelligenceWorkflowStage(BaseModel):
    name: str
    status: IntelligenceWorkflowStageStatus
    run_id: UUID | None = None
    detail: str | None = None


class BidIntelligenceWorkflowResult(BaseModel):
    bid_id: UUID
    status: IntelligenceWorkflowStatus
    stages: list[IntelligenceWorkflowStage]
    profile: BidIntelligenceProfile | None = None
