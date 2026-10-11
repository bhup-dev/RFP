"""LangGraph bid-comparison endpoint."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.bid_comparison_agent import (
    ComparisonDependencies,
    build_bid_comparison_graph,
    report_from_state,
)
from app.agents.checkpointing import get_checkpointer
from app.agents.comparison_state import BidComparisonState, initial_comparison_state
from app.api.intelligence_profile import get_bid_intelligence_service
from app.config import settings
from app.core.redaction import redact_text
from app.db.session import get_session_factory
from app.schemas.comparison import BidComparisonReport, BidComparisonRequest
from app.services.comparison_store import SqlComparisonStore
from app.services.llm_service import get_llm_service
from app.services.specialized_agent_store import SqlSpecializedAgentStore
from app.observability.instrumentation import SqlNodeObserver
from app.services.review_service import ReviewService
from app.services.workflow_review_manager import ComparisonReviewManager

router = APIRouter(prefix=f"{settings.api_prefix}/v1/bids", tags=["comparison"])


async def get_comparison_session() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            if session.in_transaction():
                await session.rollback()


def get_comparison_dependencies(
    session: AsyncSession = Depends(get_comparison_session),
) -> ComparisonDependencies:
    return ComparisonDependencies(
        get_bid_intelligence_service(session),
        SqlComparisonStore(session),
        SqlSpecializedAgentStore(session),
        get_llm_service(),
        max_bids=settings.bid_comparison_max_bids,
        semantic_timeout_seconds=settings.bid_comparison_semantic_timeout_seconds,
        max_semantic_rows=settings.bid_comparison_max_semantic_rows,
        max_output_tokens=settings.bid_comparison_max_output_tokens,
        observer=SqlNodeObserver(session),
        review_manager=ComparisonReviewManager(ReviewService(session)),
        human_review_interrupt_enabled=settings.human_review_enabled,
    )


@router.post("/compare", response_model=BidComparisonReport)
async def compare_bids(
    body: BidComparisonRequest,
    dependencies: ComparisonDependencies = Depends(get_comparison_dependencies),
) -> BidComparisonReport:
    state = initial_comparison_state(uuid4(), body, datetime.now(UTC))
    store = cast(SqlComparisonStore, dependencies.persistence)
    await store.create_run(state)
    graph = build_bid_comparison_graph(dependencies, get_checkpointer())
    config = {"configurable": {"thread_id": str(state["comparison_id"])}}
    try:
        result = cast(
            BidComparisonState, await graph.ainvoke(state, config=config)
        )
    except Exception as exc:
        failed = cast(
            BidComparisonState,
            {
                **state,
                "status": "failed",
                "current_stage": "finalize_comparison",
                "stage_statuses": {
                    **state["stage_statuses"],
                    "finalize_comparison": "completed",
                },
                "errors": [redact_text(str(exc)) or type(exc).__name__],
                "error_code": "comparison_runtime_error",
                "requires_human_review": True,
                "completed_at": datetime.now(UTC),
            },
        )
        report = report_from_state(failed)
        await store.save_report(report)
        return report
    report = report_from_state(result)
    if result["error_code"] == "bid_not_found":
        raise HTTPException(
            status_code=404, detail=report.model_dump(mode="json")
        )
    if result["error_code"] == "profile_unavailable":
        raise HTTPException(
            status_code=409, detail=report.model_dump(mode="json")
        )
    if result["error_code"] in {"too_many_bids", "profile_load_failed"}:
        raise HTTPException(
            status_code=422 if result["error_code"] == "too_many_bids" else 409,
            detail=report.model_dump(mode="json"),
        )
    return report
