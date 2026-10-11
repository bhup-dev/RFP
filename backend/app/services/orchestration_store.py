"""PostgreSQL persistence and prerequisite lookup for LangGraph workflows."""

from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.state import BidIntelligenceState
from app.db.models.addendum import AddendumAnalysisRun
from app.db.models.bid import Bid
from app.db.models.document import Document
from app.db.models.extraction import ExtractionRun
from app.db.models.orchestration import BidWorkflowRun
from app.db.models.validation import ValidationRun

ELIGIBLE_DOCUMENT_STATUSES = {"indexed", "partial", "completed"}
FAILED_DOCUMENT_STATUSES = {"failed", "error"}


class WorkflowLineageError(RuntimeError):
    """An upstream run does not belong to this workflow's bid."""


class WorkflowRunNotFound(RuntimeError):
    """The workflow record disappeared during graph execution."""


@dataclass(frozen=True)
class BidWorkflowContext:
    exists: bool
    document_ids: list[UUID]
    eligible_document_ids: list[UUID]
    failed_document_ids: list[UUID]


class SqlOrchestrationStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bid_exists(self, bid_id: UUID) -> bool:
        return (
            await self._session.scalar(select(Bid.id).where(Bid.id == bid_id))
        ) is not None

    async def load_bid_context(self, bid_id: UUID) -> BidWorkflowContext:
        if not await self.bid_exists(bid_id):
            return BidWorkflowContext(False, [], [], [])
        rows = (
            await self._session.execute(
                select(Document.id, Document.ingestion_status)
                .where(Document.bid_id == bid_id)
                .order_by(Document.created_at, Document.id)
            )
        ).all()
        return BidWorkflowContext(
            exists=True,
            document_ids=[row.id for row in rows],
            eligible_document_ids=[
                row.id
                for row in rows
                if row.ingestion_status in ELIGIBLE_DOCUMENT_STATUSES
            ],
            failed_document_ids=[
                row.id for row in rows if row.ingestion_status in FAILED_DOCUMENT_STATUSES
            ],
        )

    async def create_run(self, state: BidIntelligenceState) -> None:
        self._session.add(
            BidWorkflowRun(
                id=state["workflow_id"],
                bid_id=state["bid_id"],
                workflow_version=state["workflow_version"],
                status=state["workflow_status"],
                current_stage=state["current_stage"],
                stage_statuses=state["stage_statuses"],
                started_at=state["started_at"],
            )
        )
        await self._session.commit()

    async def load_state(self, workflow_id: UUID) -> BidIntelligenceState:
        run = await self._session.get(BidWorkflowRun, workflow_id)
        if run is None:
            raise WorkflowRunNotFound(str(workflow_id))
        summaries = run.summaries or {}
        return cast(
            BidIntelligenceState,
            {
                "workflow_id": run.id,
                "workflow_version": run.workflow_version,
                "bid_id": run.bid_id,
                "workflow_status": run.status,
                "current_stage": run.current_stage,
                "stage_statuses": run.stage_statuses,
                "document_ids": [UUID(item) for item in run.document_ids],
                "eligible_document_count": run.eligible_document_count,
                "extraction_run_id": run.extraction_run_id,
                "addendum_analysis_run_id": run.addendum_analysis_run_id,
                "validation_run_id": run.validation_run_id,
                "profile_id": run.profile_id,
                "extraction_summary": summaries.get("extraction", {}),
                "addendum_summary": summaries.get("addendum", {}),
                "validation_summary": summaries.get("validation", {}),
                "validation_findings": [],
                "profile_status": summaries.get("profile_status"),
                "requirements_findings": [],
                "product_findings": [],
                "commercial_findings": [],
                "compliance_findings": [],
                "evidence_review_findings": [],
                "agent_statuses": summaries.get("agent_statuses", {}),
                "agent_warnings": summaries.get("agent_warnings", []),
                "agent_errors": summaries.get("agent_errors", []),
                "review_item_ids": [
                    UUID(item) for item in summaries.get("review_item_ids", [])
                ],
                "review_status": summaries.get("review_status", "not_required"),
                "warnings": run.warnings,
                "errors": run.errors,
                "error_code": run.error_code,
                "requires_human_review": run.requires_human_review,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
            },
        )

    async def save_state(self, state: BidIntelligenceState) -> None:
        run = await self._session.get(BidWorkflowRun, state["workflow_id"])
        if run is None:
            raise WorkflowRunNotFound(str(state["workflow_id"]))
        await self._validate_lineage(state)
        run.status = state["workflow_status"]
        run.current_stage = state["current_stage"]
        run.stage_statuses = dict(state["stage_statuses"])
        run.document_ids = [str(item) for item in state["document_ids"]]
        run.eligible_document_count = state["eligible_document_count"]
        run.extraction_run_id = state["extraction_run_id"]
        run.addendum_analysis_run_id = state["addendum_analysis_run_id"]
        run.validation_run_id = state["validation_run_id"]
        run.profile_id = state["profile_id"]
        run.summaries = {
            "extraction": state["extraction_summary"],
            "addendum": state["addendum_summary"],
            "validation": state["validation_summary"],
            "profile_status": state["profile_status"],
            "agent_finding_counts": {
                "requirements": len(state["requirements_findings"]),
                "product_specification": len(state["product_findings"]),
                "commercial_terms": len(state["commercial_findings"]),
                "compliance_documentation": len(state["compliance_findings"]),
                "evidence_review": len(state["evidence_review_findings"]),
            },
            "agent_statuses": state["agent_statuses"],
            "agent_warnings": state["agent_warnings"],
            "agent_errors": state["agent_errors"],
            "review_item_ids": [str(item) for item in state["review_item_ids"]],
            "review_status": state["review_status"],
        }
        run.warnings = list(state["warnings"])
        run.errors = list(state["errors"])
        run.requires_human_review = state["requires_human_review"]
        run.completed_at = state["completed_at"]
        run.error_code = state["error_code"]
        run.error_detail = state["errors"][-1] if state["errors"] else None
        await self._session.commit()

    async def _validate_lineage(self, state: BidIntelligenceState) -> None:
        expected = state["bid_id"]
        references = (
            (ExtractionRun, state["extraction_run_id"], "extraction"),
            (AddendumAnalysisRun, state["addendum_analysis_run_id"], "addendum"),
            (ValidationRun, state["validation_run_id"], "validation"),
        )
        for model, run_id, label in references:
            if run_id is None:
                continue
            actual = await self._session.scalar(
                select(model.bid_id).where(model.id == run_id)
            )
            if actual != expected:
                raise WorkflowLineageError(
                    f"{label} run {run_id} does not belong to bid {expected}"
                )
