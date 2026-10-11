"""End-to-end workflow API delegates to the coordinator."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.workflow import get_end_to_end_pipeline
from app.config import settings
from app.main import app
from app.schemas.workflow import EndToEndSummary, WorkflowDocumentResult

PATH = f"{settings.api_prefix}/v1/ingestion"


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class Pipeline:
    def __init__(self) -> None:
        self.calls = []

    async def process_directory(self, relative_directory=None, *, bid_identifier=None):
        self.calls.append((relative_directory, bid_identifier))
        return EndToEndSummary(files_discovered=0)

    async def process_registered_document(self, document_id):
        self.calls.append(document_id)
        return WorkflowDocumentResult(
            document_id=document_id,
            bid_id=uuid4(),
            relative_path="Bid/source.pdf",
            parsing="completed",
            chunking="completed",
            indexing="completed",
            completed=True,
        )


def test_process_directory_and_registered_document(client: TestClient) -> None:
    pipeline = Pipeline()
    app.dependency_overrides[get_end_to_end_pipeline] = lambda: pipeline
    directory = client.post(
        f"{PATH}/process",
        json={"relative_directory": "Bid", "bid_identifier": "internal-bid"},
    )
    document_id = uuid4()
    document = client.post(f"{PATH}/documents/{document_id}/process")

    assert directory.status_code == 200
    assert directory.json()["files_discovered"] == 0
    assert document.status_code == 200
    assert document.json()["document_id"] == str(document_id)
    assert document.json()["completed"] is True
    assert pipeline.calls == [("Bid", "internal-bid"), document_id]
    assert "storage_path" not in document.text


def test_unknown_registered_document_returns_404(client: TestClient) -> None:
    class Missing(Pipeline):
        async def process_registered_document(self, document_id):
            return WorkflowDocumentResult(
                document_id=document_id,
                relative_path="",
                errors=["document is not registered"],
            )

    app.dependency_overrides[get_end_to_end_pipeline] = lambda: Missing()
    response = client.post(f"{PATH}/documents/{uuid4()}/process")
    assert response.status_code == 404
    assert response.json()["detail"] == "document is not registered"
