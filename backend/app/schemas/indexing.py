"""Summary of an indexing run. Vectors are not included."""

from uuid import UUID

from pydantic import BaseModel, Field


class IndexMessage(BaseModel):
    """A warning or error for one chunk or one document. The message has no chunk text."""

    code: str
    message: str
    chunk_id: UUID | None = None
    document_id: UUID | None = None


class IndexingSummary(BaseModel):
    """What was embedded and upserted. A failed chunk is not counted as an upsert."""

    bid_id: UUID | None = None
    document_id: UUID | None = None
    chunks_requested: int = Field(default=0, ge=0)
    chunks_embedded: int = Field(default=0, ge=0)
    points_upserted: int = Field(default=0, ge=0)
    chunks_skipped: int = Field(default=0, ge=0)
    chunks_failed: int = Field(default=0, ge=0)
    warnings: list[IndexMessage] = Field(default_factory=list)
    errors: list[IndexMessage] = Field(default_factory=list)
    ingestion_status: str | None = None
