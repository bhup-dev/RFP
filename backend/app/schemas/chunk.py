"""Parsed document chunk schemas."""

from typing import Any
from uuid import UUID

from pydantic import AliasChoices, Field

from app.schemas.common import AwareDatetime, JsonValue, OrmSchema


class ChunkBase(OrmSchema):
    """Fields shared by chunk input and output."""

    chunk_index: int = Field(ge=0)
    page_number: int | None = Field(default=None, ge=1)
    section_title: str | None = Field(default=None, max_length=512)
    chunk_type: str | None = Field(default=None, max_length=64)
    text: str = Field(min_length=1)
    content_hash: str | None = Field(default=None, max_length=64)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ChunkCreate(ChunkBase):
    """A chunk produced by a future parser. No database id yet."""

    document_id: UUID


class ChunkRead(ChunkBase):
    """A stored chunk.

    The ORM attribute is ``chunk_metadata`` because SQLAlchemy reserves
    ``metadata``. API JSON uses ``metadata``.
    """

    id: UUID
    document_id: UUID
    created_at: AwareDatetime
    metadata: dict[str, JsonValue] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("chunk_metadata", "metadata"),
    )
