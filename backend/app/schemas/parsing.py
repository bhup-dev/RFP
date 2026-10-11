"""Normalized parser output. This result is not stored in PostgreSQL."""

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class ParseStatus(str, Enum):
    """Whether extraction produced usable source text."""

    PARSED = "parsed"
    FAILED = "failed"


class ParseMessage(BaseModel):
    """A warning or a fatal parsing error. The message does not include file contents."""

    code: str
    message: str
    page_number: int | None = Field(default=None, ge=1)
    anchor: str | None = None


class ParsedTable(BaseModel):
    """One table. Missing cells stay null, and headers stay null when they are not detectable."""

    table_id: str
    page_number: int | None = Field(default=None, ge=1)
    section_title: str | None = None
    anchor: str | None = None
    headers: list[str | None] | None = None
    rows: list[list[str | None]]
    text: str
    warnings: list[ParseMessage] = Field(default_factory=list)


class ParsedSection(BaseModel):
    """One PDF page or one HTML heading section.

    PDF page numbers are one-based. HTML leaves ``page_number`` null and uses
    ``anchor`` for an element id when the source has one.
    """

    page_number: int | None = Field(default=None, ge=1)
    section_title: str | None = None
    heading_level: int | None = Field(default=None, ge=1, le=6)
    anchor: str | None = None
    text: str
    tables: list[ParsedTable] = Field(default_factory=list)
    warnings: list[ParseMessage] = Field(default_factory=list)


class ParseMetadata(BaseModel):
    """Counts and parser notes. ``needs_ocr`` is true when a PDF has no extractable text."""

    page_count: int | None = Field(default=None, ge=0)
    section_count: int = Field(default=0, ge=0)
    table_count: int = Field(default=0, ge=0)
    character_count: int = Field(default=0, ge=0)
    needs_ocr: bool = False
    reading_order: str | None = None


class ParsedDocument(BaseModel):
    """In-memory parse result. The chunking pipeline stores chunks separately."""

    document_id: UUID
    relative_path: str
    file_name: str
    document_type: str
    parser_name: str
    parser_version: str
    status: ParseStatus
    text: str
    sections: list[ParsedSection]
    tables: list[ParsedTable]
    warnings: list[ParseMessage]
    errors: list[ParseMessage]
    metadata: ParseMetadata
