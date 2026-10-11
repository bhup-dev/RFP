import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.agents.comparison_state import initial_comparison_state
from app.config import settings
from app.db.models.bid import Bid
from app.db.models.comparison import BidComparisonProfile, BidComparisonRun
from app.db.models.extraction import ExtractionRun
from app.schemas.comparison import (
    BidComparisonReport,
    BidComparisonRequest,
    ComparisonProfileReference,
    ComparisonStatus,
    ComparisonSummary,
)
from app.services.comparison_store import (
    ComparisonLineageError,
    SqlComparisonStore,
)

pytestmark = pytest.mark.integration


class DatabaseUnreachable(Exception):
    pass


def test_comparison_history_preserves_profile_references_and_rejects_cross_bid_runs():
    try:
        asyncio.run(_run())
    except DatabaseUnreachable as exc:
        pytest.skip(f"PostgreSQL is not available ({type(exc.__cause__).__name__})")


async def _run():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    first, second = uuid4(), uuid4()
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                session.add_all(
                    [
                        Bid(id=first, bid_identifier=f"compare-{first}"),
                        Bid(id=second, bid_identifier=f"compare-{second}"),
                    ]
                )
                await session.commit()
                first_extraction = ExtractionRun(
                    bid_id=first,
                    status="completed",
                    model_name="mock",
                    completed_at=datetime.now(UTC),
                    document_ids=[],
                )
                second_extraction = ExtractionRun(
                    bid_id=second,
                    status="completed",
                    model_name="mock",
                    completed_at=datetime.now(UTC),
                    document_ids=[],
                )
                session.add_all([first_extraction, second_extraction])
                await session.commit()

                request = BidComparisonRequest(bid_ids=[first, second])
                state = initial_comparison_state(
                    uuid4(), request, datetime.now(UTC)
                )
                store = SqlComparisonStore(session)
                await store.create_run(state)
                references = [
                    ComparisonProfileReference(
                        bid_id=first,
                        bid_identifier="first",
                        profile_id=uuid4(),
                        extraction_run_id=first_extraction.id,
                        profile_status="ready",
                    ),
                    ComparisonProfileReference(
                        bid_id=second,
                        bid_identifier="second",
                        profile_id=uuid4(),
                        extraction_run_id=second_extraction.id,
                        profile_status="ready",
                    ),
                ]
                await store.save_profile_references(state["comparison_id"], references)
                report = BidComparisonReport(
                    comparison_id=state["comparison_id"],
                    status=ComparisonStatus.COMPLETED,
                    bid_ids=[first, second],
                    profile_references=references,
                    comparison_dimensions=request.dimensions,
                    summary=ComparisonSummary(
                        total_rows=0,
                        equivalent=0,
                        different=0,
                        missing=0,
                        conflicting=0,
                        not_comparable=0,
                        uncertain=0,
                    ),
                    comparison_rows=[],
                    requires_human_review=False,
                    created_at=datetime.now(UTC),
                )
                await store.save_report(report)

                persisted = await session.get(BidComparisonRun, state["comparison_id"])
                assert persisted is not None
                assert persisted.status == "completed"
                assert persisted.result["comparison_id"] == str(state["comparison_id"])
                assert int(
                    await session.scalar(
                        select(func.count()).select_from(BidComparisonProfile)
                    )
                ) == 2

                references[0] = references[0].model_copy(
                    update={"extraction_run_id": second_extraction.id}
                )
                with pytest.raises(ComparisonLineageError):
                    await store.save_profile_references(
                        state["comparison_id"], references
                    )
                await session.rollback()
        except (OSError, OperationalError, InterfaceError, DBAPIError, TimeoutError) as exc:
            raise DatabaseUnreachable from exc
    finally:
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    delete(Bid).where(Bid.id.in_([first, second]))
                )
        finally:
            await engine.dispose()
