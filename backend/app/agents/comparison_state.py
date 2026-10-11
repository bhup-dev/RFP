"""Checkpoint-serializable state for the dedicated comparison StateGraph."""

from datetime import datetime
from typing import Any, TypedDict
from uuid import UUID

from app.schemas.comparison import BidComparisonRequest


class BidComparisonState(TypedDict):
    comparison_id: UUID
    request: dict[str, Any]
    status: str
    current_stage: str
    stage_statuses: dict[str, str]
    profiles: list[dict[str, Any]]
    profile_references: list[dict[str, Any]]
    comparison_rows: list[dict[str, Any]]
    semantic_attributes: list[str]
    warnings: list[str]
    errors: list[str]
    error_code: str | None
    requires_human_review: bool
    review_item_ids: list[UUID]
    review_status: str
    started_at: datetime
    completed_at: datetime | None


COMPARISON_STAGES = (
    "validate_comparison_request",
    "load_profiles",
    "compare_structured_fields",
    "compare_semantic_requirements",
    "validate_comparison_evidence",
    "create_comparison_review_items",
    "comparison_human_review_gate",
    "finalize_comparison",
)


def initial_comparison_state(
    comparison_id: UUID,
    request: BidComparisonRequest,
    started_at: datetime,
) -> BidComparisonState:
    return {
        "comparison_id": comparison_id,
        "request": request.model_dump(mode="json"),
        "status": "processing",
        "current_stage": "validate_comparison_request",
        "stage_statuses": {stage: "pending" for stage in COMPARISON_STAGES},
        "profiles": [],
        "profile_references": [],
        "comparison_rows": [],
        "semantic_attributes": [],
        "warnings": [],
        "errors": [],
        "error_code": None,
        "requires_human_review": False,
        "review_item_ids": [],
        "review_status": "not_required",
        "started_at": started_at,
        "completed_at": None,
    }
