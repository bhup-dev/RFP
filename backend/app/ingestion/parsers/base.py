"""Parser interface and helpers for building a normalized result."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from app.schemas.parsing import (
    ParseMessage,
    ParseMetadata,
    ParseStatus,
    ParsedDocument,
    ParsedSection,
    ParsedTable,
)


@dataclass(frozen=True)
class RegisteredSource:
    """Metadata already stored for a source file. The path is relative to the input root."""

    document_id: UUID
    relative_path: str
    file_name: str
    document_type: str | None
    file_hash: str | None = None


class DocumentParser(Protocol):
    """Extract text and tables from one local file."""

    name: str
    version: str

    def parse(self, path: Path, source: RegisteredSource) -> ParsedDocument:
        """Return a normalized result. A failure uses status ``failed`` and does not raise."""


def failed_document(
    source: RegisteredSource,
    *,
    code: str,
    message: str,
    parser_name: str = "",
    parser_version: str = "",
    document_type: str | None = None,
) -> ParsedDocument:
    """Build a failed result whose message is safe to return to a caller."""
    return ParsedDocument(
        document_id=source.document_id,
        relative_path=source.relative_path,
        file_name=source.file_name,
        document_type=document_type if document_type is not None else (source.document_type or ""),
        parser_name=parser_name,
        parser_version=parser_version,
        status=ParseStatus.FAILED,
        text="",
        sections=[],
        tables=[],
        warnings=[],
        errors=[ParseMessage(code=code, message=message)],
        metadata=ParseMetadata(),
    )


def table_text(headers: list[str | None] | None, rows: list[list[str | None]]) -> str:
    """Render a table without substituting placeholder words for missing cells."""

    def line(cells: list[str | None]) -> str:
        return " | ".join("" if cell is None else cell for cell in cells)

    lines: list[str] = []
    if headers:
        lines.append(line(headers))
    lines.extend(line(row) for row in rows)
    return "\n".join(lines)


def assemble_document(
    source: RegisteredSource,
    *,
    parser_name: str,
    parser_version: str,
    document_type: str,
    sections: list[ParsedSection],
    warnings: list[ParseMessage],
    errors: list[ParseMessage],
    text: str,
    status: ParseStatus,
    metadata: ParseMetadata,
) -> ParsedDocument:
    """Collect section tables onto the document as well."""
    tables = [table for section in sections for table in section.tables]
    metadata.section_count = len(sections)
    metadata.table_count = len(tables)
    metadata.character_count = len(text)
    return ParsedDocument(
        document_id=source.document_id,
        relative_path=source.relative_path,
        file_name=source.file_name,
        document_type=document_type,
        parser_name=parser_name,
        parser_version=parser_version,
        status=status,
        text=text,
        sections=sections,
        tables=tables,
        warnings=warnings,
        errors=errors,
        metadata=metadata,
    )
