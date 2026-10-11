"""PostgreSQL source of truth for comparison history."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.comparison_state import BidComparisonState
from app.db.models.addendum import AddendumAnalysisRun
from app.db.models.bid import Bid
from app.db.models.comparison import BidComparisonProfile, BidComparisonRun
from app.db.models.extraction import ExtractionRun
from app.db.models.validation import ValidationRun
from app.schemas.comparison import BidComparisonReport, ComparisonProfileReference


class ComparisonLineageError(RuntimeError):
    pass


class SqlComparisonStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_run(self, state: BidComparisonState) -> None:
        request = state["request"]
        self._session.add(
            BidComparisonRun(
                id=state["comparison_id"],
                status=state["status"],
                dimensions=request["dimensions"],
                allow_incomplete_profiles=request["allow_incomplete_profiles"],
                request_config=request,
                started_at=state["started_at"],
            )
        )
        await self._session.commit()

    async def existing_bid_ids(self, bid_ids: list[UUID]) -> set[UUID]:
        return set(
            await self._session.scalars(select(Bid.id).where(Bid.id.in_(bid_ids)))
        )

    async def save_profile_references(
        self,
        comparison_id: UUID,
        references: list[ComparisonProfileReference],
    ) -> None:
        run = await self._session.scalar(
            select(BidComparisonRun)
            .options(selectinload(BidComparisonRun.profiles))
            .where(BidComparisonRun.id == comparison_id)
        )
        if run is None:
            raise RuntimeError("comparison run does not exist")
        for reference in references:
            await self._validate_reference(reference)
        run.profiles.clear()
        for reference in references:
            run.profiles.append(
                BidComparisonProfile(
                    bid_id=reference.bid_id,
                    profile_id=reference.profile_id,
                    extraction_run_id=reference.extraction_run_id,
                    addendum_analysis_run_id=reference.addendum_analysis_run_id,
                    validation_run_id=reference.validation_run_id,
                    profile_status=reference.profile_status,
                )
            )
        await self._session.commit()

    async def save_report(self, report: BidComparisonReport) -> None:
        run = await self._session.get(BidComparisonRun, report.comparison_id)
        if run is None:
            raise RuntimeError("comparison run does not exist")
        run.status = report.status.value
        run.result = report.model_dump(mode="json")
        run.warnings = report.warnings
        run.errors = report.errors
        run.completed_at = report.created_at
        await self._session.commit()

    async def save_failure(self, state: BidComparisonState) -> None:
        run = await self._session.get(BidComparisonRun, state["comparison_id"])
        if run is None:
            raise RuntimeError("comparison run does not exist")
        run.status = "failed"
        run.warnings = state["warnings"]
        run.errors = state["errors"]
        run.error_code = state["error_code"]
        run.completed_at = state["completed_at"]
        await self._session.commit()

    async def _validate_reference(
        self, reference: ComparisonProfileReference
    ) -> None:
        checks = (
            (ExtractionRun, reference.extraction_run_id, "extraction"),
            (AddendumAnalysisRun, reference.addendum_analysis_run_id, "addendum"),
            (ValidationRun, reference.validation_run_id, "validation"),
        )
        for model, run_id, label in checks:
            if run_id is None:
                continue
            owner = await self._session.scalar(
                select(model.bid_id).where(model.id == run_id)
            )
            if owner != reference.bid_id:
                raise ComparisonLineageError(
                    f"{label} run does not belong to bid {reference.bid_id}"
                )
