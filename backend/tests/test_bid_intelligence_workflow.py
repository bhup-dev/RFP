import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.schemas.extraction import ASSIGNMENT_FIELD_NAMES
from app.schemas.intelligence import (
    BidIntelligenceProfile,
    IntelligenceProfileField,
    IntelligenceProfileMetadata,
)
from app.services.addendum_analysis_service import NoAddendaAvailable
from app.services.bid_intelligence_workflow import BidIntelligenceWorkflow


class Stage:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    async def extract(self, bid_id):
        return await self._call((bid_id,))

    async def analyze(self, bid_id):
        return await self._call((bid_id,))

    async def validate(self, bid_id, run_id):
        return await self._call((bid_id, run_id))

    async def build(self, bid_id, run_id=None):
        return await self._call((bid_id, run_id))

    async def _call(self, args):
        self.calls.append(args)
        if self.error:
            raise self.error
        return self.result


def _profile(bid_id, extraction_run, validation_run):
    return BidIntelligenceProfile(
        metadata=IntelligenceProfileMetadata(
            bid_id=bid_id,
            bid_identifier="unseen-synthetic-folder",
            profile_id=uuid4(),
            generated_at=datetime.now(UTC),
            extraction_run_id=extraction_run,
            validation_run_id=validation_run,
            source_document_count=1,
            source_document_ids=[uuid4()],
            status="needs_review",
            review_required=True,
        ),
        fields={
            name: IntelligenceProfileField(
                value=None,
                validation_status="not_found",
                review_recommendation="Review if required.",
            )
            for name in ASSIGNMENT_FIELD_NAMES
        },
    )


def test_complete_workflow_preserves_upstream_run_references():
    bid_id, extraction_id, addendum_id, validation_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    profile = _profile(bid_id, extraction_id, validation_id)
    workflow = BidIntelligenceWorkflow(
        Stage(SimpleNamespace(run_id=extraction_id)),
        Stage(SimpleNamespace(run_id=addendum_id)),
        Stage(SimpleNamespace(validation_run_id=validation_id)),
        Stage(profile),
    )
    result = asyncio.run(workflow.execute(bid_id))
    assert result.status.value == "completed"
    assert [stage.run_id for stage in result.stages[:3]] == [
        extraction_id,
        addendum_id,
        validation_id,
    ]
    assert result.profile is not None
    assert result.profile.metadata.extraction_run_id == extraction_id
    assert result.profile.metadata.validation_run_id == validation_id


def test_no_addenda_is_a_valid_skipped_stage():
    bid_id, extraction_id, validation_id = uuid4(), uuid4(), uuid4()
    result = asyncio.run(
        BidIntelligenceWorkflow(
            Stage(SimpleNamespace(run_id=extraction_id)),
            Stage(error=NoAddendaAvailable("none")),
            Stage(SimpleNamespace(validation_run_id=validation_id)),
            Stage(_profile(bid_id, extraction_id, validation_id)),
        ).execute(bid_id)
    )
    assert result.status.value == "completed"
    assert result.stages[1].status.value == "skipped"


def test_addendum_failure_stops_validation_and_profile():
    validation, profile = Stage(), Stage()
    result = asyncio.run(
        BidIntelligenceWorkflow(
            Stage(SimpleNamespace(run_id=uuid4())),
            Stage(error=RuntimeError("provider failed")),
            validation,
            profile,
        ).execute(uuid4())
    )
    assert result.status.value == "failed"
    assert result.stages[-1].name == "addendum_analysis"
    assert validation.calls == profile.calls == []
    assert result.profile is None


def test_validation_timeout_is_reported_as_failed_not_completed():
    profile = Stage()
    result = asyncio.run(
        BidIntelligenceWorkflow(
            Stage(SimpleNamespace(run_id=uuid4())),
            Stage(error=NoAddendaAvailable("none")),
            Stage(error=TimeoutError()),
            profile,
        ).execute(uuid4())
    )
    assert result.status.value == "failed"
    assert result.stages[-1].name == "validation"
    assert result.stages[-1].status.value == "failed"
    assert profile.calls == []


def test_missing_required_documents_fails_at_extraction():
    result = asyncio.run(
        BidIntelligenceWorkflow(
            Stage(error=RuntimeError("bid has no eligible documents")),
            Stage(),
            Stage(),
            Stage(),
        ).execute(uuid4())
    )
    assert result.status.value == "failed"
    assert len(result.stages) == 1
    assert result.stages[0].name == "extraction"
