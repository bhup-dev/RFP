"""API contracts for LangGraph bid-intelligence workflows."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.agents.state import BidIntelligenceState


class WorkflowStageResult(BaseModel):
    name: str
    status: str


class BidWorkflowResponse(BaseModel):
    workflow_id: UUID
    workflow_version: str
    bid_id: UUID
    status: str
    current_stage: str
    stages: list[WorkflowStageResult]
    document_ids: list[UUID] = Field(default_factory=list)
    eligible_document_count: int
    extraction_run_id: UUID | None = None
    addendum_analysis_run_id: UUID | None = None
    validation_run_id: UUID | None = None
    profile_id: UUID | None = None
    extraction_summary: dict[str, Any] = Field(default_factory=dict)
    addendum_summary: dict[str, Any] = Field(default_factory=dict)
    validation_summary: dict[str, Any] = Field(default_factory=dict)
    profile_status: str | None = None
    agent_statuses: dict[str, str] = Field(default_factory=dict)
    finding_counts: dict[str, int] = Field(default_factory=dict)
    unresolved_conflicts: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    requires_human_review: bool
    started_at: datetime
    completed_at: datetime | None = None

    @classmethod
    def from_state(cls, state: BidIntelligenceState) -> "BidWorkflowResponse":
        return cls(
            workflow_id=state["workflow_id"],
            workflow_version=state["workflow_version"],
            bid_id=state["bid_id"],
            status=state["workflow_status"],
            current_stage=state["current_stage"],
            stages=[
                WorkflowStageResult(name=name, status=status)
                for name, status in state["stage_statuses"].items()
            ],
            document_ids=state["document_ids"],
            eligible_document_count=state["eligible_document_count"],
            extraction_run_id=state["extraction_run_id"],
            addendum_analysis_run_id=state["addendum_analysis_run_id"],
            validation_run_id=state["validation_run_id"],
            profile_id=state["profile_id"],
            extraction_summary=state["extraction_summary"],
            addendum_summary=state["addendum_summary"],
            validation_summary=state["validation_summary"],
            profile_status=state["profile_status"],
            agent_statuses=state["agent_statuses"],
            finding_counts={
                "requirements": len(state["requirements_findings"]),
                "product_specification": len(state["product_findings"]),
                "commercial_terms": len(state["commercial_findings"]),
                "compliance_documentation": len(state["compliance_findings"]),
                "evidence_review": len(state["evidence_review_findings"]),
            },
            unresolved_conflicts=[
                item
                for item in state["evidence_review_findings"]
                if item.get("issue_type") == "conflicting_findings"
            ],
            warnings=state["warnings"],
            errors=state["errors"],
            requires_human_review=state["requires_human_review"],
            started_at=state["started_at"],
            completed_at=state["completed_at"],
        )
