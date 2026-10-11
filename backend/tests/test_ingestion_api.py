"""HTTP scan endpoint. The database dependency is replaced for these tests."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.ingestion import get_ingestion_pipeline
from app.config import settings
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.registry import SqlDocumentRegistry
from app.main import app
from tests.ingestion_doubles import RecordingSession

SCAN_PATH = f"{settings.api_prefix}/v1/ingestion/scan"


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _use_pipeline(root: Path) -> RecordingSession:
    session = RecordingSession()
    pipeline = IngestionPipeline(SqlDocumentRegistry(session), root)
    app.dependency_overrides[get_ingestion_pipeline] = lambda: pipeline
    return session


def test_scan_returns_a_structured_summary(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "bids"
    source = root / "Alpha"
    source.mkdir(parents=True)
    secret = b"SECRET_DOCUMENT_TEXT_SHOULD_NOT_BE_LOGGED"
    (source / "notes.pdf").write_bytes(secret)
    (source / "skip.txt").write_bytes(b"text")
    session = _use_pipeline(root)

    response = client.post(
        SCAN_PATH,
        json={"relative_directory": "Alpha"},
        headers={"X-Request-ID": "ingest-scan-01"},
    )
    repeated = client.post(SCAN_PATH, json={"relative_directory": "Alpha"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "ingest-scan-01"
    body = response.json()
    assert body["input_root"] == "bids"
    assert body["files_discovered"] == 2
    assert body["files_registered"] == 1
    assert body["unsupported_files"] == 1
    assert body["failed_files"] == 0
    registered = next(item for item in body["results"] if item["outcome"] == "registered")
    assert registered["relative_path"] == "Alpha/notes.pdf"
    assert registered["document_type"] == "pdf"
    assert registered["document_id"] == str(session.documents[0].id)
    assert registered["file_hash"] == session.documents[0].file_hash
    assert "Traceback" not in response.text
    assert str(tmp_path) not in response.text
    assert "SECRET_DOCUMENT_TEXT_SHOULD_NOT_BE_LOGGED" not in response.text
    assert repeated.status_code == 200
    assert repeated.json()["files_registered"] == 0
    assert repeated.json()["duplicates_skipped"] == 1
    assert len(session.documents) == 1


def test_scan_rejects_directories_outside_the_input_root(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "bids"
    (root / "Alpha").mkdir(parents=True)
    _use_pipeline(root)

    for relative in ("../outside", "/etc", "Alpha/../../outside"):
        response = client.post(SCAN_PATH, json={"relative_directory": relative})
        assert response.status_code == 400
        assert response.json()["detail"] == "directory must stay inside the configured input root"
        assert "Traceback" not in response.text
        assert str(tmp_path) not in response.text


def test_missing_input_root_and_bad_grouping_id_are_safe_errors(client: TestClient, tmp_path: Path) -> None:
    _use_pipeline(tmp_path / "missing")
    missing = client.post(SCAN_PATH, json={})
    assert missing.status_code == 400
    assert missing.json()["detail"] == "configured input root is not a directory"
    assert "Traceback" not in missing.text
    assert str(tmp_path) not in missing.text

    root = tmp_path / "bids"
    (root / "Alpha").mkdir(parents=True)
    (root / "Alpha" / "notes.pdf").write_bytes(b"pdf")
    _use_pipeline(root)
    rejected = client.post(SCAN_PATH, json={"bid_identifier": ".."})
    assert rejected.status_code == 400
    assert "Traceback" not in rejected.text
    assert str(tmp_path) not in rejected.text
