"""Extraction API delegates to the service and exposes safe structured errors."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.extraction import get_extraction_service
from app.config import settings
from app.main import app
from app.schemas.extraction import (
    ASSIGNMENT_FIELD_NAMES,
    BidExtraction,
    ExtractionResponse,
)
from app.services.extraction_service import (
    BidNotFound,
    BidNotReady,
    ExtractionUnavailable,
    InvalidExtractionResponse,
)

PATH = f"{settings.api_prefix}/v1/bids"


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _response(bid_id):
    now = datetime.now(UTC)
    missing = {
        name: {
            "value": None,
            "confidence": None,
            "citations": [],
            "notes": "Not established by source evidence.",
            "validation_status": "missing",
        }
        for name in ASSIGNMENT_FIELD_NAMES
    }
    return ExtractionResponse(
        run_id=uuid4(),
        bid_id=bid_id,
        status="completed",
        model_name="mock-model",
        document_ids=[uuid4()],
        started_at=now,
        completed_at=now,
        fields=BidExtraction.model_validate(missing),
    )


def test_extract_endpoint_serializes_all_exact_external_field_names(client: TestClient) -> None:
    bid_id = uuid4()

    class Service:
        async def extract(self, requested):
            assert requested == bid_id
            return _response(bid_id)

    app.dependency_overrides[get_extraction_service] = lambda: Service()
    response = client.post(f"{PATH}/{bid_id}/extract")

    assert response.status_code == 200
    body = response.json()
    assert body["bid_id"] == str(bid_id)
    assert body["status"] == "completed"
    assert tuple(body["fields"]) == ASSIGNMENT_FIELD_NAMES
    assert body["fields"]["Bid Number"]["value"] is None
    assert "prompt" not in response.text.lower()
    assert "sk-" not in response.text
    assert "Traceback" not in response.text


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (BidNotFound("bid is not registered"), 404, None),
        (BidNotReady("bid has no eligible ingested documents with chunks"), 409, None),
        (
            InvalidExtractionResponse("LLM returned malformed structured extraction data"),
            502,
            "invalid_extraction_response",
        ),
        (
            ExtractionUnavailable("extraction service is unavailable"),
            503,
            "extraction_unavailable",
        ),
    ],
)
def test_extract_endpoint_maps_safe_errors(
    client: TestClient,
    error: Exception,
    status_code: int,
    code: str | None,
) -> None:
    class Service:
        async def extract(self, _bid_id):
            raise error

    app.dependency_overrides[get_extraction_service] = lambda: Service()
    response = client.post(f"{PATH}/{uuid4()}/extract")

    assert response.status_code == status_code
    if code is not None:
        assert response.json()["detail"]["code"] == code
    assert "Traceback" not in response.text
    assert "postgresql://" not in response.text


def test_extract_endpoint_rejects_invalid_bid_uuid(client: TestClient) -> None:
    response = client.post(f"{PATH}/not-a-uuid/extract")
    assert response.status_code == 422
