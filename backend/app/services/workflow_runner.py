"""Run one LangGraph bid-intelligence workflow; shared by the API and the job worker."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from app.agents.checkpointing import get_checkpointer
from app.agents.state import BidIntelligenceState, initial_state
from app.agents.workflow import WorkflowDependencies, build_bid_intelligence_graph
from app.config import settings
from app.core.redaction import redact_text
from app.services.orchestration_store import SqlOrchestrationStore


class BidNotFound(Exception):
    """The requested bid does not exist."""


async def execute_bid_workflow(
    bid_id: UUID,
    dependencies: WorkflowDependencies,
) -> BidIntelligenceState:
    """Create a new append-only run and execute the compiled StateGraph.

    Timeouts and runtime errors are persisted as a failed run and returned,
    not raised, so callers always get the durable terminal state.
    """
    store = cast(SqlOrchestrationStore, dependencies.workflow_store)
    if not await store.bid_exists(bid_id):
        raise BidNotFound("bid not found")

    state = initial_state(
        workflow_id=uuid4(),
        bid_id=bid_id,
        workflow_version=settings.langgraph_workflow_version,
        started_at=datetime.now(UTC),
    )
    await store.create_run(state)
    graph = build_bid_intelligence_graph(dependencies, get_checkpointer())
    config = {"configurable": {"thread_id": str(state["workflow_id"])}}
    try:
        async with asyncio.timeout(settings.langgraph_workflow_timeout_seconds):
            return cast(
                BidIntelligenceState,
                await graph.ainvoke(state, config=config),
            )
    except TimeoutError:
        persisted = await store.load_state(state["workflow_id"])
        return await terminal_failure(
            store, persisted, "workflow_timeout", "Workflow execution timed out"
        )
    except Exception as exc:
        persisted = await store.load_state(state["workflow_id"])
        return await terminal_failure(
            store,
            persisted,
            "workflow_runtime_error",
            redact_text(str(exc)) or type(exc).__name__,
        )


async def terminal_failure(
    store: SqlOrchestrationStore,
    state: BidIntelligenceState,
    code: str,
    message: str,
) -> BidIntelligenceState:
    failed = cast(
        BidIntelligenceState,
        {
            **state,
            "workflow_status": "failed",
            "current_stage": "finalize",
            "stage_statuses": {
                **state["stage_statuses"],
                "finalize": "completed",
            },
            "errors": [*state["errors"], message],
            "error_code": code,
            "requires_human_review": True,
            "completed_at": datetime.now(UTC),
        },
    )
    await store.save_state(failed)
    return failed
