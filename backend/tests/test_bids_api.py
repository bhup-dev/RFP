"""Bids, uploads, documents, and jobs API with dependency-override fakes."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.bids import get_bid_catalog, get_job_repository, get_upload_service
from app.config import settings
from app.db.models.bid import Bid
from app.db.models.document import Document
from app.db.models.job import ProcessingJob
from app.jobs.repository import ActiveJobExists
from app.main import app
from app.schemas.bids import BidSummary
from app.services.bid_catalog import BidIdentifierTaken, BidPage
from app.services.upload_service import UploadOutcome

PDF = b"%PDF-1.7\n%%EOF\n"


def _job(bid_id: UUID, **overrides) -> ProcessingJob:
    values = dict(
        id=uuid4(),
        bid_id=bid_id,
        job_type="ingest_and_analyze",
        status="queued",
        stage="queued",
        attempts=0,
        max_attempts=3,
        result_summary={},
        created_at=datetime.now(UTC),
    )
    values.update(overrides)
    return ProcessingJob(**values)


class FakeCatalog:
    def __init__(self) -> None:
        self.bids: dict[UUID, Bid] = {}
        self.documents: list[Document] = []

    def add_bid(self, identifier: str = "City RFP") -> Bid:
        bid = Bid(id=uuid4(), bid_identifier=identifier, created_at=datetime.now(UTC))
        self.bids[bid.id] = bid
        return bid

    async def create_bid(self, data):
        for bid in self.bids.values():
            if bid.bid_identifier == data.bid_identifier:
                raise BidIdentifierTaken(bid)
        bid = self.add_bid(data.bid_identifier)
        bid.title = data.title
        return bid

    async def get_bid(self, bid_id):
        return self.bids.get(bid_id)

    async def summaries(self, bids):
        return [
            BidSummary(
                id=bid.id,
                bid_identifier=bid.bid_identifier,
                title=bid.title,
                created_at=bid.created_at,
                document_count=sum(1 for d in self.documents if d.bid_id == bid.id),
            )
            for bid in bids
        ]

    async def list_bids(self, *, limit, offset, search):
        items = [
            bid
            for bid in self.bids.values()
            if not search or search.lower() in bid.bid_identifier.lower()
        ]
        return BidPage(items=await self.summaries(items[offset : offset + limit]), total=len(items))

    async def list_documents(self, bid_id):
        return [d for d in self.documents if d.bid_id == bid_id]

    async def get_document(self, document_id):
        return next((d for d in self.documents if d.id == document_id), None)


class FakeJobs:
    def __init__(self) -> None:
        self.jobs: list[ProcessingJob] = []

    async def enqueue(self, bid_id, job_type, *, max_attempts):
        active = await self.active_for_bid(bid_id)
        if active is not None:
            raise ActiveJobExists(active)
        job = _job(bid_id, job_type=job_type, max_attempts=max_attempts)
        self.jobs.append(job)
        return job

    async def active_for_bid(self, bid_id):
        return next(
            (j for j in self.jobs if j.bid_id == bid_id and j.status in {"queued", "running"}),
            None,
        )

    async def list_for_bid(self, bid_id, *, limit=20):
        return [j for j in self.jobs if j.bid_id == bid_id][:limit]

    async def list_recent(self, *, limit=10):
        return self.jobs[:limit]

    async def get(self, job_id):
        return next((j for j in self.jobs if j.id == job_id), None)


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class FakeUploads:
    def __init__(self) -> None:
        self.session = FakeSession()
        self.calls: list = []
        self.discarded = False
        self.fail = False

    async def upload(self, bid, items):
        self.calls.append(items)
        if self.fail:
            raise RuntimeError("database went away")
        outcomes = []
        for item in items:
            data = await item.source.read()
            if data.startswith(b"%PDF-"):
                outcomes.append(
                    UploadOutcome(
                        original_filename=item.source.filename,
                        outcome="registered",
                        document_id=uuid4(),
                        stored_name=f"abc_{item.source.filename}",
                        size_bytes=len(data),
                        role=item.role if item.role not in (None, "auto") else None,
                    )
                )
            else:
                outcomes.append(
                    UploadOutcome(
                        original_filename=item.source.filename,
                        outcome="rejected",
                        reason="file content is not a valid PDF file",
                    )
                )
        return outcomes

    def discard_written_files(self) -> None:
        self.discarded = True


@pytest.fixture
def fakes():
    catalog, jobs, uploads = FakeCatalog(), FakeJobs(), FakeUploads()
    app.dependency_overrides[get_bid_catalog] = lambda: catalog
    app.dependency_overrides[get_job_repository] = lambda: jobs
    app.dependency_overrides[get_upload_service] = lambda: uploads
    with TestClient(app) as client:
        yield client, catalog, jobs, uploads
    app.dependency_overrides.clear()


def test_create_bid_validates_identifier_and_rejects_duplicates(fakes) -> None:
    client, catalog, _, _ = fakes

    created = client.post("/api/v1/bids", json={"bid_identifier": " City RFP ", "title": "Docks"})
    assert created.status_code == 201
    assert created.json()["bid_identifier"] == "City RFP"
    assert created.json()["title"] == "Docks"

    duplicate = client.post("/api/v1/bids", json={"bid_identifier": "City RFP"})
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["bid_id"] == created.json()["id"]

    for bad in ["../etc", ".hidden", "a/b", "", "x" * 129]:
        assert client.post("/api/v1/bids", json={"bid_identifier": bad}).status_code == 422


def test_list_and_detail_bids(fakes) -> None:
    client, catalog, jobs, _ = fakes
    bid = catalog.add_bid("Alpha")
    catalog.add_bid("Beta")
    jobs.jobs.append(_job(bid.id, status="succeeded", stage="done"))

    listing = client.get("/api/v1/bids", params={"search": "alp"})
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    assert body["items"][0]["bid_identifier"] == "Alpha"

    detail = client.get(f"/api/v1/bids/{bid.id}")
    assert detail.status_code == 200
    assert detail.json()["jobs"][0]["status"] == "succeeded"
    assert client.get(f"/api/v1/bids/{uuid4()}").status_code == 404
    assert client.get("/api/v1/bids", params={"limit": 0}).status_code == 422


def test_upload_reports_per_file_outcomes_and_commits(fakes) -> None:
    client, catalog, _, uploads = fakes
    bid = catalog.add_bid()

    response = client.post(
        f"/api/v1/bids/{bid.id}/documents",
        files=[
            ("files", ("rfp.pdf", PDF, "application/pdf")),
            ("files", ("fake.pdf", b"not a pdf", "application/pdf")),
        ],
        data={"roles": ["original", "auto"], "addendum_numbers": ["", ""]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["registered"] == 1
    assert body["rejected"] == 1
    assert body["results"][0]["role"] == "original"
    assert "not a valid PDF" in body["results"][1]["reason"]
    assert uploads.session.commits == 1


def test_upload_guards(fakes) -> None:
    client, catalog, jobs, uploads = fakes
    bid = catalog.add_bid()
    url = f"/api/v1/bids/{bid.id}/documents"
    pdf = ("files", ("rfp.pdf", PDF, "application/pdf"))

    assert client.post(f"/api/v1/bids/{uuid4()}/documents", files=[pdf]).status_code == 404
    assert client.post(url, files=[pdf], data={"roles": ["a", "b"]}).status_code == 422
    assert client.post(url, files=[pdf], data={"addendum_numbers": ["two"]}).status_code == 422
    too_many = [pdf] * (settings.upload_max_files_per_request + 1)
    assert client.post(url, files=too_many).status_code == 413

    jobs.jobs.append(_job(bid.id, status="running"))
    busy = client.post(url, files=[pdf])
    assert busy.status_code == 409
    assert busy.json()["detail"]["job_id"] == str(jobs.jobs[0].id)
    assert uploads.calls == []


def test_upload_failure_discards_written_files(fakes) -> None:
    client, catalog, _, uploads = fakes
    bid = catalog.add_bid()
    uploads.fail = True

    with pytest.raises(RuntimeError):
        client.post(
            f"/api/v1/bids/{bid.id}/documents",
            files=[("files", ("rfp.pdf", PDF, "application/pdf"))],
        )
    assert uploads.discarded is True


def test_process_enqueues_once_and_reports_conflicts(fakes) -> None:
    client, catalog, jobs, _ = fakes
    bid = catalog.add_bid()

    no_documents = client.post(f"/api/v1/bids/{bid.id}/process")
    assert no_documents.status_code == 409

    catalog.documents.append(
        Document(id=uuid4(), bid_id=bid.id, file_name="a.pdf", storage_path="x/a.pdf")
    )
    started = client.post(f"/api/v1/bids/{bid.id}/process")
    assert started.status_code == 202
    assert started.json()["status"] == "queued"
    assert started.json()["job_type"] == "ingest_and_analyze"

    again = client.post(f"/api/v1/bids/{bid.id}/process", json={"job_type": "analyze_only"})
    assert again.status_code == 409
    assert again.json()["detail"]["job_id"] == started.json()["id"]

    bad_type = client.post(f"/api/v1/bids/{bid.id}/process", json={"job_type": "delete"})
    assert bad_type.status_code == 422
    assert client.post(f"/api/v1/bids/{uuid4()}/process").status_code == 404


def test_job_endpoints(fakes) -> None:
    client, catalog, jobs, _ = fakes
    bid = catalog.add_bid()
    job = _job(bid.id, status="failed", error_code="ingestion_failed", error_detail="bad pdf")
    jobs.jobs.append(job)

    single = client.get(f"/api/v1/jobs/{job.id}")
    assert single.status_code == 200
    assert single.json()["error_detail"] == "bad pdf"
    assert client.get(f"/api/v1/jobs/{uuid4()}").status_code == 404
    assert len(client.get(f"/api/v1/bids/{bid.id}/jobs").json()) == 1
    assert len(client.get("/api/v1/jobs").json()) == 1


def test_download_serves_pdf_inline_and_html_as_sandboxed_attachment(
    fakes, tmp_path: Path, monkeypatch
) -> None:
    client, catalog, _, _ = fakes
    monkeypatch.setattr(settings, "ingestion_input_root", tmp_path)
    bid = catalog.add_bid()
    (tmp_path / "City RFP").mkdir()
    (tmp_path / "City RFP" / "abc_rfp.pdf").write_bytes(PDF)
    (tmp_path / "City RFP" / "abc_add.html").write_bytes(b"<html><script>x</script></html>")
    pdf_doc = Document(
        id=uuid4(),
        bid_id=bid.id,
        file_name="abc_rfp.pdf",
        original_filename="RFP.pdf",
        document_type="pdf",
        storage_path="City RFP/abc_rfp.pdf",
    )
    html_doc = Document(
        id=uuid4(),
        bid_id=bid.id,
        file_name="abc_add.html",
        document_type="html",
        storage_path="City RFP/abc_add.html",
    )
    escaped = Document(
        id=uuid4(),
        bid_id=bid.id,
        file_name="passwd",
        document_type="pdf",
        storage_path="../../etc/passwd",
    )
    catalog.documents.extend([pdf_doc, html_doc, escaped])

    pdf = client.get(f"/api/v1/documents/{pdf_doc.id}/file")
    assert pdf.status_code == 200
    assert pdf.content == PDF
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["content-disposition"].startswith("inline")
    assert "RFP.pdf" in pdf.headers["content-disposition"]

    html = client.get(f"/api/v1/documents/{html_doc.id}/file")
    assert html.headers["content-disposition"].startswith("attachment")
    assert html.headers["content-security-policy"] == "sandbox"
    assert html.headers["x-content-type-options"] == "nosniff"

    assert client.get(f"/api/v1/documents/{escaped.id}/file").status_code == 404
    assert client.get(f"/api/v1/documents/{uuid4()}/file").status_code == 404


def test_upload_limits_are_published(fakes) -> None:
    client, *_ = fakes
    body = client.get("/api/v1/uploads/limits").json()
    assert body["max_file_bytes"] == settings.upload_max_file_bytes
    assert ".pdf" in body["allowed_extensions"]
    assert body["roles"][0] == "auto"
