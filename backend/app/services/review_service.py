"""Transactional human-review persistence and transition rules."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.orchestration import BidWorkflowRun
from app.db.models.comparison import BidComparisonProfile, BidComparisonRun
from app.db.models.review import HumanReviewDecision, HumanReviewItem
from app.schemas.citation import Citation
from app.schemas.review import (
    ReviewDecisionRead,
    ReviewDecisionRequest,
    ReviewDecisionType,
    ReviewItemRead,
    ReviewItemDetail,
    ReviewStatus,
    WorkflowReviewStatus,
)


class ReviewNotFound(RuntimeError):
    pass


class ReviewTransitionError(RuntimeError):
    pass


class ReviewWorkflowMismatch(RuntimeError):
    pass


class ReviewService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_items(
        self,
        workflow_id: UUID,
        bid_id: UUID,
        candidates: list[dict],
    ) -> list[UUID]:
        owner = await self._session.scalar(
            select(BidWorkflowRun.bid_id).where(BidWorkflowRun.id == workflow_id)
        )
        if owner != bid_id:
            raise ReviewWorkflowMismatch("workflow does not belong to bid")
        for candidate in candidates:
            statement = (
                insert(HumanReviewItem)
                .values(
                    operation_type="bid_workflow",
                    workflow_id=workflow_id,
                    bid_id=bid_id,
                    status=ReviewStatus.PENDING.value,
                    **candidate,
                )
                .on_conflict_do_nothing(
                    constraint="uq_human_review_items_workflow_reference"
                )
            )
            await self._session.execute(statement)
        await self._session.commit()
        return list(
            await self._session.scalars(
                select(HumanReviewItem.id)
                .where(HumanReviewItem.workflow_id == workflow_id)
                .order_by(HumanReviewItem.created_at, HumanReviewItem.id)
            )
        )

    async def create_comparison_items(
        self, comparison_id: UUID, candidates: list[dict]
    ) -> list[UUID]:
        comparison = await self._session.scalar(
            select(BidComparisonRun).where(BidComparisonRun.id == comparison_id)
        )
        if comparison is None:
            raise ReviewWorkflowMismatch("comparison does not exist")
        permitted_bids = set(
            await self._session.scalars(
                select(BidComparisonProfile.bid_id).where(
                    BidComparisonProfile.comparison_id == comparison_id
                )
            )
        )
        for candidate in candidates:
            values = dict(candidate)
            bid_id = values.pop("bid_id")
            if bid_id not in permitted_bids:
                raise ReviewWorkflowMismatch(
                    "comparison review item does not belong to a compared bid"
                )
            await self._session.execute(
                insert(HumanReviewItem)
                .values(
                    operation_type="bid_comparison",
                    workflow_id=comparison_id,
                    bid_id=bid_id,
                    status=ReviewStatus.PENDING.value,
                    **values,
                )
                .on_conflict_do_nothing(
                    constraint="uq_human_review_items_workflow_reference"
                )
            )
        if candidates:
            comparison.status = "pending_review"
        await self._session.commit()
        return list(
            await self._session.scalars(
                select(HumanReviewItem.id)
                .where(HumanReviewItem.workflow_id == comparison_id)
                .order_by(HumanReviewItem.created_at, HumanReviewItem.id)
            )
        )

    async def list_items(
        self,
        *,
        bid_id: UUID | None = None,
        workflow_id: UUID | None = None,
        status: ReviewStatus | None = None,
    ) -> list[ReviewItemRead]:
        query = select(HumanReviewItem).order_by(
            HumanReviewItem.created_at, HumanReviewItem.id
        )
        if bid_id is not None:
            query = query.where(HumanReviewItem.bid_id == bid_id)
        if workflow_id is not None:
            query = query.where(HumanReviewItem.workflow_id == workflow_id)
        if status is not None:
            query = query.where(HumanReviewItem.status == status.value)
        return [_item_read(item) for item in await self._session.scalars(query)]

    async def get_item(
        self, review_id: UUID, *, bid_id: UUID | None = None
    ) -> ReviewItemRead:
        query = select(HumanReviewItem).where(HumanReviewItem.id == review_id)
        if bid_id is not None:
            query = query.where(HumanReviewItem.bid_id == bid_id)
        item = await self._session.scalar(query)
        if item is None:
            raise ReviewNotFound("review item does not exist")
        return _item_read(item)

    async def get_item_detail(
        self, review_id: UUID, *, bid_id: UUID | None = None
    ) -> ReviewItemDetail:
        query = (
            select(HumanReviewItem)
            .options(selectinload(HumanReviewItem.decisions))
            .where(HumanReviewItem.id == review_id)
        )
        if bid_id is not None:
            query = query.where(HumanReviewItem.bid_id == bid_id)
        item = await self._session.scalar(query)
        if item is None:
            raise ReviewNotFound("review item does not exist")
        return ReviewItemDetail(
            **_item_read(item).model_dump(),
            decisions=[
                _decision_read(decision)
                for decision in sorted(
                    item.decisions, key=lambda value: (value.created_at, value.id)
                )
            ],
        )

    async def decide(
        self,
        review_id: UUID,
        request: ReviewDecisionRequest,
        reviewer_id: str,
    ) -> tuple[ReviewItemRead, ReviewDecisionRead]:
        item = await self._session.scalar(
            select(HumanReviewItem)
            .where(HumanReviewItem.id == review_id)
            .with_for_update()
        )
        if item is None:
            raise ReviewNotFound("review item does not exist")
        duplicate = await self._session.scalar(
            select(HumanReviewDecision).where(
                HumanReviewDecision.review_item_id == review_id,
                HumanReviewDecision.idempotency_key == request.idempotency_key,
            )
        )
        if duplicate is not None:
            return _item_read(item), _decision_read(duplicate, duplicate=True)
        if item.status != ReviewStatus.PENDING.value:
            raise ReviewTransitionError(
                f"review item is already {item.status}; only pending items may transition"
            )
        if (
            item.category == "invalid_citation"
            and request.decision == ReviewDecisionType.APPROVE
        ):
            raise ReviewTransitionError(
                "an invalid citation cannot be approved as valid evidence"
            )
        previous = ReviewStatus(item.status)
        new_status = {
            ReviewDecisionType.APPROVE: ReviewStatus.APPROVED,
            ReviewDecisionType.REJECT: ReviewStatus.REJECTED,
            ReviewDecisionType.REQUEST_RE_EVALUATION: ReviewStatus.REEVALUATION_REQUESTED,
            ReviewDecisionType.MARK_UNRESOLVED: ReviewStatus.UNRESOLVED,
        }[request.decision]
        now = datetime.now(UTC)
        item.status = new_status.value
        item.reviewer_id = reviewer_id
        item.corrected_value = request.corrected_value
        item.version += 1
        item.decision_at = (
            None
            if new_status == ReviewStatus.REEVALUATION_REQUESTED
            else now
        )
        decision = HumanReviewDecision(
            review_item_id=item.id,
            idempotency_key=request.idempotency_key,
            decision=request.decision.value,
            previous_status=previous.value,
            new_status=new_status.value,
            reviewer_id=reviewer_id,
            comments=request.comments,
            corrected_value=request.corrected_value,
            created_at=now,
        )
        self._session.add(decision)
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            duplicate = await self._session.scalar(
                select(HumanReviewDecision).where(
                    HumanReviewDecision.review_item_id == review_id,
                    HumanReviewDecision.idempotency_key == request.idempotency_key,
                )
            )
            if duplicate is None:
                raise
            item = await self._session.get(HumanReviewItem, review_id)
            assert item is not None
            return _item_read(item), _decision_read(duplicate, duplicate=True)
        await self._session.refresh(item)
        await self._session.refresh(decision)
        return _item_read(item), _decision_read(decision)

    async def reset_after_reevaluation(
        self, review_id: UUID, *, max_attempts: int
    ) -> ReviewItemRead:
        item = await self._session.scalar(
            select(HumanReviewItem)
            .where(HumanReviewItem.id == review_id)
            .with_for_update()
        )
        if item is None:
            raise ReviewNotFound("review item does not exist")
        if item.status != ReviewStatus.REEVALUATION_REQUESTED.value:
            raise ReviewTransitionError("review item is not awaiting re-evaluation")
        if item.reevaluation_attempts >= max_attempts:
            raise ReviewTransitionError("maximum re-evaluation attempts reached")
        item.reevaluation_attempts += 1
        item.status = ReviewStatus.PENDING.value
        item.decision_at = None
        item.version += 1
        await self._session.commit()
        await self._session.refresh(item)
        return _item_read(item)

    async def attach_reevaluation_result(
        self, decision_id: UUID, result_reference: dict
    ) -> ReviewDecisionRead:
        decision = await self._session.scalar(
            select(HumanReviewDecision)
            .where(HumanReviewDecision.id == decision_id)
            .with_for_update()
        )
        if (
            decision is None
            or decision.decision
            != ReviewDecisionType.REQUEST_RE_EVALUATION.value
        ):
            raise ReviewTransitionError("re-evaluation decision does not exist")
        if (
            decision.result_reference is not None
            and decision.result_reference != result_reference
        ):
            raise ReviewTransitionError(
                "re-evaluation result is already linked to another run"
            )
        decision.result_reference = result_reference
        await self._session.commit()
        await self._session.refresh(decision)
        return _decision_read(decision)

    async def status(self, workflow_id: UUID) -> WorkflowReviewStatus:
        rows = (
            await self._session.execute(
                select(HumanReviewItem.status, func.count())
                .where(HumanReviewItem.workflow_id == workflow_id)
                .group_by(HumanReviewItem.status)
            )
        ).all()
        counts = {status: int(count) for status, count in rows}
        total = sum(counts.values())
        if total == 0:
            bid_workflow = await self._session.scalar(
                select(BidWorkflowRun.id).where(BidWorkflowRun.id == workflow_id)
            )
            comparison = await self._session.scalar(
                select(BidComparisonRun.id).where(
                    BidComparisonRun.id == workflow_id
                )
            )
            if bid_workflow is None and comparison is None:
                raise ReviewNotFound("workflow does not exist")
        pending = counts.get(ReviewStatus.PENDING.value, 0)
        reevaluation = counts.get(ReviewStatus.REEVALUATION_REQUESTED.value, 0)
        return WorkflowReviewStatus(
            workflow_id=workflow_id,
            total=total,
            pending=pending,
            approved=counts.get(ReviewStatus.APPROVED.value, 0),
            rejected=counts.get(ReviewStatus.REJECTED.value, 0),
            unresolved=counts.get(ReviewStatus.UNRESOLVED.value, 0),
            reevaluation_requested=reevaluation,
            all_required_resolved=total > 0 and pending == 0 and reevaluation == 0,
        )


def _item_read(item: HumanReviewItem) -> ReviewItemRead:
    return ReviewItemRead(
        review_item_id=item.id,
        operation_type=item.operation_type,
        bid_id=item.bid_id,
        workflow_id=item.workflow_id,
        source_stage=item.source_stage,
        category=item.category,
        item_reference=item.item_reference,
        field_name=item.field_name,
        proposed_value=item.proposed_value,
        validation_status=item.validation_status,
        confidence_score=(
            None if item.confidence_score is None else float(item.confidence_score)
        ),
        supporting_citations=[
            Citation.model_validate(value) for value in item.supporting_citations
        ],
        contradicting_citations=[
            Citation.model_validate(value) for value in item.contradicting_citations
        ],
        reason=item.reason,
        severity=item.severity,
        status=item.status,
        created_at=item.created_at,
        reviewer_id=item.reviewer_id,
        decision_at=item.decision_at,
        corrected_value=item.corrected_value,
        reevaluation_attempts=item.reevaluation_attempts,
    )


def _decision_read(
    decision: HumanReviewDecision, *, duplicate: bool = False
) -> ReviewDecisionRead:
    return ReviewDecisionRead(
        decision_id=decision.id,
        review_item_id=decision.review_item_id,
        decision=decision.decision,
        previous_status=decision.previous_status,
        new_status=decision.new_status,
        reviewer_id=decision.reviewer_id,
        comments=decision.comments,
        corrected_value=decision.corrected_value,
        result_reference=decision.result_reference,
        created_at=decision.created_at,
        duplicate=duplicate,
    )
