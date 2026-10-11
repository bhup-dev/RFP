"""Execute one claimed processing job: ingest and index documents, then analyze."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.agents.state import BidIntelligenceState
from app.db.models.document import Document
from app.db.models.job import ProcessingJob
from app.jobs.repository import JobRepository
from app.schemas.workflow import WorkflowDocumentResult

logger = logging.getLogger(__name__)

SUCCESSFUL_WORKFLOW_STATUSES = {"completed", "completed_with_warnings", "needs_review"}
RETRYABLE_WORKFLOW_ERRORS = {"workflow_timeout", "workflow_runtime_error"}
MAX_REPORTED_DOCUMENT_ERRORS = 20

IngestDocument = Callable[[UUID], Awaitable[WorkflowDocumentResult]]
AnalyzeBid = Callable[[UUID], Awaitable[BidIntelligenceState]]
ListDocuments = Callable[[UUID], Awaitable[list[UUID]]]


class JobFailure(Exception):
    def __init__(self, code: str, detail: str, *, retryable: bool, summary: dict[str, Any]) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.retryable = retryable
        self.summary = summary


@dataclass
class JobOutcome:
    summary: dict[str, Any]
    workflow_id: UUID | None


@dataclass
class JobRunner:
    """Stages are injected so the runner is testable without databases or providers."""

    repository: JobRepository
    list_documents: ListDocuments
    ingest_document: IngestDocument
    analyze_bid: AnalyzeBid

    async def run(self, job: ProcessingJob) -> JobOutcome:
        summary: dict[str, Any] = {}
        if job.job_type == "ingest_and_analyze":
            await self.repository.set_stage(job.id, "ingesting")
            summary["ingestion"] = await self._ingest(job.bid_id)
        await self.repository.set_stage(job.id, "analyzing")
        state = await self.analyze_bid(job.bid_id)
        summary["workflow"] = _workflow_summary(state)
        status = state["workflow_status"]
        if status not in SUCCESSFUL_WORKFLOW_STATUSES:
            code = state.get("error_code") or "workflow_failed"
            detail = "; ".join(state["errors"]) or f"workflow finished with status {status}"
            raise JobFailure(
                code,
                detail,
                retryable=code in RETRYABLE_WORKFLOW_ERRORS,
                summary=summary,
            )
        return JobOutcome(summary=summary, workflow_id=state["workflow_id"])

    async def _ingest(self, bid_id: UUID) -> dict[str, Any]:
        document_ids = await self.list_documents(bid_id)
        if not document_ids:
            raise JobFailure(
                "no_documents",
                "this bid has no uploaded documents to process",
                retryable=False,
                summary={},
            )
        results = [await self.ingest_document(document_id) for document_id in document_ids]
        completed = [item for item in results if item.completed]
        failed = [item for item in results if not item.completed]
        summary = {
            "documents_total": len(results),
            "documents_completed": len(completed),
            "documents_failed": len(failed),
            "chunks_created": sum(item.chunks_created for item in results),
            "points_upserted": sum(item.points_upserted for item in results),
            "failures": [
                {
                    "document_id": str(item.document_id) if item.document_id else None,
                    "relative_path": item.relative_path,
                    "errors": item.errors[:5],
                }
                for item in failed[:MAX_REPORTED_DOCUMENT_ERRORS]
            ],
        }
        if not completed:
            first = failed[0].errors[0] if failed and failed[0].errors else "unknown error"
            raise JobFailure(
                "ingestion_failed",
                f"no document could be parsed and indexed: {first}",
                retryable=True,
                summary={"ingestion": summary},
            )
        return summary


def _workflow_summary(state: BidIntelligenceState) -> dict[str, Any]:
    return {
        "workflow_id": str(state["workflow_id"]),
        "status": state["workflow_status"],
        "current_stage": state["current_stage"],
        "requires_human_review": state["requires_human_review"],
        "profile_id": str(state["profile_id"]) if state["profile_id"] else None,
        "profile_status": state["profile_status"],
        "eligible_document_count": state["eligible_document_count"],
        "warnings": list(state["warnings"])[:20],
        "errors": list(state["errors"])[:20],
    }


async def bid_document_ids(session: Any, bid_id: UUID) -> list[UUID]:
    rows = await session.scalars(
        select(Document.id)
        .where(Document.bid_id == bid_id)
        .order_by(Document.created_at, Document.id)
    )
    return list(rows.all())
