"""Job runner stages, worker outcome recording, and the shared workflow runner."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.agents.state import initial_state
from app.db.models.job import ProcessingJob
from app.jobs import worker as worker_module
from app.jobs.repository import retry_delay_seconds
from app.jobs.runner import JobFailure, JobOutcome, JobRunner
from app.jobs.worker import Worker
from app.schemas.workflow import WorkflowDocumentResult
from app.services import workflow_runner
from app.services.workflow_runner import BidNotFound, execute_bid_workflow


def _job(job_type: str = "ingest_and_analyze") -> ProcessingJob:
    return ProcessingJob(
        id=uuid4(),
        bid_id=uuid4(),
        job_type=job_type,
        status="running",
        stage="queued",
        attempts=1,
        max_attempts=3,
    )


def _state(bid_id, status="completed", **overrides):
    state = initial_state(uuid4(), bid_id, "1.0", datetime.now(UTC))
    state["workflow_status"] = status
    state.update(overrides)
    return state


class StageRecorder:
    def __init__(self) -> None:
        self.stages: list[str] = []

    async def set_stage(self, job_id, stage):
        self.stages.append(stage)


def _doc(completed: bool, errors=None) -> WorkflowDocumentResult:
    return WorkflowDocumentResult(
        document_id=uuid4(),
        relative_path="Bid/a.pdf",
        completed=completed,
        chunks_created=3 if completed else 0,
        points_upserted=3 if completed else 0,
        errors=errors or [],
    )


def _runner(repo, documents, results, state_for):
    queue = list(results)

    async def list_documents(bid_id):
        return documents

    async def ingest_document(document_id):
        return queue.pop(0)

    async def analyze_bid(bid_id):
        return state_for(bid_id)

    return JobRunner(repo, list_documents, ingest_document, analyze_bid)


def test_ingest_and_analyze_records_stages_and_summary() -> None:
    repo = StageRecorder()
    job = _job()
    runner = _runner(
        repo,
        [uuid4(), uuid4()],
        [_doc(True), _doc(False, ["parse failed"])],
        lambda bid_id: _state(bid_id, "needs_review", requires_human_review=True),
    )

    outcome = asyncio.run(runner.run(job))

    assert repo.stages == ["ingesting", "analyzing"]
    ingestion = outcome.summary["ingestion"]
    assert ingestion["documents_completed"] == 1
    assert ingestion["documents_failed"] == 1
    assert ingestion["failures"][0]["errors"] == ["parse failed"]
    assert outcome.summary["workflow"]["status"] == "needs_review"
    assert outcome.summary["workflow"]["requires_human_review"] is True
    assert outcome.workflow_id is not None


def test_analyze_only_skips_ingestion() -> None:
    repo = StageRecorder()
    runner = _runner(repo, [], [], lambda bid_id: _state(bid_id))
    outcome = asyncio.run(runner.run(_job("analyze_only")))
    assert repo.stages == ["analyzing"]
    assert "ingestion" not in outcome.summary


def test_no_documents_fails_permanently() -> None:
    runner = _runner(StageRecorder(), [], [], lambda bid_id: _state(bid_id))
    with pytest.raises(JobFailure) as failure:
        asyncio.run(runner.run(_job()))
    assert failure.value.code == "no_documents"
    assert failure.value.retryable is False


def test_all_documents_failing_is_retryable_and_skips_analysis() -> None:
    analyzed = []
    runner = _runner(
        StageRecorder(),
        [uuid4()],
        [_doc(False, ["embedding provider unavailable"])],
        lambda bid_id: analyzed.append(bid_id) or _state(bid_id),
    )
    with pytest.raises(JobFailure) as failure:
        asyncio.run(runner.run(_job()))
    assert failure.value.code == "ingestion_failed"
    assert failure.value.retryable is True
    assert "embedding provider unavailable" in failure.value.detail
    assert analyzed == []


@pytest.mark.parametrize(
    ("error_code", "retryable"),
    [("workflow_timeout", True), ("workflow_runtime_error", True), (None, False)],
)
def test_failed_workflow_is_reported_with_its_error(error_code, retryable) -> None:
    runner = _runner(
        StageRecorder(),
        [uuid4()],
        [_doc(True)],
        lambda bid_id: _state(bid_id, "failed", error_code=error_code, errors=["boom"]),
    )
    with pytest.raises(JobFailure) as failure:
        asyncio.run(runner.run(_job()))
    assert failure.value.code == (error_code or "workflow_failed")
    assert failure.value.retryable is retryable
    assert failure.value.detail == "boom"
    assert failure.value.summary["workflow"]["status"] == "failed"


def test_retry_backoff_is_exponential_and_capped() -> None:
    assert [retry_delay_seconds(n) for n in (1, 2, 3)] == [30, 60, 120]
    assert retry_delay_seconds(20) == 600


class FakeRepository:
    calls: list[tuple] = []
    claimable: list[ProcessingJob] = []

    def __init__(self, session) -> None:
        pass

    async def claim_next(self, worker_id):
        return FakeRepository.claimable.pop(0) if FakeRepository.claimable else None

    async def mark_succeeded(self, job_id, *, summary, workflow_id):
        FakeRepository.calls.append(("succeeded", job_id, summary, workflow_id))

    async def mark_failed(self, job_id, *, code, detail, retryable, summary, workflow_id):
        FakeRepository.calls.append(("failed", job_id, code, detail, retryable, workflow_id))
        return "queued" if retryable else "failed"

    async def release(self, job_id, worker_id):
        FakeRepository.calls.append(("released", job_id))

    async def heartbeat(self, job_id, worker_id):
        pass

    async def requeue_stale(self, seconds):
        return 0


@asynccontextmanager
async def _null_session():
    yield None


@pytest.fixture
def fake_repository(monkeypatch):
    FakeRepository.calls = []
    FakeRepository.claimable = []
    monkeypatch.setattr(worker_module, "JobRepository", FakeRepository)
    return FakeRepository


def _worker(run_job) -> Worker:
    return Worker(
        session_factory=_null_session,
        worker_id="test-worker",
        poll_interval_seconds=0.01,
        heartbeat_interval_seconds=60,
        run_job=run_job,
    )


def test_worker_records_success(fake_repository) -> None:
    job = _job()
    workflow_id = uuid4()
    fake_repository.claimable = [job]

    async def run_job(claimed):
        return JobOutcome(summary={"ok": True}, workflow_id=workflow_id)

    assert asyncio.run(_worker(run_job).run_once()) is True
    assert fake_repository.calls == [("succeeded", job.id, {"ok": True}, workflow_id)]
    assert asyncio.run(_worker(run_job).run_once()) is False


def test_worker_records_job_failures_and_unexpected_errors(fake_repository, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "openai_api_key", "sk-secret-value")
    first, second = _job(), _job()
    workflow_id = uuid4()
    fake_repository.claimable = [first, second]

    async def run_job(claimed):
        if claimed is first:
            raise JobFailure(
                "workflow_failed",
                "bad",
                retryable=False,
                summary={"workflow": {"workflow_id": str(workflow_id)}},
            )
        raise RuntimeError("api_key=sk-secret-value broke")

    worker = _worker(run_job)
    asyncio.run(worker.run_once())
    asyncio.run(worker.run_once())

    assert fake_repository.calls[0] == ("failed", first.id, "workflow_failed", "bad", False, workflow_id)
    code, detail, retryable = fake_repository.calls[1][2:5]
    assert code == "job_runtime_error"
    assert retryable is True
    assert "secret" not in detail


def test_worker_releases_the_job_on_shutdown(fake_repository) -> None:
    job = _job()
    fake_repository.claimable = [job]

    async def run_job(claimed):
        await asyncio.sleep(30)

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(_worker(run_job).run_once(stop))
        await asyncio.sleep(0.05)
        stop.set()
        return await asyncio.wait_for(task, timeout=2)

    assert asyncio.run(scenario()) is True
    assert fake_repository.calls == [("released", job.id)]


def test_run_forever_stops_promptly(fake_repository) -> None:
    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(_worker(None).run_forever(stop))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())


class MissingBidStore:
    async def bid_exists(self, bid_id):
        return False


class FakeDependencies:
    workflow_store = MissingBidStore()


def test_shared_workflow_runner_rejects_unknown_bids() -> None:
    with pytest.raises(BidNotFound):
        asyncio.run(execute_bid_workflow(uuid4(), FakeDependencies()))  # type: ignore[arg-type]


def test_shared_workflow_runner_persists_runtime_errors(monkeypatch) -> None:
    saved = []

    class Store:
        async def bid_exists(self, bid_id):
            return True

        async def create_run(self, state):
            self.state = state

        async def load_state(self, workflow_id):
            return self.state

        async def save_state(self, state):
            saved.append(state)

    class ExplodingGraph:
        async def ainvoke(self, state, config):
            raise RuntimeError("provider exploded")

    class Dependencies:
        workflow_store = Store()

    monkeypatch.setattr(workflow_runner, "build_bid_intelligence_graph", lambda deps, cp: ExplodingGraph())
    result = asyncio.run(execute_bid_workflow(uuid4(), Dependencies()))  # type: ignore[arg-type]

    assert result["workflow_status"] == "failed"
    assert result["error_code"] == "workflow_runtime_error"
    assert "provider exploded" in result["errors"][-1]
    assert saved and saved[-1]["workflow_status"] == "failed"
