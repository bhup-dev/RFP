"""Human review items, decisions, and audit responses."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.citation import Citation
from app.schemas.common import ConfidenceScore, JsonValue


class ReviewCategory(str, Enum):
    UNSUPPORTED_CLAIM = "unsupported_claim"
    INVALID_CITATION = "invalid_citation"
    CONFLICTING_REQUIREMENT = "conflicting_requirement"
    AMBIGUOUS_DEADLINE = "ambiguous_deadline"
    UNCERTAIN_PRODUCT_SPECIFICATION = "uncertain_product_specification"
    INCONSISTENT_AGENT_FINDINGS = "inconsistent_agent_findings"
    INCOMPLETE_PROFILE = "incomplete_profile"
    COMPARISON_UNCERTAINTY = "comparison_uncertainty"
    OTHER = "other"


class ReviewSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ReviewStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    UNRESOLVED = "unresolved"
    REEVALUATION_REQUESTED = "reevaluation_requested"


class ReviewDecisionType(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_RE_EVALUATION = "request_re_evaluation"
    MARK_UNRESOLVED = "mark_unresolved"


class ReviewItemRead(BaseModel):
    review_item_id: UUID
    operation_type: str
    bid_id: UUID
    workflow_id: UUID
    source_stage: str
    category: ReviewCategory
    item_reference: str
    field_name: str | None = None
    proposed_value: JsonValue = None
    validation_status: str | None = None
    confidence_score: ConfidenceScore | None = None
    supporting_citations: list[Citation] = Field(default_factory=list)
    contradicting_citations: list[Citation] = Field(default_factory=list)
    reason: str
    severity: ReviewSeverity
    status: ReviewStatus
    created_at: datetime
    reviewer_id: str | None = None
    decision_at: datetime | None = None
    corrected_value: JsonValue = None
    reevaluation_attempts: int = 0


class ReviewDecisionRequest(BaseModel):
    decision: ReviewDecisionType
    idempotency_key: UUID
    comments: str | None = Field(default=None, max_length=4000)
    corrected_value: JsonValue = None

    @model_validator(mode="after")
    def decision_requirements(self) -> "ReviewDecisionRequest":
        if self.decision in {
            ReviewDecisionType.REJECT,
            ReviewDecisionType.REQUEST_RE_EVALUATION,
            ReviewDecisionType.MARK_UNRESOLVED,
        } and not (self.comments and self.comments.strip()):
            raise ValueError("comments are required for this decision")
        if self.corrected_value is not None and not (
            self.comments and self.comments.strip()
        ):
            raise ValueError("a corrected value requires reviewer comments")
        return self


class ReviewDecisionRead(BaseModel):
    decision_id: UUID
    review_item_id: UUID
    decision: ReviewDecisionType
    previous_status: ReviewStatus
    new_status: ReviewStatus
    reviewer_id: str
    comments: str | None = None
    corrected_value: JsonValue = None
    result_reference: dict[str, JsonValue] | None = None
    created_at: datetime
    duplicate: bool = False


class ReviewItemDetail(ReviewItemRead):
    decisions: list[ReviewDecisionRead] = Field(default_factory=list)


class ReviewDecisionResponse(BaseModel):
    item: ReviewItemRead
    decision: ReviewDecisionRead
    workflow_resumed: bool
    workflow_status: str | None = None
    detail: str | None = None


class WorkflowReviewStatus(BaseModel):
    workflow_id: UUID
    total: int
    pending: int
    approved: int
    rejected: int
    unresolved: int
    reevaluation_requested: int
    all_required_resolved: bool


class ReviewerContext(BaseModel):
    reviewer_id: str
