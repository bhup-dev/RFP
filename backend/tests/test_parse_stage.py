"""Parser selection and parse-stage behavior. PostgreSQL is not required."""

import asyncio
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.ingestion.exceptions import UnsupportedDocumentTypeError
from app.ingestion.parsers.base import RegisteredSource
from app.ingestion.parsers.factory import parser_for
from app.ingestion.parsers.html_parser import HtmlParser
from app.ingestion.parsers.pdf_parser import PdfParser
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.registry import SqlDocumentRegistry
from app.schemas.parsing import ParseStatus, ParsedDocument
from tests.ingestion_doubles import RecordingSession
from tests.pdf_samples import write_text_pdf


def _source(
    relative_path: str,
    *,
    document_type: str | None,
    file_name: str | None = None,
    file_hash: str | None = None,
    document_id: object = None,
) -> RegisteredSource:
    return RegisteredSource(
        document_id=document_id or uuid4(),
        relative_path=relative_path,
        file_name=file_name or Path(relative_path).name,
        document_type=document_type,
        file_hash=file_hash,
    )


def test_factory_uses_the_validated_type_only() -> None:
    assert isinstance(parser_for("pdf"), PdfParser)
    assert isinstance(parser_for("PDF"), PdfParser)
    assert isinstance(parser_for(".html"), HtmlParser)
    assert isinstance(parser_for(".HTM"), HtmlParser)
    with pytest.raises(UnsupportedDocumentTypeError, match="not supported"):
        parser_for("docx")
    with pytest.raises(UnsupportedDocumentTypeError, match="not supported"):
        parser_for(".txt")


def test_filename_does_not_select_a_parser(tmp_path: Path) -> None:
    root = tmp_path / "bids"
    path = root / "Addendum_1.pdf"
    path.parent.mkdir()
    write_text_pdf(path, ["Visible specification text"])
    pipeline = IngestionPipeline(RecordingSession(), root)

    parsed = pipeline.parse_registered(
        _source("Addendum_1.pdf", document_type=None, file_name="Addendum_1.pdf")
    )

    assert parsed.status == ParseStatus.FAILED
    assert parsed.errors[0].code == "unsupported"
    assert "Visible specification text" not in parsed.text


def test_one_malformed_document_does_not_stop_another(tmp_path: Path) -> None:
    root = tmp_path / "bids"
    good = root / "Alpha" / "notes.pdf"
    bad = root / "Beta" / "broken.pdf"
    good.parent.mkdir(parents=True)
    bad.parent.mkdir()
    write_text_pdf(good, ["Visible specification text"])
    bad.write_bytes(b"%PDF-1.4\nnot a pdf")
    pipeline = IngestionPipeline(RecordingSession(), root)

    results = pipeline.parse_registered_many(
        [
            _source("Beta/broken.pdf", document_type="pdf"),
            _source("Alpha/notes.pdf", document_type="pdf"),
        ]
    )

    assert results[0].status == ParseStatus.FAILED
    assert results[1].status == ParseStatus.PARSED
    assert results[1].sections[0].page_number == 1
    assert "Visible specification text" in results[1].sections[0].text
    payload = results[1].model_dump(mode="json")
    json.dumps(payload)
    restored = ParsedDocument.model_validate(payload)
    assert restored.document_id == results[1].document_id
    assert restored.sections[0].text == results[1].sections[0].text


def test_parsing_leaves_the_registered_document_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "bids"
    relative = "Alpha/notes.pdf"
    path = root / "Alpha" / "notes.pdf"
    path.parent.mkdir(parents=True)
    write_text_pdf(path, ["Visible specification text"])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    session = RecordingSession()
    registry = SqlDocumentRegistry(session)
    decision = asyncio.run(
        registry.register(
            bid_identifier="Alpha",
            relative_path=relative,
            file_name="notes.pdf",
            document_type="pdf",
            file_hash=digest,
        )
    )
    pipeline = IngestionPipeline(registry, root)

    parsed = pipeline.parse_registered(
        _source(
            relative,
            document_type="pdf",
            file_hash=digest,
            document_id=decision.document_id,
        )
    )

    assert parsed.status == ParseStatus.PARSED
    assert parsed.document_id == decision.document_id
    assert parsed.relative_path == relative
    assert session.documents[0].ingestion_status == "pending"
    assert session.documents[0].file_hash == digest
    assert session.documents[0].storage_path == relative
    assert len(session.documents) == 1
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_changed_bytes_and_paths_outside_the_root_are_reported(tmp_path: Path) -> None:
    root = tmp_path / "bids"
    path = root / "Alpha" / "notes.pdf"
    path.parent.mkdir(parents=True)
    write_text_pdf(path, ["Visible specification text"])
    pipeline = IngestionPipeline(RecordingSession(), root)
    changed = pipeline.parse_registered(
        _source("Alpha/notes.pdf", document_type="pdf", file_hash="a" * 64)
    )
    assert changed.status == ParseStatus.PARSED
    assert changed.warnings[0].code == "content_changed"

    outside = tmp_path / "outside.pdf"
    write_text_pdf(outside, ["outside text"])
    escaped = pipeline.parse_registered(_source("../outside.pdf", document_type="pdf"))
    assert escaped.status == ParseStatus.FAILED
    assert escaped.errors[0].code == "unavailable"
    assert "outside text" not in escaped.text
    assert str(tmp_path) not in escaped.errors[0].message


def test_parse_logs_identifiers_without_document_text(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    root = tmp_path / "bids"
    path = root / "Alpha" / "notes.pdf"
    path.parent.mkdir(parents=True)
    secret = "SECRET_DOCUMENT_TEXT_SHOULD_NOT_BE_LOGGED"
    write_text_pdf(path, [secret])
    pipeline = IngestionPipeline(RecordingSession(), root)
    caplog.set_level("INFO", logger="app.ingestion.parse_stage")

    pipeline.parse_registered(_source("Alpha/notes.pdf", document_type="pdf"))

    assert "Alpha/notes.pdf" in caplog.text
    assert secret not in caplog.text
    assert str(root) not in caplog.text
