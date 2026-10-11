"""Serializable state for a future orchestrated workflow.

This model holds data only. It does not hold database sessions, Qdrant
clients, or model clients, and it does not run any agent.
"""

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.addendum import AddendumChange
from app.schemas.common import ErrorDetail
from app.schemas.extraction import BidExtraction
from app.schemas.retrieval import RetrievedChunk
from app.schemas.validation import ValidationResult


class AgentStep(str, Enum):
    START = "start"
    RETRIEVE = "retrieve"
    EXTRACT = "extract"
    RECONCILE = "reconcile"
    VALIDATE = "validate"
    COMPLETE = "complete"


class AgentState(BaseModel):
    """The data a later orchestrator can pass between steps."""

    bid_id: UUID | None = None
    query: str | None = None
    current_step: AgentStep = AgentStep.START
    retrieved_chunks: list[RetrievedChunk] = Field(default_factory=list)
    extracted_fields: BidExtraction | None = None
    addendum_changes: list[AddendumChange] = Field(default_factory=list)
    validation_result: ValidationResult | None = None
    errors: list[ErrorDetail] = Field(default_factory=list)
