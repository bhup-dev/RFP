import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from sqlalchemy import delete, func, select
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.agents.state import initial_state
from app.agents.workflow import build_bid_intelligence_graph
from app.config import settings
from app.db.models.bid import Bid
from app.db.models.review import HumanReviewDecision, HumanReviewItem
from app.schemas.review import ReviewDecisionRequest
from app.services.orchestration_store import SqlOrchestrationStore
from app.services.review_service import (
    ReviewNotFound,
    ReviewService,
    ReviewTransitionError,
)
from app.services.workflow_review_manager import WorkflowReviewManager
from test_langgraph_workflow import _fixture

pytestmark = pytest.mark.integration


class DatabaseUnreachable(Exception):
    pass


def test_review_decisions_are_audited_idempotent_and_preserve_original_value():
    try:
        asyncio.run(_run())
    except DatabaseUnreachable as exc:
        pytest.skip(f"PostgreSQL is not available ({type(exc.__cause__).__name__})")


def test_persisted_review_item_interrupts_and_authorized_decision_resumes_graph():
    try:
        asyncio.run(_run_interrupted_workflow())
    except DatabaseUnreachable as exc:
        pytest.skip(f"PostgreSQL is not available ({type(exc.__cause__).__name__})")


async def _run_interrupted_workflow():
    fixture = _fixture()
    bid_id = fixture[0]
    workflow_id = uuid4()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                session.add(
                    Bid(id=bid_id, bid_identifier=f"review-graph-{bid_id}")
                )
                await session.commit()
                state = initial_state(
                    workflow_id, bid_id, "test", datetime.now(UTC)
                )
                await SqlOrchestrationStore(session).create_run(state)
                field = SimpleNamespace(
                    model_dump=lambda **_kwargs: {
                        "field_name": "Due Date",
                        "extracted_value": "June 1 or June 2",
                        "validation_status": "conflicting",
                        "confidence_score": 0.99,
                        "supporting_citations": [],
                        "contradicting_citations": [],
                        "validation_reasons": [
                            "original and addendum deadlines conflict"
                        ],
                    }
                )
                fixture[8].result.fields = {"Due Date": field}
                review_service = ReviewService(session)
                manager = WorkflowReviewManager(
                    review_service,
                    confidence_threshold=0.55,
                    critical_fields={"Due Date"},
                )
                deps = replace(
                    fixture[-1],
                    review_manager=manager,
                    human_review_interrupt_enabled=True,
                )
                graph = build_bid_intelligence_graph(deps, InMemorySaver())
                config = {"configurable": {"thread_id": str(workflow_id)}}
                interrupted = await graph.ainvoke(state, config=config)
                assert interrupted["workflow_status"] == "pending_review"
                items = await review_service.list_items(workflow_id=workflow_id)
                assert len(items) == 1
                assert items[0].category.value == "ambiguous_deadline"

                await review_service.decide(
                    items[0].review_item_id,
                    ReviewDecisionRequest(
                        decision="approve",
                        idempotency_key=uuid4(),
                        comments="Authorized reviewer checked both source statements.",
                    ),
                    "reviewer@example.test",
                )
                completed = await graph.ainvoke(
                    Command(resume={"approved": True}), config=config
                )
                assert completed["review_status"] == "resolved"
                assert completed["workflow_status"] == "needs_review"
                detail = await review_service.get_item_detail(
                    items[0].review_item_id
                )
                assert detail.proposed_value == "June 1 or June 2"
                assert len(detail.decisions) == 1
        except (
            OSError,
            OperationalError,
            InterfaceError,
            DBAPIError,
            TimeoutError,
        ) as exc:
            raise DatabaseUnreachable from exc
    finally:
        try:
            async with engine.begin() as connection:
                await connection.execute(delete(Bid).where(Bid.id == bid_id))
        finally:
            await engine.dispose()


