"""Validated contracts shared by specialized RFP intelligence agents."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.citation import Citation
from app.schemas.common import ConfidenceScore


class SpecializedAgentName(str, Enum):
    REQUIREMENTS = "requirements"
    PRODUCT_SPECIFICATION = "product_specification"
    COMMERCIAL_TERMS = "commercial_terms"
    COMPLIANCE_DOCUMENTATION = "compliance_documentation"
    EVIDENCE_REVIEW = "evidence_review"


class AgentExecutionStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class FindingClassification(str, Enum):
    MANDATORY = "mandatory"
    OPTIONAL = "optional"
    INFORMATIONAL = "informational"
    EXPLICIT = "explicit"
    INTERPRETATION = "interpretation"
    UNCLEAR = "unclear"


class LLMSpecializedFinding(BaseModel):
    """Untrusted provider finding before chunk IDs are resolved."""

    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(min_length=1, max_length=4000)
    category: str = Field(min_length=1, max_length=128)
    classification: FindingClassification
    attributes: dict[str, Any] = Field(default_factory=dict)
    chunk_ids: list[UUID] = Field(default_factory=list, max_length=20)
    confidence: ConfidenceScore | None = None
    unresolved_questions: list[str] = Field(default_factory=list, max_length=10)
    notes: str | None = Field(default=None, max_length=2000)


class LLMSpecializedAgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[LLMSpecializedFinding] = Field(default_factory=list, max_length=50)
    warnings: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def finding_ids_are_unique(self) -> "LLMSpecializedAgentOutput":
        references = [item.finding_id for item in self.findings]
        if len(references) != len(set(references)):
            raise ValueError("finding_id values must be unique")
        return self


class SpecializedFinding(BaseModel):
    finding_id: str
    title: str
    description: str
    category: str
    classification: FindingClassification
    attributes: dict[str, Any] = Field(default_factory=dict)
    citations: list[Citation] = Field(default_factory=list)
    confidence: ConfidenceScore | None = None
    unresolved_questions: list[str] = Field(default_factory=list)
    notes: str | None = None
    requires_human_review: bool = False


class SpecializedAgentInput(BaseModel):
    workflow_id: UUID
    bid_id: UUID
    document_ids: list[UUID]
    workflow_objective: str = "Produce evidence-grounded bid intelligence"
    prior_findings: list[SpecializedFinding] = Field(default_factory=list)


class SpecializedAgentResult(BaseModel):
    run_id: UUID
    workflow_id: UUID
    bid_id: UUID
    agent_name: SpecializedAgentName
    status: AgentExecutionStatus
    findings: list[SpecializedFinding] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    evidence_chunk_count: int = Field(default=0, ge=0)
    model_name: str | None = None
    started_at: datetime
    completed_at: datetime


class EvidenceIssueType(str, Enum):
    MISSING_EVIDENCE = "missing_evidence"
    INVALID_CITATION = "invalid_citation"
    CROSS_BID_CITATION = "cross_bid_citation"
    DUPLICATE_FINDING = "duplicate_finding"
    CONFLICTING_FINDINGS = "conflicting_findings"
    UNRESOLVED_QUESTION = "unresolved_question"


class EvidenceReviewIssue(BaseModel):
    issue_type: EvidenceIssueType
    agent_name: SpecializedAgentName
    finding_id: str
    message: str
    related_finding_ids: list[str] = Field(default_factory=list)
    requires_human_review: bool = True


class EvidenceReviewResult(BaseModel):
    run_id: UUID
    workflow_id: UUID
    bid_id: UUID
    agent_name: SpecializedAgentName = SpecializedAgentName.EVIDENCE_REVIEW
    status: AgentExecutionStatus
    issues: list[EvidenceReviewIssue] = Field(default_factory=list)
    reviewed_finding_count: int = Field(ge=0)
    valid_citation_count: int = Field(ge=0)
    invalid_citation_count: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime
