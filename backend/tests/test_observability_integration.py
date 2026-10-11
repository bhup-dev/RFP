import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.models.observability import WorkflowNodeEvent
from app.observability.instrumentation import NodeObservation, SqlNodeObserver

pytestmark = pytest.mark.integration


class DatabaseUnreachable(Exception):
    pass


def test_node_observation_is_persisted_without_sensitive_payloads():
    try:
        asyncio.run(_run())
    except DatabaseUnreachable as exc:
        pytest.skip(f"PostgreSQL is not available ({type(exc.__cause__).__name__})")


async def _run():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    event_id = uuid4()
    operation_id = uuid4()
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                now = datetime.now(UTC)
                observation = NodeObservation(
                    operation_type="bid_workflow",
                    operation_id=operation_id,
                    bid_id=uuid4(),
                    node_name="extract_rfp_fields",
                    status="completed",
                    started_at=now,
                    completed_at=now,
                    duration_ms=12.5,
                    run_references={"extraction_run_id": str(uuid4())},
                )
                await SqlNodeObserver(session).record(observation)
                row = await session.get(
                    WorkflowNodeEvent,
                    await session.scalar(
                        WorkflowNodeEvent.__table__.select()
                        .with_only_columns(WorkflowNodeEvent.id)
                        .where(WorkflowNodeEvent.operation_id == operation_id)
                    ),
                )
                assert row is not None
                event_id = row.id
                assert row.duration_ms == 12.5
                assert row.run_references == observation.run_references
                assert "prompt" not in row.run_references
        except (OSError, OperationalError, InterfaceError, DBAPIError, TimeoutError) as exc:
            raise DatabaseUnreachable from exc
    finally:
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    delete(WorkflowNodeEvent).where(
                        WorkflowNodeEvent.id == event_id
                    )
                )
        finally:
            await engine.dispose()
