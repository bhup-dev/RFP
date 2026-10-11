"""Addendum reconciliation and requirement-level analysis contracts."""

from datetime import date
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.citation import Citation
from app.schemas.common import AwareDatetime, ConfidenceScore, JsonValue


class AddendumChangeStatus(str, Enum):
    NEW = "new"
    AMENDED = "amended"
    UNCHANGED = "unchanged"
    CONFLICTING = "conflicting"
    UNRESOLVED = "unresolved"


class AddendumChange(BaseModel):
    """One field as it stood before and after an addendum."""

    field_name: str = Field(min_length=1, max_length=128)
    original_value: JsonValue = None
    amended_value: JsonValue = None
    status: AddendumChangeStatus
    notes: str | None = None
    original_sources: list[Citation] = Field(default_factory=list)
    amended_sources: list[Citation] = Field(default_factory=list)


class AddendumReconciliationResult(BaseModel):
    """Every detected change for one bid, including unresolved conflicts."""

    bid_id: UUID
    changes: list[AddendumChange] = Field(default_factory=list)


class AddendumFindingClassification(str, Enum):
    NEW_REQUIREMENT = "new_requirement"
    MODIFIED_REQUIREMENT = "modified_requirement"
    REMOVED_REQUIREMENT = "removed_requirement"
    CLARIFICATION = "clarification"
    CONFLICT = "conflict"
    AMBIGUOUS = "ambiguous"
    NO_CHANGE = "no_change"


class AddendumAnalysisStatus(str, Enum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class AddendumFindingSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    REVIEW = "review"


class AddendumDocumentRole(str, Enum):
    ORIGINAL = "original"
    ADDENDUM = "addendum"
    CLARIFICATION = "clarification"
    SUPPORTING = "supporting"
    OTHER = "other"


class AddendumDocumentInfo(BaseModel):
    document_id: UUID
    file_name: str = Field(min_length=1, max_length=512)
    role: AddendumDocumentRole
    document_type: str | None = Field(default=None, max_length=64)
    addendum_number: int | None = Field(default=None, ge=0)
    document_date: date | None = None


class LLMAddendumTopic(BaseModel):
    """Untrusted topic discovered from one amendment document."""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=128)
    retrieval_query: str = Field(min_length=1, max_length=500)
    addendum_chunk_ids: list[UUID] = Field(min_length=1, max_length=20)


class LLMAddendumTopics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topics: list[LLMAddendumTopic] = Field(default_factory=list, max_length=20)


class LLMAddendumFinding(BaseModel):
    """Untrusted requirement-level comparison before citation validation."""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=128)
    classification: AddendumFindingClassification
    original_value: JsonValue = None
    amended_value: JsonValue = None
    explanation: str = Field(min_length=1, max_length=4000)
    impact_note: str | None = Field(default=None, max_length=2000)
    severity: AddendumFindingSeverity | None = None
    confidence: ConfidenceScore | None = None
    original_chunk_ids: list[UUID] = Field(default_factory=list, max_length=20)
    addendum_chunk_ids: list[UUID] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def required_evidence_sides_are_present(self) -> "LLMAddendumFinding":
        paired = {
            AddendumFindingClassification.MODIFIED_REQUIREMENT,
            AddendumFindingClassification.REMOVED_REQUIREMENT,
            AddendumFindingClassification.CLARIFICATION,
            AddendumFindingClassification.CONFLICT,
            AddendumFindingClassification.NO_CHANGE,
        }
        if self.classification in paired and not self.original_chunk_ids:
            raise ValueError(f"{self.classification.value} requires original evidence")
        if (
            self.classification == AddendumFindingClassification.NEW_REQUIREMENT
            and self.original_chunk_ids
        ):
            raise ValueError("new_requirement must not claim an original source")
        return self


class LLMAddendumFindings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[LLMAddendumFinding] = Field(default_factory=list, max_length=50)


class AddendumFinding(BaseModel):
    """One persisted comparison with authoritative source citations."""

    id: UUID
    analysis_run_id: UUID
    bid_id: UUID
    original_document_id: UUID | None = None
    addendum_document_id: UUID
    topic: str = Field(min_length=1, max_length=128)
    classification: AddendumFindingClassification
    original_value: JsonValue = None
    amended_value: JsonValue = None
    explanation: str
    impact_note: str | None = None
    severity: AddendumFindingSeverity | None = None
    confidence: ConfidenceScore | None = None
    original_sources: list[Citation] = Field(default_factory=list)
    addendum_sources: list[Citation] = Field(min_length=1)
    created_at: AwareDatetime


class AddendumAnalysisResponse(BaseModel):
    run_id: UUID
    bid_id: UUID
    status: AddendumAnalysisStatus
    model_name: str
    original_documents: list[AddendumDocumentInfo]
    addendum_documents: list[AddendumDocumentInfo]
    relationship_uncertain: bool
    ordering_uncertain: bool
    findings: list[AddendumFinding] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    started_at: AwareDatetime
    completed_at: AwareDatetime
