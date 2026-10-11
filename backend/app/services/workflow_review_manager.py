"""Adapter between deterministic review rules and LangGraph nodes."""

from uuid import UUID

from app.agents.state import BidIntelligenceState
from app.agents.comparison_state import BidComparisonState
from app.services.review_guardrails import (
    comparison_review_candidates,
    review_candidates,
)
from app.services.review_service import ReviewService


class WorkflowReviewManager:
    def __init__(
        self,
        service: ReviewService,
        *,
        confidence_threshold: float,
        critical_fields: set[str],
    ) -> None:
        self.service = service
        self._confidence_threshold = confidence_threshold
        self._critical_fields = critical_fields

    async def create_items(self, state: BidIntelligenceState) -> list[UUID]:
        candidates = review_candidates(
            state,
            confidence_threshold=self._confidence_threshold,
            critical_fields=self._critical_fields,
        )
        if not candidates:
            return []
        return await self.service.create_items(
            state["workflow_id"], state["bid_id"], candidates
        )

    async def all_resolved(self, workflow_id: UUID) -> bool:
        return (await self.service.status(workflow_id)).all_required_resolved


class ComparisonReviewManager:
    def __init__(self, service: ReviewService) -> None:
        self.service = service

    async def create_items(self, state: BidComparisonState) -> list[UUID]:
        candidates = comparison_review_candidates(state)
        if not candidates:
            return []
        return await self.service.create_comparison_items(
            state["comparison_id"], candidates
        )

    async def all_resolved(self, comparison_id: UUID) -> bool:
        return (await self.service.status(comparison_id)).all_required_resolved
