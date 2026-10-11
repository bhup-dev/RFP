from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.extraction_validation import get_extraction_validation_service
from app.config import settings
from app.main import app
from app.schemas.extraction import ASSIGNMENT_FIELD_NAMES
from app.schemas.validation import (
    ConfidenceCategory,
    ExtractionValidationReport,
    FieldValidationOutcome,
    ValidatedField,
    ValidationSummary,
)
from app.services.extraction_validation_service import (
    ExtractionRunBidMismatch,
    ExtractionRunNotFound,
    ValidationBidNotFound,
    ValidationUnavailable,
)

PATH = f"{settings.api_prefix}/v1/bids"


@pytest.fixture
def client():
    with TestClient(app) as value:
        yield value
    app.dependency_overrides.clear()


def _report(bid_id, extraction_id):
    validation_id = uuid4()
    now = datetime.now(UTC)
    fields = {
        name: ValidatedField(
            field_name=name,
            extracted_value=None,
            validation_status=FieldValidationOutcome.NOT_FOUND,
            confidence_category=ConfidenceCategory.UNAVAILABLE,
            validation_reasons=["No value."],
            recommended_action="Review if required.",
            extraction_run_id=extraction_id,
            validation_run_id=validation_id,
        )
        for name in ASSIGNMENT_FIELD_NAMES
    }
    return ExtractionValidationReport(
        validation_run_id=validation_id,
        extraction_run_id=extraction_id,
        bid_id=bid_id,
        status="completed",
        overall_status="needs_review",
        addendum_check_status="not_run",
        thresholds={"high": 0.8, "medium": 0.55},
        fields=fields,
        summary=ValidationSummary(
            total_fields=20,
            validated=0,
            partially_supported=0,
            unsupported=0,
            conflicting=0,
            not_found=20,
            needs_review=0,
            high_confidence=0,
            medium_confidence=0,
            low_confidence=0,
            unavailable_confidence=20,
        ),
        started_at=now,
        completed_at=now,
    )


def test_validation_endpoint_returns_exact_fields_without_secrets(client):
    bid_id, extraction_id = uuid4(), uuid4()

    class Service:
        async def validate(self, requested_bid, requested_run):
            assert (requested_bid, requested_run) == (bid_id, extraction_id)
            return _report(bid_id, extraction_id)

    app.dependency_overrides[get_extraction_validation_service] = lambda: Service()
    response = client.post(f"{PATH}/{bid_id}/extractions/{extraction_id}/validate")
    assert response.status_code == 200
    assert tuple(response.json()["fields"]) == ASSIGNMENT_FIELD_NAMES
    assert "prompt" not in response.text.lower()
    assert "sk-" not in response.text


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (ValidationBidNotFound("bid is not registered"), 404, None),
        (ExtractionRunNotFound("run does not exist"), 404, None),
        (ExtractionRunBidMismatch("run belongs to another bid"), 409, None),
        (ValidationUnavailable("validation unavailable"), 503, "validation_unavailable"),
    ],
)
def test_validation_endpoint_maps_safe_errors(client, error, status, code):
    class Service:
        async def validate(self, *_args):
            raise error

    app.dependency_overrides[get_extraction_validation_service] = lambda: Service()
    response = client.post(f"{PATH}/{uuid4()}/extractions/{uuid4()}/validate")
    assert response.status_code == status
    if code:
        assert response.json()["detail"]["code"] == code
    assert "Traceback" not in response.text


def test_validation_endpoint_rejects_invalid_uuid(client):
    assert client.post(f"{PATH}/bad/extractions/bad/validate").status_code == 422
