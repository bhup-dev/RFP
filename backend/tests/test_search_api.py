"""Search route delegates to the retrieval service and returns safe failures."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.search import get_retrieval_service
from app.config import settings
from app.main import app
from app.retrieval.base import RetrievalUnavailable
from app.schemas.retrieval import RetrievalResponse

PATH = f"{settings.api_prefix}/v1/search"


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_search_endpoint_returns_structured_response(client: TestClient) -> None:
    bid_id = uuid4()

    class Service:
        async def search(self, request):
            return RetrievalResponse(
                query=request.query,
                bid_id=request.bid_id,
                retrieval_mode="hybrid",
                applied_filters={"bid_id": request.bid_id},
                diagnostics={"rrf_constant": 60},
            )

    app.dependency_overrides[get_retrieval_service] = lambda: Service()
    response = client.post(PATH, json={"query": " payment terms ", "bid_id": str(bid_id)})
    assert response.status_code == 200
    assert response.json()["query"] == "payment terms"
    assert response.json()["retrieval_mode"] == "hybrid"
    assert response.json()["applied_filters"]["bid_id"] == str(bid_id)
    assert "Traceback" not in response.text


def test_both_backends_unavailable_returns_503(client: TestClient) -> None:
    class Service:
        async def search(self, request):
            raise RetrievalUnavailable(
                ["Semantic index is unavailable", "Keyword search is unavailable"]
            )

    app.dependency_overrides[get_retrieval_service] = lambda: Service()
    response = client.post(PATH, json={"query": "delivery"})
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "retrieval_unavailable"
    assert "Semantic index is unavailable" in detail["errors"]
    assert "Traceback" not in response.text
    assert "postgresql://" not in response.text


def test_search_request_validation(client: TestClient) -> None:
    assert client.post(PATH, json={"query": "   "}).status_code == 422
    assert client.post(PATH, json={"query": "x", "top_k": 51}).status_code == 422
    assert client.post(PATH, json={"query": "x", "document_ids": ["not-a-uuid"]}).status_code == 422
