"""Addendum analysis API returns structured results and safe failures."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.addendum_analysis import get_addendum_analysis_service
from app.config import settings
from app.main import app
from app.schemas.addendum import (
    AddendumAnalysisResponse,
    AddendumDocumentInfo,
    AddendumDocumentRole,
)
from app.services.addendum_analysis_service import (
    AddendumAnalysisUnavailable,
    AddendumBidNotFound,
    AddendumBidNotReady,
    InvalidAddendumResponse,
    NoAddendaAvailable,
)

PATH = f"{settings.api_prefix}/v1/bids"


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _response(bid_id):
    now = datetime.now(UTC)
    return AddendumAnalysisResponse(
        run_id=uuid4(),
        bid_id=bid_id,
        status="completed",
        model_name="mock-model",
        original_documents=[
            AddendumDocumentInfo(
                document_id=uuid4(),
                file_name="solicitation.pdf",
                role=AddendumDocumentRole.ORIGINAL,
                document_type="solicitation",
            )
        ],
        addendum_documents=[
            AddendumDocumentInfo(
                document_id=uuid4(),
                file_name="notice.pdf",
                role=AddendumDocumentRole.ADDENDUM,
                document_type="addendum",
                addendum_number=1,
            )
        ],
        relationship_uncertain=False,
        ordering_uncertain=False,
        findings=[],
        warnings=[],
        started_at=now,
        completed_at=now,
    )


def test_analyze_addenda_endpoint_returns_traceable_response(client: TestClient) -> None:
    bid_id = uuid4()

    class Service:
        async def analyze(self, requested):
            assert requested == bid_id
            return _response(bid_id)

    app.dependency_overrides[get_addendum_analysis_service] = lambda: Service()
    response = client.post(f"{PATH}/{bid_id}/analyze-addenda")

    assert response.status_code == 200
    body = response.json()
    assert body["bid_id"] == str(bid_id)
    assert body["status"] == "completed"
    assert body["original_documents"][0]["role"] == "original"
    assert body["addendum_documents"][0]["role"] == "addendum"
    assert "prompt" not in response.text.lower()
    assert "sk-" not in response.text
    assert "Traceback" not in response.text


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (AddendumBidNotFound("bid is not registered"), 404, None),
        (AddendumBidNotReady("bid has no eligible documents"), 409, None),
        (NoAddendaAvailable("bid has no addenda"), 409, None),
        (
            InvalidAddendumResponse("LLM returned malformed addendum finding data"),
            502,
            "invalid_addendum_response",
        ),
        (
            AddendumAnalysisUnavailable("addendum analysis service is unavailable"),
            503,
            "addendum_analysis_unavailable",
        ),
    ],
)
def test_analyze_addenda_endpoint_maps_safe_errors(
    client: TestClient,
    error: Exception,
    status_code: int,
    code: str | None,
) -> None:
    class Service:
        async def analyze(self, _bid_id):
            raise error

    app.dependency_overrides[get_addendum_analysis_service] = lambda: Service()
    response = client.post(f"{PATH}/{uuid4()}/analyze-addenda")

    assert response.status_code == status_code
    if code is not None:
        assert response.json()["detail"]["code"] == code
    assert "Traceback" not in response.text
    assert "postgresql://" not in response.text


def test_analyze_addenda_rejects_invalid_uuid(client: TestClient) -> None:
    response = client.post(f"{PATH}/not-a-uuid/analyze-addenda")
    assert response.status_code == 422
