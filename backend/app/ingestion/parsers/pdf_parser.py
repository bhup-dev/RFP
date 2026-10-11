"""PDF text and table extraction. Page numbers are one-based.

Text uses PyMuPDF's reading order. Multi-column pages can come out in an
imperfect order, and each page stays in its own section. Image-only PDFs are
reported as failures so a later OCR step can handle them.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pymupdf

from app.ingestion.parsers.base import (
    RegisteredSource,
    assemble_document,
    failed_document,
    table_text,
)
from app.schemas.parsing import (
    ParseMessage,
    ParseMetadata,
    ParseStatus,
    ParsedDocument,
    ParsedSection,
    ParsedTable,
)

logger = logging.getLogger(__name__)

PARSER_NAME = "pymupdf"
LOW_TEXT_CHARACTERS = 20
pymupdf.no_recommend_layout()


class PdfParser:
    """Extract one section per PDF page and any ruled tables on that page."""

    name = PARSER_NAME
    version = pymupdf.version[0]

    def parse(self, path: Path, source: RegisteredSource) -> ParsedDocument:
        document = None
        try:
            document = pymupdf.open(path)
            if document.needs_pass:
                return failed_document(
                    source,
                    code="encrypted",
                    message="PDF is encrypted and cannot be read",
                    parser_name=self.name,
                    parser_version=self.version,
                    document_type="pdf",
                )
            if not document.is_pdf or document.page_count == 0:
                return failed_document(
                    source,
                    code="unreadable",
                    message="PDF could not be read",
                    parser_name=self.name,
                    parser_version=self.version,
                    document_type="pdf",
                )
            return self._extract(document, source)
        except (pymupdf.FileDataError, pymupdf.EmptyFileError, OSError):
            return failed_document(
                source,
                code="unreadable",
                message="PDF could not be read",
                parser_name=self.name,
                parser_version=self.version,
                document_type="pdf",
            )
        finally:
            if document is not None:
                document.close()

    def _extract(self, document: pymupdf.Document, source: RegisteredSource) -> ParsedDocument:
        sections: list[ParsedSection] = []
        warnings: list[ParseMessage] = []
        table_count = 0
        for index in range(document.page_count):
            page = document[index]
            page_number = index + 1
            text = page.get_text("text").replace("\r\n", "\n").replace("\r", "\n").strip()
            tables, table_warnings = _page_tables(page, page_number, table_count)
            table_count += len(tables)
            page_warnings = list(table_warnings)
            page_warnings.extend(_page_text_warnings(page, page_number, text, tables))
            warnings.extend(page_warnings)
            sections.append(
                ParsedSection(
                    page_number=page_number,
                    text=text,
                    tables=tables,
                    warnings=page_warnings,
                )
            )

        joined = "\f".join(section.text for section in sections)
        has_content = any(section.text or section.tables for section in sections)
        errors: list[ParseMessage] = []
        status = ParseStatus.PARSED
        needs_ocr = False
        if not has_content:
            status = ParseStatus.FAILED
            needs_ocr = True
            errors.append(
                ParseMessage(
                    code="no_extractable_text",
                    message="PDF has no extractable text. OCR may be needed later.",
                )
            )
        return assemble_document(
            source,
            parser_name=self.name,
            parser_version=self.version,
            document_type="pdf",
            sections=sections,
            warnings=warnings,
            errors=errors,
            text=joined,
            status=status,
            metadata=ParseMetadata(
                page_count=document.page_count,
                needs_ocr=needs_ocr,
                reading_order="pymupdf",
            ),
        )


def _page_text_warnings(
    page: pymupdf.Page,
    page_number: int,
    text: str,
    tables: list[ParsedTable],
) -> list[ParseMessage]:
    if tables:
        return []
    if not text:
        return [
            ParseMessage(
                code="empty_page",
                message="page has no extractable text",
                page_number=page_number,
            )
        ]
    if page.get_images() and len(text) < LOW_TEXT_CHARACTERS:
        return [
            ParseMessage(
                code="low_text",
                message="page has very little extractable text",
                page_number=page_number,
            )
        ]
    return []


def _page_tables(
    page: pymupdf.Page,
    page_number: int,
    table_count: int,
) -> tuple[list[ParsedTable], list[ParseMessage]]:
    try:
        finder = page.find_tables()
    except Exception:
        logger.error("PDF table extraction failed on page %s (%s)", page_number, "table")
        return [], [
            ParseMessage(
                code="table_extraction_failed",
                message="tables on this page could not be extracted",
                page_number=page_number,
            )
        ]
    tables: list[ParsedTable] = []
    for offset, table in enumerate(finder.tables, start=1):
        extracted = table.extract() or []
        rows = [[_cell(value) for value in row] for row in extracted]
        headers = _headers(table)
        if headers is not None and rows and rows[0] == headers:
            rows = rows[1:]
        tables.append(
            ParsedTable(
                table_id=f"table-{table_count + offset}",
                page_number=page_number,
                headers=headers,
                rows=rows,
                text=table_text(headers, rows),
            )
        )
    return tables, []


def _headers(table: object) -> list[str | None] | None:
    header = getattr(table, "header", None)
    names = getattr(header, "names", None) if header is not None else None
    if not names:
        return None
    cleaned = [_cell(name) for name in names]
    if not any(cleaned):
        return None
    return cleaned


def _cell(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
