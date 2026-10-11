"""Serializable shared state for the bid-intelligence LangGraph."""

from datetime import datetime
from typing import Any, Literal, TypedDict
from uuid import UUID

WorkflowStatus = Literal[
    "running",
    "completed",
    "completed_with_warnings",
    "needs_review",
    "pending_review",
    "failed",
]
StageStatus = Literal["pending", "running", "completed", "skipped", "failed"]


class BidIntelligenceState(TypedDict):
    workflow_id: UUID
    workflow_version: str
    bid_id: UUID
    workflow_status: WorkflowStatus
    current_stage: str
    stage_statuses: dict[str, StageStatus]
    document_ids: list[UUID]
    eligible_document_count: int
    extraction_run_id: UUID | None
    addendum_analysis_run_id: UUID | None
    validation_run_id: UUID | None
    profile_id: UUID | None
    extraction_summary: dict[str, Any]
    addendum_summary: dict[str, Any]
    validation_summary: dict[str, Any]
    validation_findings: list[dict[str, Any]]
    profile_status: str | None
    requirements_findings: list[dict[str, Any]]
    product_findings: list[dict[str, Any]]
    commercial_findings: list[dict[str, Any]]
    compliance_findings: list[dict[str, Any]]
    evidence_review_findings: list[dict[str, Any]]
    agent_statuses: dict[str, str]
    agent_warnings: list[str]
    agent_errors: list[str]
    review_item_ids: list[UUID]
    review_status: str
    warnings: list[str]
    errors: list[str]
    error_code: str | None
    requires_human_review: bool
    started_at: datetime
    completed_at: datetime | None


STAGES = (
    "validate_bid_context",
    "extract_rfp_fields",
    "analyze_addenda",
    "requirements_agent",
    "product_specification_agent",
    "commercial_terms_agent",
    "compliance_documentation_agent",
    "evidence_review_agent",
    "validate_extraction",
    "create_review_items",
    "human_review_gate",
    "generate_profile",
    "finalize",
)


def initial_state(
    workflow_id: UUID,
    bid_id: UUID,
    workflow_version: str,
    started_at: datetime,
) -> BidIntelligenceState:
    """Create a complete state with deterministic, checkpoint-safe defaults."""
    return {
        "workflow_id": workflow_id,
        "workflow_version": workflow_version,
        "bid_id": bid_id,
        "workflow_status": "running",
        "current_stage": "validate_bid_context",
        "stage_statuses": {stage: "pending" for stage in STAGES},
        "document_ids": [],
        "eligible_document_count": 0,
        "extraction_run_id": None,
        "addendum_analysis_run_id": None,
        "validation_run_id": None,
        "profile_id": None,
        "extraction_summary": {},
        "addendum_summary": {},
        "validation_summary": {},
        "validation_findings": [],
        "profile_status": None,
        "requirements_findings": [],
        "product_findings": [],
        "commercial_findings": [],
        "compliance_findings": [],
        "evidence_review_findings": [],
        "agent_statuses": {
            "requirements": "pending",
            "product_specification": "pending",
            "commercial_terms": "pending",
            "compliance_documentation": "pending",
            "evidence_review": "pending",
        },
        "agent_warnings": [],
        "agent_errors": [],
        "review_item_ids": [],
        "review_status": "not_required",
        "warnings": [],
        "errors": [],
        "error_code": None,
        "requires_human_review": False,
        "started_at": started_at,
        "completed_at": None,
    }
