"""Parse endpoint. Database access is replaced for these tests."""

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.ingestion import get_parse_stage, get_registered_source
from app.config import settings
from app.ingestion.parse_stage import DocumentParseStage
from app.ingestion.parsers.base import RegisteredSource
from app.main import app
from tests.pdf_samples import write_text_pdf

PARSE_PATH = f"{settings.api_prefix}/v1/ingestion/documents"


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_parse_endpoint_returns_page_text_without_host_paths(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "bids"
    path = root / "Alpha" / "notes.pdf"
    path.parent.mkdir(parents=True)
    secret = "SECRET_DOCUMENT_TEXT_SHOULD_NOT_BE_LOGGED"
    write_text_pdf(path, ["Visible specification text", secret])
    document_id = uuid4()
    source = RegisteredSource(
        document_id=document_id,
        relative_path="Alpha/notes.pdf",
        file_name="notes.pdf",
        document_type="pdf",
    )

    def source_override(document_id: object = document_id) -> RegisteredSource:
        return source

    app.dependency_overrides[get_registered_source] = source_override
    app.dependency_overrides[get_parse_stage] = lambda: DocumentParseStage(root)

    response = client.post(f"{PARSE_PATH}/{document_id}/parse")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "parsed"
    assert body["relative_path"] == "Alpha/notes.pdf"
    assert body["sections"][0]["page_number"] == 1
    assert body["sections"][0]["text"] == "Visible specification text"
    assert body["sections"][1]["page_number"] == 2
    assert body["sections"][1]["text"] == secret
    assert "Traceback" not in response.text
    assert str(tmp_path) not in response.text


def test_unknown_document_returns_a_safe_not_found(client: TestClient) -> None:
    from fastapi import HTTPException

    def missing(document_id: object) -> RegisteredSource:
        raise HTTPException(status_code=404, detail="document is not registered")

    app.dependency_overrides[get_registered_source] = missing
    response = client.post(f"{PARSE_PATH}/{uuid4()}/parse")

    assert response.status_code == 404
    assert response.json()["detail"] == "document is not registered"
    assert "Traceback" not in response.text
