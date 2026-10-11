"""Source location for a claim.

A citation records where a statement was found. It does not prove that the
statement is correct or that the citation itself can be resolved.
"""

from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import UnitScore


class Citation(BaseModel):
    """Trace a claim back to one chunk of a source document."""

    bid_id: UUID
    document_id: UUID
    chunk_id: UUID
    file_name: str = Field(min_length=1, max_length=512)
    page_number: int | None = Field(
        default=None,
        ge=1,
        description="Omitted for HTML and other sources that have no page numbers.",
    )
    section_title: str | None = Field(default=None, max_length=512)
    excerpt: str = Field(min_length=1)
    relevance_score: UnitScore | None = None
