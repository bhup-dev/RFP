"""Summary of a chunking run. Embeddings are not included."""

from uuid import UUID

from pydantic import BaseModel, Field


class ChunkMessage(BaseModel):
    """A chunking warning or error. The message does not include chunk text."""

    code: str
    message: str
    page_number: int | None = Field(default=None, ge=1)
    table_id: str | None = None


class ChunkingSummary(BaseModel):
    """What was stored for one document. Unchanged content reports skips instead of new rows."""

    document_id: UUID
    chunks_created: int = Field(default=0, ge=0)
    text_chunks_created: int = Field(default=0, ge=0)
    table_chunks_created: int = Field(default=0, ge=0)
    chunks_skipped: int = Field(default=0, ge=0)
    warnings: list[ChunkMessage] = Field(default_factory=list)
    errors: list[ChunkMessage] = Field(default_factory=list)
