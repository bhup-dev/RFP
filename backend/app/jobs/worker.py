"""Polling worker loop: claim a job, run it with a heartbeat, and record the outcome."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.redaction import redact_text
from app.db.models.job import ProcessingJob
from app.jobs.repository import JobRepository
from app.jobs.runner import JobFailure, JobOutcome, JobRunner, bid_document_ids

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
STALE_SWEEP_EVERY_POLLS = 15


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:6]}"


async def run_job_with_providers(session_factory: SessionFactory, job: ProcessingJob) -> JobOutcome:
    """Wire the real ingestion pipeline and LangGraph workflow for one job."""
    from app.api.orchestration import get_workflow_dependencies
    from app.api.workflow import get_end_to_end_pipeline
    from app.services.workflow_runner import execute_bid_workflow

    async with session_factory() as ingest_session, session_factory() as workflow_session:
        async with session_factory() as job_session:
            pipeline = get_end_to_end_pipeline(ingest_session)

            async def list_documents(bid_id: UUID) -> list[UUID]:
                return await bid_document_ids(ingest_session, bid_id)

            async def ingest_document(document_id: UUID):
                result = await pipeline.process_registered_document(document_id)
                if ingest_session.in_transaction():
                    await ingest_session.commit()
                return result

            async def analyze_bid(bid_id: UUID):
                return await execute_bid_workflow(
                    bid_id, get_workflow_dependencies(workflow_session)
                )

            runner = JobRunner(
                repository=JobRepository(job_session),
                list_documents=list_documents,
                ingest_document=ingest_document,
                analyze_bid=analyze_bid,
            )
            return await runner.run(job)


@dataclass
class Worker:
    session_factory: SessionFactory
    worker_id: str
    poll_interval_seconds: float = settings.job_poll_interval_seconds
    stale_after_seconds: float = settings.job_stale_after_seconds
    heartbeat_interval_seconds: float = settings.job_heartbeat_interval_seconds
    run_job: Callable[[ProcessingJob], Any] | None = None

    async def run_forever(self, stop: asyncio.Event) -> None:
        logger.info("Job worker started worker_id=%s", self.worker_id)
        polls = 0
        while not stop.is_set():
            try:
                if polls % STALE_SWEEP_EVERY_POLLS == 0:
                    await self._requeue_stale()
                polls += 1
                processed = await self.run_once(stop)
            except Exception as exc:  # keep the loop alive across transient DB errors
                logger.error(
                    "Job worker loop error worker_id=%s (%s)", self.worker_id, type(exc).__name__
                )
                processed = False
            if not processed:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self.poll_interval_seconds)
        logger.info("Job worker stopped worker_id=%s", self.worker_id)

    async def run_once(self, stop: asyncio.Event | None = None) -> bool:
        async with self.session_factory() as session:
            job = await JobRepository(session).claim_next(self.worker_id)
        if job is None:
            return False
        logger.info(
            "Job claimed job_id=%s bid_id=%s type=%s attempt=%s",
            job.id,
            job.bid_id,
            job.job_type,
            job.attempts,
        )
        work = asyncio.create_task(self._execute(job))
        heartbeat = asyncio.create_task(self._heartbeat(job.id))
        stopper = asyncio.create_task(stop.wait()) if stop is not None else None
        try:
            waiters = {work} if stopper is None else {work, stopper}
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if not work.done():
                work.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await work
                async with self.session_factory() as session:
                    await JobRepository(session).release(job.id, self.worker_id)
                logger.info("Job released on shutdown job_id=%s", job.id)
                return True
            await work
        finally:
            heartbeat.cancel()
            if stopper is not None:
                stopper.cancel()
        return True

    async def _execute(self, job: ProcessingJob) -> None:
        runner = self.run_job or (lambda claimed: run_job_with_providers(self.session_factory, claimed))
        try:
            outcome: JobOutcome = await runner(job)
        except JobFailure as failure:
            await self._fail(job, failure.code, failure.detail, failure.retryable, failure.summary)
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            detail = redact_text(str(exc)) or type(exc).__name__
            await self._fail(job, "job_runtime_error", detail, True, None)
            return
        async with self.session_factory() as session:
            await JobRepository(session).mark_succeeded(
                job.id, summary=outcome.summary, workflow_id=outcome.workflow_id
            )
        logger.info("Job succeeded job_id=%s bid_id=%s", job.id, job.bid_id)

    async def _fail(
        self,
        job: ProcessingJob,
        code: str,
        detail: str,
        retryable: bool,
        summary: dict[str, Any] | None,
    ) -> None:
        workflow_id = None
        if summary and isinstance(summary.get("workflow"), dict):
            raw = summary["workflow"].get("workflow_id")
            workflow_id = UUID(raw) if raw else None
        async with self.session_factory() as session:
            status = await JobRepository(session).mark_failed(
                job.id,
                code=code,
                detail=detail,
                retryable=retryable,
                summary=summary,
                workflow_id=workflow_id,
            )
        logger.warning(
            "Job failed job_id=%s bid_id=%s code=%s next_status=%s", job.id, job.bid_id, code, status
        )

    async def _heartbeat(self, job_id: UUID) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval_seconds)
            try:
                async with self.session_factory() as session:
                    await JobRepository(session).heartbeat(job_id, self.worker_id)
            except Exception as exc:
                logger.warning("Job heartbeat failed job_id=%s (%s)", job_id, type(exc).__name__)

    async def _requeue_stale(self) -> None:
        async with self.session_factory() as session:
            recovered = await JobRepository(session).requeue_stale(self.stale_after_seconds)
        if recovered:
            logger.warning("Recovered %s stale processing job(s)", recovered)
