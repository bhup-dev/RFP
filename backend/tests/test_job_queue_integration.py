"""PostgreSQL job queue: skip-locked claims, one active job per bid, retries, stale recovery."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.models.bid import Bid
from app.db.models.job import ProcessingJob
from app.jobs.repository import ActiveJobExists, JobRepository

pytestmark = pytest.mark.integration


def test_job_queue_against_postgres() -> None:
    try:
        asyncio.run(_run())
    except (OSError, OperationalError, InterfaceError, DBAPIError) as exc:
        pytest.skip(f"PostgreSQL is not available ({type(exc).__name__})")


async def _run() -> None:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    bid_a, bid_b = uuid4(), uuid4()
    try:
        async with factory() as session:
            session.add_all(
                [
                    Bid(id=bid_a, bid_identifier=f"jobs-{bid_a}"),
                    Bid(id=bid_b, bid_identifier=f"jobs-{bid_b}"),
                ]
            )
            await session.commit()
            # Ignore jobs that other local runs left queued.
            await session.execute(
                update(ProcessingJob)
                .where(ProcessingJob.status == "queued")
                .values(available_at=datetime.now(UTC) + timedelta(days=1))
            )
            await session.commit()

        async with factory() as session:
            repo = JobRepository(session)
            job_a = await repo.enqueue(bid_a, "ingest_and_analyze", max_attempts=2)
            job_b = await repo.enqueue(bid_b, "analyze_only", max_attempts=1)
            with pytest.raises(ActiveJobExists):
                await repo.enqueue(bid_a, "analyze_only", max_attempts=2)

        async with factory() as first, factory() as second:
            claims = await asyncio.gather(
                JobRepository(first).claim_next("worker-1"),
                JobRepository(second).claim_next("worker-2"),
            )
        claimed_ids = {job.id for job in claims if job is not None}
        assert claimed_ids == {job_a.id, job_b.id}

        async with factory() as session:
            repo = JobRepository(session)
            status = await repo.mark_failed(
                job_a.id, code="ingestion_failed", detail="parse failed", retryable=True
            )
            assert status == "queued"
            requeued = await repo.get(job_a.id)
            assert requeued.available_at > datetime.now(UTC)
            assert requeued.error_code == "ingestion_failed"

            await session.execute(
                update(ProcessingJob)
                .where(ProcessingJob.id == job_b.id)
                .values(heartbeat_at=datetime.now(UTC) - timedelta(hours=1))
            )
            await session.commit()
            assert await repo.requeue_stale(60) >= 1
            lost = await session.get(ProcessingJob, job_b.id, populate_existing=True)
            assert lost.status == "failed"
            assert lost.error_code == "worker_lost"

            await session.execute(
                update(ProcessingJob)
                .where(ProcessingJob.id == job_a.id)
                .values(available_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await session.commit()
            again = await repo.claim_next("worker-3")
            assert again is not None and again.id == job_a.id
            assert again.attempts == 2
            assert await repo.mark_failed(
                job_a.id, code="ingestion_failed", detail="still broken", retryable=True
            ) == "failed"

            fresh = await repo.enqueue(bid_a, "analyze_only", max_attempts=1)
            await repo.claim_next("worker-4")
            await repo.mark_succeeded(fresh.id, summary={"ok": True}, workflow_id=None)
            done = await session.get(ProcessingJob, fresh.id, populate_existing=True)
            assert done.status == "succeeded"
            assert done.stage == "done"
    finally:
        async with factory() as session:
            await session.execute(delete(Bid).where(Bid.id.in_([bid_a, bid_b])))
            await session.commit()
        await engine.dispose()
