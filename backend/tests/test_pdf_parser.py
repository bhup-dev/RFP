"""PDF parser tests. Files are generated in temporary directories."""

import hashlib
from pathlib import Path
from uuid import uuid4

import pymupdf
import pytest

from app.ingestion.parsers.base import RegisteredSource
from app.ingestion.parsers.pdf_parser import PdfParser
from app.schemas.parsing import ParseStatus
from tests.pdf_samples import write_encrypted_pdf, write_image_pdf, write_table_pdf, write_text_pdf


def _source(path: Path, root: Path) -> RegisteredSource:
    return RegisteredSource(
        document_id=uuid4(),
        relative_path=path.relative_to(root).as_posix(),
        file_name=path.name,
        document_type="pdf",
    )


def test_multi_page_pdf_keeps_one_based_page_text(tmp_path: Path) -> None:
    path = tmp_path / "notes.pdf"
    write_text_pdf(path, ["Page one procurement text", "Second page specifications"])
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    parsed = PdfParser().parse(path, _source(path, tmp_path))

    assert parsed.status == ParseStatus.PARSED
    assert parsed.parser_name == "pymupdf"
    assert parsed.metadata.page_count == 2
    assert parsed.metadata.reading_order == "pymupdf"
    assert [section.page_number for section in parsed.sections] == [1, 2]
    assert parsed.sections[0].text == "Page one procurement text"
    assert parsed.sections[1].text == "Second page specifications"
    assert "Second page specifications" not in parsed.sections[0].text
    assert parsed.text == "Page one procurement text\fSecond page specifications"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_pdf_table_preserves_headers_and_rows(tmp_path: Path) -> None:
    path = tmp_path / "pricing.pdf"
    write_table_pdf(path)

    parsed = PdfParser().parse(path, _source(path, tmp_path))

    assert parsed.status == ParseStatus.PARSED
    assert len(parsed.tables) == 1
    table = parsed.tables[0]
    assert table.table_id == "table-1"
    assert table.page_number == 1
    assert table.headers == ["Item", "Qty"]
    assert table.rows == [["Widget", "2"]]
    assert "Widget" in table.text
    assert table is parsed.sections[0].tables[0]


def test_blank_page_warns_and_image_only_pdf_asks_for_ocr(tmp_path: Path) -> None:
    mixed = tmp_path / "mixed.pdf"
    write_text_pdf(mixed, ["Visible specification text", ""])
    parsed = PdfParser().parse(mixed, _source(mixed, tmp_path))
    assert parsed.status == ParseStatus.PARSED
    assert parsed.errors == []
    blank = next(section for section in parsed.sections if section.page_number == 2)
    assert any(item.code == "empty_page" and item.page_number == 2 for item in blank.warnings)
    assert any(item.code == "empty_page" for item in parsed.warnings)

    image = tmp_path / "scan.pdf"
    write_image_pdf(image)
    image_result = PdfParser().parse(image, _source(image, tmp_path))
    assert image_result.status == ParseStatus.FAILED
    assert image_result.metadata.needs_ocr is True
    assert image_result.errors[0].code == "no_extractable_text"
    assert "OCR" in image_result.errors[0].message
    assert image_result.text == ""

    low = tmp_path / "low.pdf"
    write_image_pdf(low, caption="Hi")
    low_result = PdfParser().parse(low, _source(low, tmp_path))
    assert low_result.status == ParseStatus.PARSED
    assert any(item.code == "low_text" for item in low_result.warnings)


def test_malformed_and_encrypted_pdfs_fail_without_leaking_paths(tmp_path: Path) -> None:
    malformed = tmp_path / "broken.pdf"
    malformed.write_bytes(b"%PDF-1.4\nthis is not a pdf")
    broken = PdfParser().parse(malformed, _source(malformed, tmp_path))
    assert broken.status == ParseStatus.FAILED
    assert broken.errors[0].code == "unreadable"
    assert broken.sections == []
    assert str(tmp_path) not in broken.errors[0].message
    assert "Traceback" not in broken.errors[0].message

    encrypted = tmp_path / "locked.pdf"
    write_encrypted_pdf(encrypted)
    locked = PdfParser().parse(encrypted, _source(encrypted, tmp_path))
    assert locked.status == ParseStatus.FAILED
    assert locked.errors[0].code == "encrypted"
    assert "Secret page" not in locked.text
    assert "secret" not in locked.errors[0].message


def test_pdf_parser_closes_the_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "notes.pdf"
    write_text_pdf(path, ["Page one procurement text"])
    closed: list[bool] = []
    real_open = pymupdf.open

    def spy(target: object, *args: object, **kwargs: object) -> pymupdf.Document:
        document = real_open(target, *args, **kwargs)
        original = document.close

        def close(*close_args: object, **close_kwargs: object) -> None:
            closed.append(True)
            original(*close_args, **close_kwargs)

        document.close = close  # type: ignore[method-assign]
        return document

    monkeypatch.setattr("app.ingestion.parsers.pdf_parser.pymupdf.open", spy)
    parsed = PdfParser().parse(path, _source(path, tmp_path))

    assert parsed.status == ParseStatus.PARSED
    assert closed == [True]
