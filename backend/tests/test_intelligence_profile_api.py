from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.intelligence_profile import get_bid_intelligence_service
from app.config import settings
from app.main import app
from app.schemas.extraction import ASSIGNMENT_FIELD_NAMES
from app.schemas.intelligence import (
    BidIntelligenceProfile,
    IntelligenceProfileField,
    IntelligenceProfileMetadata,
)
from app.services.bid_intelligence_service import (
    IntelligenceBidNotFound,
    IntelligenceExtractionBidMismatch,
    IntelligenceExtractionNotFound,
)

PATH = f"{settings.api_prefix}/v1/bids"


@pytest.fixture
def client():
    with TestClient(app) as value:
        yield value
    app.dependency_overrides.clear()


def _profile(bid_id):
    return BidIntelligenceProfile(
        metadata=IntelligenceProfileMetadata(
            bid_id=bid_id,
            bid_identifier="synthetic",
            profile_id=uuid4(),
            generated_at=datetime.now(UTC),
            source_document_count=0,
            status="incomplete",
            review_required=True,
        ),
        fields={
            name: IntelligenceProfileField(
                value=None,
                validation_status="unvalidated",
                review_recommendation="Run extraction.",
            )
            for name in ASSIGNMENT_FIELD_NAMES
        },
    )


def test_profile_endpoint_is_read_only_and_serializes_exact_fields(client):
    bid_id, extraction_id = uuid4(), uuid4()

    class Service:
        async def build(self, requested_bid, requested_extraction):
            assert (requested_bid, requested_extraction) == (bid_id, extraction_id)
            return _profile(bid_id)

    app.dependency_overrides[get_bid_intelligence_service] = lambda: Service()
    response = client.get(
        f"{PATH}/{bid_id}/intelligence-profile",
        params={"extraction_run_id": str(extraction_id)},
    )
    assert response.status_code == 200
    assert tuple(response.json()["fields"]) == ASSIGNMENT_FIELD_NAMES
    assert "prompt" not in response.text.lower()
    assert "sk-" not in response.text


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (IntelligenceBidNotFound("bid missing"), 404),
        (IntelligenceExtractionNotFound("run missing"), 404),
        (IntelligenceExtractionBidMismatch("foreign run"), 409),
    ],
)
def test_profile_endpoint_maps_resource_errors(client, error, status):
    class Service:
        async def build(self, *_args):
            raise error

    app.dependency_overrides[get_bid_intelligence_service] = lambda: Service()
    response = client.get(f"{PATH}/{uuid4()}/intelligence-profile")
    assert response.status_code == status
    assert "Traceback" not in response.text


def test_profile_endpoint_rejects_invalid_uuid(client):
    assert client.get(f"{PATH}/bad/intelligence-profile").status_code == 422
