"""Indexing endpoints. The service is replaced so these tests do not call providers."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.indexing import get_indexing_service
from app.config import settings
from app.ingestion.indexing.indexing_service import IndexingNotFound
from app.main import app
from app.schemas.indexing import IndexingSummary

INDEX_PATH = f"{settings.api_prefix}/v1/indexing"


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_document_index_returns_counts_without_chunk_text(client: TestClient) -> None:
    document_id = uuid4()
    bid_id = uuid4()

    class Service:
        async def index_document(self, requested):
            assert requested == document_id
            return IndexingSummary(
                bid_id=bid_id,
                document_id=document_id,
                chunks_requested=1,
                chunks_embedded=1,
                points_upserted=1,
                ingestion_status="indexed",
            )

        async def index_bid(self, _bid_id):
            raise AssertionError("bid indexing was not requested")

    app.dependency_overrides[get_indexing_service] = lambda: Service()
    response = client.post(f"{INDEX_PATH}/documents/{document_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["document_id"] == str(document_id)
    assert body["points_upserted"] == 1
    assert body["ingestion_status"] == "indexed"
    assert "Traceback" not in response.text
    assert "sk-" not in response.text


def test_unknown_document_is_not_found(client: TestClient) -> None:
    class Service:
        async def index_document(self, _document_id):
            raise IndexingNotFound("document is not registered")

        async def index_bid(self, _bid_id):
            raise IndexingNotFound("bid is not registered")

    app.dependency_overrides[get_indexing_service] = lambda: Service()
    missing = client.post(f"{INDEX_PATH}/documents/{uuid4()}")
    invalid = client.post(f"{INDEX_PATH}/documents/not-a-uuid")
    missing_bid = client.post(f"{INDEX_PATH}/bids/{uuid4()}")

    assert missing.status_code == 404
    assert missing.json()["detail"] == "document is not registered"
    assert invalid.status_code == 422
    assert missing_bid.status_code == 404
    assert missing_bid.json()["detail"] == "bid is not registered"
    assert "Traceback" not in missing.text
