"""Choose a parser from a validated document type."""

from __future__ import annotations

from app.ingestion.exceptions import UnsupportedDocumentTypeError
from app.ingestion.parsers.base import DocumentParser
from app.ingestion.parsers.html_parser import HtmlParser
from app.ingestion.parsers.pdf_parser import PdfParser

_PDF = {"pdf", ".pdf"}
_HTML = {"html", "htm", ".html", ".htm"}


def parser_for(document_type: str) -> DocumentParser:
    """Return the parser for a registered type. The filename is not consulted."""
    key = document_type.strip().lower()
    if key in _PDF:
        return PdfParser()
    if key in _HTML:
        return HtmlParser()
    raise UnsupportedDocumentTypeError("document type is not supported")
