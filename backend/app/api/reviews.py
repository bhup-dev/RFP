"""Token-protected human review, audit, and workflow-resume endpoints."""

import hmac
import logging
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.checkpointing import get_checkpointer
from app.agents.workflow import build_bid_intelligence_graph
from app.agents.bid_comparison_agent import build_bid_comparison_graph
from app.api.comparison import get_comparison_dependencies
from app.api.orchestration import get_workflow_dependencies
from app.config import settings
from app.db.session import get_session_factory
from app.schemas.review import (
    ReviewDecisionRequest,
    ReviewDecisionResponse,
    ReviewerContext,
    ReviewItemDetail,
    ReviewItemRead,
    ReviewStatus,
    WorkflowReviewStatus,
)
from app.services.review_service import (
    ReviewNotFound,
    ReviewService,
    ReviewTransitionError,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix=f"{settings.api_prefix}/v1", tags=["human-review"])


async def get_review_session() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            if session.in_transaction():
                await session.rollback()


def require_reviewer(
    authorization: str | None = Header(default=None),
    reviewer_id: str | None = Header(default=None, alias="X-Reviewer-ID"),
) -> ReviewerContext:
    token = settings.human_review_api_token
    if token is None:
        raise HTTPException(
            status_code=503,
            detail="human review API is disabled until HUMAN_REVIEW_API_TOKEN is configured",
        )
    expected = f"Bearer {token}"
    if authorization is None or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=403, detail="reviewer authorization failed")
    identity = (reviewer_id or "").strip()
    if not identity or len(identity) > 255:
        raise HTTPException(status_code=400, detail="X-Reviewer-ID is required")
    return ReviewerContext(reviewer_id=identity)


@router.get("/reviews", response_model=list[ReviewItemRead])
async def list_reviews(
    bid_id: UUID | None = Query(default=None),
    workflow_id: UUID | None = Query(default=None),
    status: ReviewStatus | None = Query(default=None),
    _reviewer: ReviewerContext = Depends(require_reviewer),
    session: AsyncSession = Depends(get_review_session),
) -> list[ReviewItemRead]:
    return await ReviewService(session).list_items(
        bid_id=bid_id, workflow_id=workflow_id, status=status
    )


