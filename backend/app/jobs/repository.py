"""Queue operations for ``processing_jobs``. Each method commits its own change."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redaction import redact_text
from app.db.models.job import ACTIVE_JOB_STATUSES, JOB_TYPES, ProcessingJob

RETRY_BASE_SECONDS = 30
RETRY_MAX_SECONDS = 600
MAX_ERROR_DETAIL_LENGTH = 2000


class ActiveJobExists(Exception):
    """Another queued or running job already exists for this bid."""

    def __init__(self, job: ProcessingJob) -> None:
        super().__init__("a processing job is already queued or running for this bid")
        self.job = job


def retry_delay_seconds(attempts: int) -> int:
    """Exponential backoff: 30s, 60s, 120s ... capped at 10 minutes."""
    return min(RETRY_BASE_SECONDS * (2 ** max(attempts - 1, 0)), RETRY_MAX_SECONDS)


class JobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(self, bid_id: UUID, job_type: str, *, max_attempts: int) -> ProcessingJob:
        if job_type not in JOB_TYPES:
            raise ValueError(f"unknown job type: {job_type}")
        existing = await self.active_for_bid(bid_id)
        if existing is not None:
            raise ActiveJobExists(existing)
        job = ProcessingJob(
            bid_id=bid_id,
            job_type=job_type,
            status="queued",
            stage="queued",
            max_attempts=max_attempts,
            available_at=datetime.now(UTC),
        )
        self._session.add(job)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            # The partial unique index caught a concurrent enqueue for the same bid.
            await self._session.rollback()
            existing = await self.active_for_bid(bid_id)
            if existing is not None:
                raise ActiveJobExists(existing) from exc
            raise
        await self._session.refresh(job)
        return job

    async def get(self, job_id: UUID) -> ProcessingJob | None:
        return await self._session.get(ProcessingJob, job_id)

    async def active_for_bid(self, bid_id: UUID) -> ProcessingJob | None:
        return await self._session.scalar(
            select(ProcessingJob).where(
                ProcessingJob.bid_id == bid_id,
                ProcessingJob.status.in_(ACTIVE_JOB_STATUSES),
            )
        )

    async def list_for_bid(self, bid_id: UUID, *, limit: int = 20) -> list[ProcessingJob]:
        rows = await self._session.scalars(
            select(ProcessingJob)
            .where(ProcessingJob.bid_id == bid_id)
            .order_by(ProcessingJob.created_at.desc())
            .limit(limit)
        )
        return list(rows.all())

    async def list_recent(self, *, limit: int = 10) -> list[ProcessingJob]:
        rows = await self._session.scalars(
            select(ProcessingJob).order_by(ProcessingJob.created_at.desc()).limit(limit)
        )
        return list(rows.all())

    async def claim_next(self, worker_id: str) -> ProcessingJob | None:
        """Lock the oldest available queued job so concurrent workers never share one."""
        now = datetime.now(UTC)
        job = await self._session.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.status == "queued", ProcessingJob.available_at <= now)
            .order_by(ProcessingJob.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            await self._session.rollback()
            return None
        job.status = "running"
        job.attempts += 1
        job.locked_by = worker_id[:128]
        job.locked_at = now
        job.heartbeat_at = now
        job.started_at = job.started_at or now
        job.error_code = None
        job.error_detail = None
        await self._session.commit()
        await self._session.refresh(job)
        return job

    async def heartbeat(self, job_id: UUID, worker_id: str) -> None:
        await self._session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == job_id, ProcessingJob.locked_by == worker_id[:128])
            .values(heartbeat_at=datetime.now(UTC))
        )
        await self._session.commit()

    async def set_stage(self, job_id: UUID, stage: str) -> None:
        await self._session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == job_id)
            .values(stage=stage, heartbeat_at=datetime.now(UTC))
        )
        await self._session.commit()

    async def mark_succeeded(
        self,
        job_id: UUID,
        *,
        summary: dict[str, Any],
        workflow_id: UUID | None,
    ) -> None:
        await self._session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == job_id)
            .values(
                status="succeeded",
                stage="done",
                result_summary=summary,
                workflow_id=workflow_id,
                finished_at=datetime.now(UTC),
                locked_by=None,
                error_code=None,
                error_detail=None,
            )
        )
        await self._session.commit()

    async def mark_failed(
        self,
        job_id: UUID,
        *,
        code: str,
        detail: str,
        retryable: bool,
        summary: dict[str, Any] | None = None,
        workflow_id: UUID | None = None,
    ) -> str:
        """Requeue with backoff while attempts remain; otherwise fail permanently."""
        job = await self._session.get(ProcessingJob, job_id, populate_existing=True)
        if job is None:
            return "missing"
        now = datetime.now(UTC)
        job.error_code = code[:64]
        job.error_detail = (redact_text(detail) or code)[:MAX_ERROR_DETAIL_LENGTH]
        job.locked_by = None
        if summary is not None:
            job.result_summary = summary
        if workflow_id is not None:
            job.workflow_id = workflow_id
        if retryable and job.attempts < job.max_attempts:
            job.status = "queued"
            job.stage = "queued"
            job.available_at = now + timedelta(seconds=retry_delay_seconds(job.attempts))
        else:
            job.status = "failed"
            job.finished_at = now
        await self._session.commit()
        return job.status

    async def requeue_stale(self, stale_after_seconds: float) -> int:
        """Recover jobs whose worker stopped heartbeating (crash or kill)."""
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
        rows = await self._session.scalars(
            select(ProcessingJob)
            .where(
                ProcessingJob.status == "running",
                or_(ProcessingJob.heartbeat_at.is_(None), ProcessingJob.heartbeat_at < cutoff),
            )
            .with_for_update(skip_locked=True)
        )
        recovered = 0
        now = datetime.now(UTC)
        for job in rows.all():
            job.locked_by = None
            job.error_code = "worker_lost"
            job.error_detail = "the worker stopped responding while processing this job"
            if job.attempts < job.max_attempts:
                job.status = "queued"
                job.stage = "queued"
                job.available_at = now
            else:
                job.status = "failed"
                job.finished_at = now
            recovered += 1
        await self._session.commit()
        return recovered

    async def release(self, job_id: UUID, worker_id: str) -> None:
        """Return a running job to the queue on graceful shutdown without using an attempt."""
        await self._session.execute(
            update(ProcessingJob)
            .where(
                ProcessingJob.id == job_id,
                ProcessingJob.status == "running",
                ProcessingJob.locked_by == worker_id[:128],
            )
            .values(
                status="queued",
                stage="queued",
                locked_by=None,
                attempts=ProcessingJob.attempts - 1,
                available_at=datetime.now(UTC),
            )
        )
        await self._session.commit()
