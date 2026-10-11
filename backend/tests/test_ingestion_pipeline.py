"""Ingestion coordinator. PostgreSQL is replaced with an in-memory session stand-in."""

import asyncio
import hashlib
import logging
from pathlib import Path

import pytest

from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.registry import SqlDocumentRegistry
from app.schemas.ingestion import IngestionOutcome, IngestionSummary
from tests.ingestion_doubles import RecordingSession


def _write(root: Path, relative: str, content: bytes) -> None:
    path = root.joinpath(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _pipeline(root: Path, session: RecordingSession | None = None) -> tuple[IngestionPipeline, RecordingSession]:
    store = session or RecordingSession()
    return IngestionPipeline(SqlDocumentRegistry(store), root), store


def _counts_match(summary: IngestionSummary) -> None:
    assert summary.files_discovered == (
        summary.files_registered
        + summary.duplicates_skipped
        + summary.changed_files
        + summary.unsupported_files
        + summary.failed_files
    )


def test_multi_folder_scan_registers_supported_files_only(tmp_path: Path) -> None:
    root = tmp_path / "bids"
    _write(root, "Alpha/notes.pdf", b"alpha-notes")
    _write(root, "Alpha/page.html", b"<html>alpha</html>")
    _write(root, "Alpha/form.HTM", b"<html>form</html>")
    _write(root, "Alpha/skip.txt", b"text")
    _write(root, "Alpha/picture.png", b"png")
    _write(root, "Beta/spec.PDF", b"beta-spec")
    _write(root, "Beta/.secret.pdf", b"hidden")
    _write(root, "Beta/node_modules/lib.pdf", b"library")
    (root / "Empty Folder").mkdir(parents=True)
    pipeline, session = _pipeline(root)

    summary = asyncio.run(pipeline.scan())

    _counts_match(summary)
    assert summary.input_root == "bids"
    assert summary.files_discovered == 6
    assert summary.files_registered == 4
    assert summary.unsupported_files == 2
    assert summary.duplicates_skipped == 0
    assert summary.changed_files == 0
    assert summary.failed_files == 0
    assert [item.relative_path for item in summary.results] == sorted(
        item.relative_path for item in summary.results
    )
    registered = {item.relative_path: item for item in summary.results if item.outcome == IngestionOutcome.REGISTERED}
    assert registered["Alpha/notes.pdf"].document_type == "pdf"
    assert registered["Alpha/form.HTM"].document_type == "html"
    assert registered["Beta/spec.PDF"].document_type == "pdf"
    assert registered["Alpha/notes.pdf"].message == "metadata registered; document is not parsed"
    assert all(item.ingestion_status == "pending" for item in session.documents)
    assert {bid.bid_identifier for bid in session.bids} == {"Alpha", "Beta"}
    assert all(bid.solicitation_number is None for bid in session.bids)
    assert all(bid.title is None and bid.issuing_organization is None for bid in session.bids)
    assert all(item.document_date is None and item.addendum_number is None for item in session.documents)
    assert all(str(root) not in item.relative_path for item in summary.results)


def test_single_folder_scan_limits_the_results(tmp_path: Path) -> None:
    root = tmp_path / "bids"
    _write(root, "Alpha/notes.pdf", b"alpha")
    _write(root, "Beta/spec.pdf", b"beta")
    pipeline, session = _pipeline(root)

    summary = asyncio.run(pipeline.scan("Alpha"))

    assert summary.files_registered == 1
    assert [item.relative_path for item in summary.results] == ["Alpha/notes.pdf"]
    assert session.bids[0].bid_identifier == "Alpha"


def test_same_content_changed_content_and_repeat_scans(tmp_path: Path) -> None:
    root = tmp_path / "bids"
    _write(root, "Alpha/one.pdf", b"same-bytes")
    _write(root, "Alpha/two.pdf", b"same-bytes")
    pipeline, session = _pipeline(root)

    first = asyncio.run(pipeline.scan())
    second = asyncio.run(pipeline.scan())
    _write(root, "Alpha/one.pdf", b"changed-bytes")
    third = asyncio.run(pipeline.scan())

    assert first.files_registered == 1
    assert first.duplicates_skipped == 1
    assert len(session.documents) == 1
    assert session.documents[0].storage_path == "Alpha/one.pdf"
    assert second.files_registered == 0
    assert second.duplicates_skipped == 2
    assert {item.document_id for item in second.results} == {session.documents[0].id}
    outcomes = {item.relative_path: item.outcome for item in third.results}
    assert outcomes["Alpha/one.pdf"] == IngestionOutcome.CHANGED
    assert outcomes["Alpha/two.pdf"] == IngestionOutcome.DUPLICATE
    assert session.documents[0].file_hash == hashlib.sha256(b"same-bytes").hexdigest()
    assert len(session.documents) == 1


def test_explicit_grouping_id_is_not_stored_as_a_solicitation_number(tmp_path: Path) -> None:
    root = tmp_path / "bids"
    _write(root, "Alpha/notes.pdf", b"alpha")
    _write(root, "Beta/spec.html", b"<html></html>")
    pipeline, session = _pipeline(root)

    summary = asyncio.run(pipeline.scan(bid_identifier="internal-group"))

    assert summary.files_registered == 2
    assert {item.bid_identifier for item in summary.results} == {"internal-group"}
    assert len(session.bids) == 1
    assert session.bids[0].solicitation_number is None


def test_one_database_failure_does_not_stop_other_files(tmp_path: Path) -> None:
    root = tmp_path / "bids"
    _write(root, "Alpha/notes.pdf", b"alpha")
    _write(root, "Beta/spec.pdf", b"beta")
    store = RecordingSession()
    pipeline = IngestionPipeline(_FailPath(SqlDocumentRegistry(store), "Alpha/notes.pdf"), root)

    summary = asyncio.run(pipeline.scan())

    _counts_match(summary)
    assert summary.failed_files == 1
    assert summary.files_registered == 1
    failed = next(item for item in summary.results if item.outcome == IngestionOutcome.FAILED)
    assert failed.relative_path == "Alpha/notes.pdf"
    assert failed.message == "database rejected the registration"
    assert store.documents[0].storage_path == "Beta/spec.pdf"
    assert store.documents[0].ingestion_status == "pending"


def test_scan_logs_counts_without_document_contents_or_absolute_paths(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    root = tmp_path / "bids"
    secret = b"SECRET_DOCUMENT_TEXT_SHOULD_NOT_BE_LOGGED"
    _write(root, "Alpha/notes.pdf", secret)
    pipeline, _session = _pipeline(root)
    caplog.set_level(logging.INFO, logger="app.ingestion.pipeline")

    asyncio.run(pipeline.scan())

    assert "Ingestion scan complete" in caplog.text
    assert "SECRET_DOCUMENT_TEXT_SHOULD_NOT_BE_LOGGED" not in caplog.text
    assert str(root) not in caplog.text


class _FailPath:
    def __init__(self, inner: SqlDocumentRegistry, relative_path: str) -> None:
        self._inner = inner
        self._relative_path = relative_path

    async def register(self, **kwargs: object) -> object:
        if kwargs["relative_path"] == self._relative_path:
            raise RuntimeError("database unavailable")
        return await self._inner.register(**kwargs)
