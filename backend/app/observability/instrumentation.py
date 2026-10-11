"""Failure-isolated wrappers for LangGraph node observability."""

import json
import logging
import time
from datetime import UTC, datetime
from functools import wraps
from typing import Any, Awaitable, Callable, Protocol
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redaction import redact_text
from app.db.models.observability import WorkflowNodeEvent

logger = logging.getLogger(__name__)


class NodeObservation(BaseModel):
    operation_type: str
    operation_id: UUID
    bid_id: UUID | None = None
    node_name: str
    status: str
    started_at: datetime
    completed_at: datetime
    duration_ms: float
    retry_count: int = 0
    error_category: str | None = None
    error_detail: str | None = None
    run_references: dict[str, str | None]
    requires_human_review: bool = False


class NodeObserver(Protocol):
    async def record(self, observation: NodeObservation) -> None: ...


class SqlNodeObserver:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, observation: NodeObservation) -> None:
        self._session.add(
            WorkflowNodeEvent(
                operation_type=observation.operation_type,
                operation_id=observation.operation_id,
                bid_id=observation.bid_id,
                node_name=observation.node_name,
                status=observation.status,
                started_at=observation.started_at,
                completed_at=observation.completed_at,
                duration_ms=observation.duration_ms,
                retry_count=observation.retry_count,
                error_category=observation.error_category,
                error_detail=observation.error_detail,
                run_references=observation.run_references,
                requires_human_review=observation.requires_human_review,
            )
        )
        try:
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise


def instrument_node(
    node_name: str,
    node: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    observer: NodeObserver | None,
    *,
    operation_type: str,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Observe one graph node without changing its state update or exceptions."""

    @wraps(node)
    async def wrapped(state: dict[str, Any]) -> dict[str, Any]:
        started_at = datetime.now(UTC)
        started = time.perf_counter()
        error: Exception | None = None
        update: dict[str, Any] = {}
        try:
            update = await node(state)
            return update
        except Exception as exc:
            error = exc
            raise
        finally:
            completed_at = datetime.now(UTC)
            merged = {**state, **update}
            status = _node_status(node_name, merged, error)
            category = (
                classify_error(error)
                if error is not None
                else classify_error_code(merged.get("error_code"))
                if status == "failed"
                else None
            )
            detail = (
                redact_text(str(error))[:1000]
                if error is not None
                else _last_error(merged) if status == "failed" else None
            )
            observation = NodeObservation(
                operation_type=operation_type,
                operation_id=_operation_id(merged),
                bid_id=_bid_id(merged),
                node_name=node_name,
                status=status,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=(time.perf_counter() - started) * 1000,
                error_category=category,
                error_detail=detail,
                run_references=_run_references(merged),
                requires_human_review=bool(
                    merged.get("requires_human_review", False)
                ),
            )
            logger.info(
                "workflow_node operation_type=%s operation_id=%s bid_id=%s "
                "node=%s status=%s duration_ms=%.3f error_category=%s refs=%s",
                observation.operation_type,
                observation.operation_id,
                observation.bid_id,
                observation.node_name,
                observation.status,
                observation.duration_ms,
                observation.error_category,
                json.dumps(observation.run_references, sort_keys=True),
            )
            if observer is not None:
                try:
                    await observer.record(observation)
                except Exception as instrumentation_error:
                    logger.error(
                        "Workflow instrumentation failed node=%s (%s)",
                        node_name,
                        type(instrumentation_error).__name__,
                    )

    return wrapped


def classify_error(error: Exception | None) -> str | None:
    if error is None:
        return None
    name = type(error).__name__.casefold()
    message = str(error).casefold()
    if isinstance(error, TimeoutError) or "timeout" in name or "timed out" in message:
        return "timeout"
    if isinstance(error, SQLAlchemyError) or any(
        value in name for value in ("database", "operationalerror", "interfaceerror")
    ):
        return "database_error"
    if "retrieval" in name or "retrieval" in message:
        return "retrieval_error"
    if "provider" in name or "openai" in name:
        return "llm_provider_error"
    if "jsondecode" in name or "structured" in message:
        return "invalid_structured_output"
    if "citation" in name or "citation" in message:
        return "citation_validation_error"
    if "checkpoint" in name or "checkpoint" in message:
        return "checkpoint_error"
    if "validation" in name or "valueerror" in name:
        return "validation_error"
    return "unknown_error"


def classify_error_code(code: object) -> str | None:
    if not isinstance(code, str):
        return None
    normalized = code.casefold()
    if "timeout" in normalized:
        return "timeout"
    if "citation" in normalized or "evidence" in normalized:
        return "citation_validation_error"
    if "retrieval" in normalized:
        return "retrieval_error"
    if "validation" in normalized or normalized in {
        "bid_not_found",
        "no_eligible_documents",
        "too_many_bids",
        "profile_unavailable",
    }:
        return "validation_error"
    if "provider" in normalized or "semantic" in normalized:
        return "llm_provider_error"
    if "profile_load" in normalized:
        return "database_error"
    return "unknown_error"


def _operation_id(state: dict[str, Any]) -> UUID:
    value = state.get("workflow_id") or state.get("comparison_id")
    return value if isinstance(value, UUID) else UUID(str(value))


def _bid_id(state: dict[str, Any]) -> UUID | None:
    value = state.get("bid_id")
    if value is None:
        return None
    return value if isinstance(value, UUID) else UUID(str(value))


def _node_status(
    node_name: str, state: dict[str, Any], error: Exception | None
) -> str:
    if error is not None:
        return "failed"
    statuses = state.get("stage_statuses") or {}
    return str(statuses.get(node_name, "completed"))


def _last_error(state: dict[str, Any]) -> str | None:
    errors = state.get("errors") or []
    return redact_text(str(errors[-1]))[:1000] if errors else None


def _run_references(state: dict[str, Any]) -> dict[str, str | None]:
    return {
        name: None if state.get(name) is None else str(state[name])
        for name in (
            "extraction_run_id",
            "addendum_analysis_run_id",
            "validation_run_id",
            "profile_id",
        )
        if name in state
    }
