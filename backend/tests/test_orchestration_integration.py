import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.agents.state import initial_state
from app.config import settings
from app.db.models.bid import Bid
from app.db.models.extraction import ExtractionRun
from app.db.models.orchestration import BidWorkflowRun
from app.services.orchestration_store import (
    SqlOrchestrationStore,
    WorkflowLineageError,
)

pytestmark = pytest.mark.integration


class DatabaseUnreachable(Exception):
    pass


def test_workflow_history_is_persisted_and_cross_bid_runs_are_rejected():
    try:
        asyncio.run(_run())
    except DatabaseUnreachable as exc:
        pytest.skip(f"PostgreSQL is not available ({type(exc.__cause__).__name__})")


async def _run():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    bid_id, other_bid_id = uuid4(), uuid4()
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                session.add_all(
                    [
                        Bid(id=bid_id, bid_identifier=f"workflow-{bid_id}"),
                        Bid(id=other_bid_id, bid_identifier=f"workflow-{other_bid_id}"),
                    ]
                )
                await session.commit()
                foreign_extraction = ExtractionRun(
                    bid_id=other_bid_id,
                    status="completed",
                    model_name="mock",
                    completed_at=datetime.now(UTC),
                    document_ids=[],
                )
                session.add(foreign_extraction)
                await session.commit()

                store = SqlOrchestrationStore(session)
                first = initial_state(uuid4(), bid_id, "1.0", datetime.now(UTC))
                second = initial_state(uuid4(), bid_id, "1.0", datetime.now(UTC))
                await store.create_run(first)
                await store.create_run(second)

                first["workflow_status"] = "completed"
                first["current_stage"] = "finalize"
                first["stage_statuses"]["finalize"] = "completed"
                first["completed_at"] = datetime.now(UTC)
                await store.save_state(first)

                second["extraction_run_id"] = foreign_extraction.id
                with pytest.raises(WorkflowLineageError):
                    await store.save_state(second)
                await session.rollback()

                rows = (
                    await session.scalars(
                        select(BidWorkflowRun)
                        .where(BidWorkflowRun.bid_id == bid_id)
                        .order_by(BidWorkflowRun.created_at)
                    )
                ).all()
                assert len(rows) == 2
                assert rows[0].status == "completed"
                assert rows[1].status == "running"
        except (OSError, OperationalError, InterfaceError, DBAPIError, TimeoutError) as exc:
            raise DatabaseUnreachable from exc
    finally:
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    delete(Bid).where(Bid.id.in_([bid_id, other_bid_id]))
                )
        finally:
            await engine.dispose()
