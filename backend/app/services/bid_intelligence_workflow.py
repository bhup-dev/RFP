"""Minimal coordinator for the four independently callable Task 3 stages."""

from typing import Protocol
from uuid import UUID

from app.schemas.intelligence import (
    BidIntelligenceWorkflowResult,
    IntelligenceWorkflowStage,
    IntelligenceWorkflowStageStatus,
    IntelligenceWorkflowStatus,
)
from app.services.addendum_analysis_service import NoAddendaAvailable


class ExtractionStage(Protocol):
    async def extract(self, bid_id: UUID): ...


class AddendumStage(Protocol):
    async def analyze(self, bid_id: UUID): ...


class ValidationStage(Protocol):
    async def validate(self, bid_id: UUID, extraction_run_id: UUID): ...


class ProfileStage(Protocol):
    async def build(self, bid_id: UUID, extraction_run_id: UUID | None = None): ...


class BidIntelligenceWorkflow:
    """Run extraction, addendum analysis, validation, then read-only consolidation."""

    def __init__(
        self,
        extraction: ExtractionStage,
        addendum: AddendumStage,
        validation: ValidationStage,
        profile: ProfileStage,
    ) -> None:
        self._extraction = extraction
        self._addendum = addendum
        self._validation = validation
        self._profile = profile

    async def execute(self, bid_id: UUID) -> BidIntelligenceWorkflowResult:
        stages: list[IntelligenceWorkflowStage] = []
        try:
            extraction = await self._extraction.extract(bid_id)
        except Exception as exc:
            return _failed(bid_id, stages, "extraction", exc)
        stages.append(
            IntelligenceWorkflowStage(
                name="extraction",
                status=IntelligenceWorkflowStageStatus.COMPLETED,
                run_id=extraction.run_id,
            )
        )

        try:
            addendum = await self._addendum.analyze(bid_id)
        except NoAddendaAvailable:
            stages.append(
                IntelligenceWorkflowStage(
                    name="addendum_analysis",
                    status=IntelligenceWorkflowStageStatus.SKIPPED,
                    detail="No explicitly identified addenda or clarifications",
                )
            )
        except Exception as exc:
            return _failed(bid_id, stages, "addendum_analysis", exc)
        else:
            stages.append(
                IntelligenceWorkflowStage(
                    name="addendum_analysis",
                    status=IntelligenceWorkflowStageStatus.COMPLETED,
                    run_id=addendum.run_id,
                )
            )

        try:
            validation = await self._validation.validate(bid_id, extraction.run_id)
        except Exception as exc:
            return _failed(bid_id, stages, "validation", exc)
        stages.append(
            IntelligenceWorkflowStage(
                name="validation",
                status=IntelligenceWorkflowStageStatus.COMPLETED,
                run_id=validation.validation_run_id,
            )
        )

        try:
            profile = await self._profile.build(bid_id, extraction.run_id)
        except Exception as exc:
            return _failed(bid_id, stages, "profile", exc)
        stages.append(
            IntelligenceWorkflowStage(
                name="profile",
                status=IntelligenceWorkflowStageStatus.COMPLETED,
                run_id=profile.metadata.profile_id,
            )
        )
        return BidIntelligenceWorkflowResult(
            bid_id=bid_id,
            status=IntelligenceWorkflowStatus.COMPLETED,
            stages=stages,
            profile=profile,
        )


def _failed(
    bid_id: UUID,
    stages: list[IntelligenceWorkflowStage],
    name: str,
    error: Exception,
) -> BidIntelligenceWorkflowResult:
    return BidIntelligenceWorkflowResult(
        bid_id=bid_id,
        status=IntelligenceWorkflowStatus.FAILED,
        stages=[
            *stages,
            IntelligenceWorkflowStage(
                name=name,
                status=IntelligenceWorkflowStageStatus.FAILED,
                detail=f"{name} failed ({type(error).__name__})",
            ),
        ],
    )