@router.get("/reviews/{review_id}", response_model=ReviewItemDetail)
async def get_review(
    review_id: UUID,
    bid_id: UUID | None = Query(default=None),
    _reviewer: ReviewerContext = Depends(require_reviewer),
    session: AsyncSession = Depends(get_review_session),
) -> ReviewItemDetail:
    try:
        return await ReviewService(session).get_item_detail(
            review_id, bid_id=bid_id
        )
    except ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/workflows/{workflow_id}/review-status",
    response_model=WorkflowReviewStatus,
)
async def workflow_review_status(
    workflow_id: UUID,
    _reviewer: ReviewerContext = Depends(require_reviewer),
    session: AsyncSession = Depends(get_review_session),
) -> WorkflowReviewStatus:
    try:
        return await ReviewService(session).status(workflow_id)
    except ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/reviews/{review_id}/decision",
    response_model=ReviewDecisionResponse,
)
async def decide_review(
    review_id: UUID,
    body: ReviewDecisionRequest,
    reviewer: ReviewerContext = Depends(require_reviewer),
    session: AsyncSession = Depends(get_review_session),
) -> ReviewDecisionResponse:
    service = ReviewService(session)
    try:
        item_before = await service.get_item(review_id)
    except ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    checkpointer = get_checkpointer()
    if checkpointer is None:
        raise HTTPException(
            status_code=409,
            detail="workflow checkpoint is unavailable; decision was not recorded",
        )
    dependencies = None
    if item_before.operation_type == "bid_comparison":
        graph = build_bid_comparison_graph(
            get_comparison_dependencies(session), checkpointer
        )
    else:
        dependencies = get_workflow_dependencies(session)
        graph = build_bid_intelligence_graph(dependencies, checkpointer)
    config = {"configurable": {"thread_id": str(item_before.workflow_id)}}
    snapshot = await graph.aget_state(config)
    if not snapshot.values:
        raise HTTPException(
            status_code=409,
            detail="workflow checkpoint is missing; decision was not recorded",
        )

    if (
        body.decision.value == "request_re_evaluation"
        and (
            item_before.operation_type != "bid_workflow"
            or item_before.source_stage != "validate_extraction"
        )
    ):
        raise HTTPException(
            status_code=409,
            detail="targeted re-evaluation is not safely supported for this source stage",
        )
    if (
        body.decision.value == "request_re_evaluation"
        and item_before.reevaluation_attempts
        >= settings.human_review_max_reevaluation_attempts
    ):
        raise HTTPException(
            status_code=409,
            detail="maximum re-evaluation attempts reached",
        )
    try:
        item, decision = await service.decide(
            review_id, body, reviewer.reviewer_id
        )
    except ReviewTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if decision.duplicate:
        return ReviewDecisionResponse(
            item=item,
            decision=decision,
            workflow_resumed=False,
            detail="duplicate idempotency key; original decision returned",
        )

    if body.decision.value == "request_re_evaluation":
        assert dependencies is not None
        values = snapshot.values
        extraction_run_id = values.get("extraction_run_id")
        if extraction_run_id is None:
            raise HTTPException(
                status_code=409,
                detail="checkpoint has no extraction run for targeted validation",
            )
        try:
            report = await dependencies.validation.validate(
                item.bid_id, extraction_run_id
            )
            decision = await service.attach_reevaluation_result(
                decision.decision_id,
                {
                    "stage": "validate_extraction",
                    "previous_validation_run_id": (
                        None
                        if values.get("validation_run_id") is None
                        else str(values["validation_run_id"])
                    ),
                    "validation_run_id": str(report.validation_run_id),
                },
            )
            await graph.aupdate_state(
                config,
                {
                    "validation_run_id": report.validation_run_id,
                    "addendum_analysis_run_id": (
                        report.addendum_analysis_run_id
                        or values.get("addendum_analysis_run_id")
                    ),
                    "validation_summary": {
                        "status": report.status.value,
                        "overall_status": report.overall_status.value,
                        "addendum_check_status": report.addendum_check_status.value,
                        **report.summary.model_dump(mode="json"),
                    },
                    "validation_findings": [
                        field.model_dump(mode="json")
                        for field in report.fields.values()
                    ],
                },
            )
            item = await service.reset_after_reevaluation(
                review_id,
                max_attempts=settings.human_review_max_reevaluation_attempts,
            )
        except ReviewTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            try:
                await service.reset_after_reevaluation(
                    review_id,
                    max_attempts=settings.human_review_max_reevaluation_attempts,
                )
            except Exception:
                logger.error(
                    "Could not reset failed re-evaluation review_id=%s", review_id
                )
            logger.error(
                "Targeted review re-evaluation failed workflow_id=%s review_id=%s (%s)",
                item.workflow_id,
                review_id,
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="targeted validation re-evaluation failed",
            ) from exc
        logger.info(
            "Review re-evaluation workflow_id=%s review_id=%s reviewer_id=%s",
            item.workflow_id,
            review_id,
            reviewer.reviewer_id,
        )
        return ReviewDecisionResponse(
            item=item,
            decision=decision,
            workflow_resumed=False,
            workflow_status="pending_review",
            detail="validation was re-executed; the review item remains pending",
        )

    review_status = await service.status(item.workflow_id)
    resumed = False
    workflow_status = "pending_review"
    if review_status.all_required_resolved:
        result = await graph.ainvoke(
            Command(resume={"reviewed": True}),
            config=config,
        )
        resumed = True
        workflow_status = result.get("workflow_status")
    logger.info(
        "Review decision workflow_id=%s review_id=%s reviewer_id=%s decision=%s resumed=%s",
        item.workflow_id,
        review_id,
        reviewer.reviewer_id,
        body.decision.value,
        resumed,
    )
    return ReviewDecisionResponse(
        item=item,
        decision=decision,
        workflow_resumed=resumed,
        workflow_status=workflow_status,
    )
