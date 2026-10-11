"""Upload validation and storage. Uses a temp directory and an in-memory session."""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from app.db.models.bid import Bid
from app.services.upload_service import (
    OUTCOME_DUPLICATE,
    OUTCOME_REGISTERED,
    OUTCOME_REJECTED,
    UploadRejected,
    UploadRequestItem,
    UploadService,
    content_matches_extension,
    normalize_role,
    sanitize_filename,
)
from tests.ingestion_doubles import RecordingSession

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
HTML = b"<!DOCTYPE html><html><body><p>Addendum No. 1</p></body></html>"


class FakeUpload:
    def __init__(self, filename: str | None, data: bytes, chunk: int = 7) -> None:
        self.filename = filename
        self._data = data
        self._offset = 0
        self._chunk = chunk

    async def read(self, size: int = -1) -> bytes:
        size = self._chunk if size < 0 else min(size, self._chunk)
        block = self._data[self._offset : self._offset + size]
        self._offset += len(block)
        return block


def _service(session: RecordingSession, root: Path, max_bytes: int = 1024 * 1024) -> UploadService:
    return UploadService(
        session=session,  # type: ignore[arg-type]
        input_root=root,
        max_file_bytes=max_bytes,
        allowed_extensions=frozenset({".pdf", ".html", ".htm"}),
    )


def _bid(session: RecordingSession, identifier: str = "City RFP") -> Bid:
    bid = Bid(id=uuid4(), bid_identifier=identifier)
    session.bids.append(bid)
    return bid


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.PDF", "report.pdf"),
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("C:\\Users\\me\\Bid Docs\\RFP #12.pdf", "RFP_12.pdf"),
        ("...hidden.html", "hidden.html"),
        ("na\x00me.htm", "name.htm"),
    ],
)
def test_sanitize_filename_strips_paths_and_unsafe_characters(raw: str, expected: str) -> None:
    assert sanitize_filename(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "noextension", "...", "/"])
def test_sanitize_filename_rejects_unusable_names(raw: str | None) -> None:
    with pytest.raises(UploadRejected):
        sanitize_filename(raw)


def test_content_sniffing_does_not_trust_extensions() -> None:
    assert content_matches_extension(PDF, ".pdf")
    assert not content_matches_extension(HTML, ".pdf")
    assert content_matches_extension(HTML, ".html")
    assert content_matches_extension(b"\xef\xbb\xbf  <HTML><body>x</body></HTML>", ".htm")
    assert not content_matches_extension(PDF, ".html")
    assert not content_matches_extension(b"<html>\x00binary", ".html")
    assert not content_matches_extension(PDF, ".docx")


def test_roles_are_validated() -> None:
    assert normalize_role(None) is None
    assert normalize_role("auto") is None
    assert normalize_role(" Addendum ") == "addendum"
    with pytest.raises(UploadRejected):
        normalize_role("contract")


def test_valid_files_are_stored_registered_and_tagged(tmp_path: Path) -> None:
    session = RecordingSession()
    bid = _bid(session)
    service = _service(session, tmp_path)

    outcomes = asyncio.run(
        service.upload(
            bid,
            [
                UploadRequestItem(FakeUpload("Original RFP.pdf", PDF), role="original"),
                UploadRequestItem(FakeUpload("addendum-1.html", HTML), role="addendum", addendum_number=1),
            ],
        )
    )

    assert [item.outcome for item in outcomes] == [OUTCOME_REGISTERED, OUTCOME_REGISTERED]
    stored = sorted(path.name for path in (tmp_path / "City RFP").iterdir())
    assert len(stored) == 2
    assert all(not name.startswith(".") for name in stored)
    assert len(session.documents) == 2
    original, addendum = session.documents
    assert original.document_type == "pdf"
    assert original.document_role == "original"
    assert original.original_filename == "Original RFP.pdf"
    assert original.uploaded_at is not None
    assert original.storage_path.startswith("City RFP/")
    assert addendum.document_type == "html"
    assert addendum.document_role == "addendum"
    assert addendum.addendum_number == 1
    assert (tmp_path / original.storage_path).read_bytes() == PDF


def test_rejections_leave_no_files_or_rows(tmp_path: Path) -> None:
    session = RecordingSession()
    bid = _bid(session)
    service = _service(session, tmp_path, max_bytes=2048)

    outcomes = asyncio.run(
        service.upload(
            bid,
            [
                UploadRequestItem(FakeUpload("fake.pdf", HTML)),
                UploadRequestItem(FakeUpload("big.pdf", PDF + b"0" * 4096)),
                UploadRequestItem(FakeUpload("empty.pdf", b"")),
                UploadRequestItem(FakeUpload("sheet.xlsx", PDF)),
                UploadRequestItem(FakeUpload("ok.pdf", PDF), role="contract"),
                UploadRequestItem(FakeUpload("ok.pdf", PDF), addendum_number=0),
            ],
        )
    )

    assert all(item.outcome == OUTCOME_REJECTED for item in outcomes)
    reasons = [item.reason or "" for item in outcomes]
    assert "not a valid PDF" in reasons[0]
    assert "limit" in reasons[1]
    assert "empty" in reasons[2]
    assert "not supported" in reasons[3]
    assert "role" in reasons[4]
    assert "addendum number" in reasons[5]
    assert list((tmp_path / "City RFP").iterdir()) == []
    assert session.documents == []


def test_duplicate_content_in_same_bid_is_not_stored_twice(tmp_path: Path) -> None:
    session = RecordingSession()
    bid = _bid(session)
    service = _service(session, tmp_path)

    first = asyncio.run(service.upload(bid, [UploadRequestItem(FakeUpload("a.pdf", PDF))]))
    second = asyncio.run(service.upload(bid, [UploadRequestItem(FakeUpload("copy.pdf", PDF))]))

    assert first[0].outcome == OUTCOME_REGISTERED
    assert second[0].outcome == OUTCOME_DUPLICATE
    assert second[0].document_id == first[0].document_id
    assert len(list((tmp_path / "City RFP").iterdir())) == 1
    assert len(session.documents) == 1


def test_same_content_in_another_bid_is_registered_for_that_bid(tmp_path: Path) -> None:
    session = RecordingSession()
    bid_a = _bid(session, "Bid A")
    bid_b = _bid(session, "Bid B")
    service = _service(session, tmp_path)

    first = asyncio.run(service.upload(bid_a, [UploadRequestItem(FakeUpload("a.pdf", PDF))]))
    second = asyncio.run(service.upload(bid_b, [UploadRequestItem(FakeUpload("a.pdf", PDF))]))

    assert first[0].outcome == OUTCOME_REGISTERED
    assert second[0].outcome == OUTCOME_REGISTERED
    assert {doc.bid_id for doc in session.documents} == {bid_a.id, bid_b.id}


def test_discard_removes_files_when_the_transaction_is_not_committed(tmp_path: Path) -> None:
    session = RecordingSession()
    bid = _bid(session)
    service = _service(session, tmp_path)

    asyncio.run(service.upload(bid, [UploadRequestItem(FakeUpload("a.pdf", PDF))]))
    assert len(list((tmp_path / "City RFP").iterdir())) == 1
    service.discard_written_files()
    assert list((tmp_path / "City RFP").iterdir()) == []