async def _run():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    bid_id = uuid4()
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                session.add(Bid(id=bid_id, bid_identifier=f"review-{bid_id}"))
                await session.commit()
                state = initial_state(
                    uuid4(), bid_id, "test", datetime.now(UTC)
                )
                await SqlOrchestrationStore(session).create_run(state)
                service = ReviewService(session)
                ids = await service.create_items(
                    state["workflow_id"],
                    bid_id,
                    [
                        {
                            "source_stage": "validate_extraction",
                            "category": "unsupported_claim",
                            "item_reference": "validation:Due Date",
                            "field_name": "Due Date",
                            "proposed_value": "June 1",
                            "validation_status": "unsupported",
                            "confidence_score": 0.2,
                            "supporting_citations": [],
                            "contradicting_citations": [],
                            "reason": "no valid citation",
                            "severity": "critical",
                        }
                    ],
                )
                request = ReviewDecisionRequest(
                    decision="approve",
                    idempotency_key=uuid4(),
                    comments="Verified against the signed addendum.",
                    corrected_value="June 2",
                )
                item, decision = await service.decide(
                    ids[0], request, "reviewer@example.test"
                )
                assert item.proposed_value == "June 1"
                assert item.corrected_value == "June 2"
                assert item.status.value == "approved"
                duplicate_item, duplicate = await service.decide(
                    ids[0], request, "reviewer@example.test"
                )
                assert duplicate.duplicate is True
                assert duplicate.decision_id == decision.decision_id
                assert duplicate_item.proposed_value == "June 1"
                assert (
                    await session.scalar(
                        select(func.count(HumanReviewDecision.id)).where(
                            HumanReviewDecision.review_item_id == ids[0]
                        )
                    )
                    == 1
                )
                invalid_ids = await service.create_items(
                    state["workflow_id"],
                    bid_id,
                    [
                        {
                            "source_stage": "evidence_review_agent",
                            "category": "invalid_citation",
                            "item_reference": "agent:invalid-citation",
                            "field_name": "requirement",
                            "proposed_value": "Unsupported claim",
                            "validation_status": "cross_bid_citation",
                            "confidence_score": 1.0,
                            "supporting_citations": [],
                            "contradicting_citations": [],
                            "reason": "citation belongs to another bid",
                            "severity": "high",
                        }
                    ],
                )
                with pytest.raises(
                    ReviewTransitionError,
                    match="invalid citation cannot be approved",
                ):
                    await service.decide(
                        invalid_ids[-1],
                        ReviewDecisionRequest(
                            decision="approve",
                            idempotency_key=uuid4(),
                            comments="Attempted approval.",
                        ),
                        "reviewer@example.test",
                    )
                await session.rollback()
                with pytest.raises(ReviewTransitionError):
                    await service.decide(
                        ids[0],
                        request.model_copy(update={"idempotency_key": uuid4()}),
                        "other-reviewer@example.test",
                    )
                await session.rollback()
                with pytest.raises(ReviewNotFound):
                    await service.get_item(ids[0], bid_id=uuid4())
                await session.rollback()

                concurrent_ids = await service.create_items(
                    state["workflow_id"],
                    bid_id,
                    [
                        {
                            "source_stage": "validate_extraction",
                            "category": "conflicting_requirement",
                            "item_reference": "validation:Payment Terms",
                            "field_name": "Payment Terms",
                            "proposed_value": "Net 30 or Net 60",
                            "validation_status": "conflicting",
                            "confidence_score": 0.5,
                            "supporting_citations": [],
                            "contradicting_citations": [],
                            "reason": "terms conflict",
                            "severity": "high",
                        }
                    ],
                )

                async def decide_concurrently(decision_name: str):
                    async with factory() as reviewer_session:
                        return await ReviewService(reviewer_session).decide(
                            concurrent_ids[-1],
                            ReviewDecisionRequest(
                                decision=decision_name,
                                idempotency_key=uuid4(),
                                comments="Concurrent reviewer decision.",
                            ),
                            f"{decision_name}@example.test",
                        )

                results = await asyncio.gather(
                    decide_concurrently("approve"),
                    decide_concurrently("reject"),
                    return_exceptions=True,
                )
                assert sum(not isinstance(result, Exception) for result in results) == 1
                assert sum(
                    isinstance(result, ReviewTransitionError) for result in results
                ) == 1
                await session.rollback()
                assert (
                    await session.scalar(
                        select(func.count(HumanReviewDecision.id)).where(
                            HumanReviewDecision.review_item_id
                            == concurrent_ids[-1]
                        )
                    )
                    == 1
                )
        except (OSError, OperationalError, InterfaceError, DBAPIError, TimeoutError) as exc:
            raise DatabaseUnreachable from exc
    finally:
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    delete(HumanReviewItem).where(HumanReviewItem.bid_id == bid_id)
                )
                await connection.execute(delete(Bid).where(Bid.id == bid_id))
        finally:
            await engine.dispose()
