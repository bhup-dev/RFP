"""Chunk endpoint. Database access is replaced for these tests."""

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.ingestion import get_chunk_document, get_chunk_pipeline, get_parse_stage
from app.config import settings
from app.ingestion.chunking.base import ChunkDocument
from app.ingestion.chunking.pipeline import ChunkingPipeline
from app.ingestion.parse_stage import DocumentParseStage
from app.main import app
from tests.pdf_samples import write_text_pdf
from tests.test_chunking_pipeline import MemoryChunkStore

CHUNK_PATH = f"{settings.api_prefix}/v1/ingestion/documents"


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_chunk_endpoint_returns_counts_without_host_paths(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "bids"
    path = root / "Open_Bid" / "notes.pdf"
    path.parent.mkdir(parents=True)
    write_text_pdf(path, ["Visible specification text"])
    document_id = uuid4()
    record = ChunkDocument(
        document_id=document_id,
        bid_id=uuid4(),
        file_name="notes.pdf",
        file_type="pdf",
        relative_path="Open_Bid/notes.pdf",
    )
    store = MemoryChunkStore()
    app.dependency_overrides[get_chunk_document] = lambda document_id=document_id: record
    app.dependency_overrides[get_parse_stage] = lambda: DocumentParseStage(root)
    app.dependency_overrides[get_chunk_pipeline] = lambda: ChunkingPipeline(
        store,
        chunk_size=1000,
        chunk_overlap=0,
        table_chunk_max_rows=20,
        chunk_min_text_length=50,
    )

    response = client.post(f"{CHUNK_PATH}/{document_id}/chunk")
    repeated = client.post(f"{CHUNK_PATH}/{document_id}/chunk")

    assert response.status_code == 200
    body = response.json()
    assert body["document_id"] == str(document_id)
    assert body["chunks_created"] == 1
    assert body["text_chunks_created"] == 1
    assert body["errors"] == []
    assert repeated.json()["chunks_created"] == 0
    assert repeated.json()["chunks_skipped"] == 1
    assert store.rows[document_id][0].text == "Visible specification text"
    assert "Traceback" not in response.text
    assert str(tmp_path) not in response.text
    assert store.rows[document_id][0].metadata["relative_path"] == "Open_Bid/notes.pdf"
