"""Document metadata schemas.

``DocumentRead`` is the public shape and omits the storage path.
``DocumentRecord`` keeps that path for internal use.
"""

from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import AwareDatetime, OrmSchema, ProcessingStatus


class DocumentCreate(BaseModel):
    """Metadata for a file that will be stored outside PostgreSQL."""

    bid_id: UUID
    file_name: str = Field(min_length=1, max_length=512)
    document_type: str | None = Field(default=None, max_length=64)
    file_hash: str | None = Field(default=None, max_length=64)
    storage_path: str = Field(min_length=1, max_length=1024)
    document_date: date | None = None
    addendum_number: int | None = Field(default=None, ge=0)
    ingestion_status: ProcessingStatus = ProcessingStatus.PENDING


class DocumentRead(OrmSchema):
    """Public document metadata. The storage path is not included."""

    id: UUID
    bid_id: UUID
    file_name: str
    document_type: str | None = None
    file_hash: str | None = None
    document_date: date | None = None
    addendum_number: int | None = Field(default=None, ge=0)
    ingestion_status: ProcessingStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime


class DocumentRecord(DocumentRead):
    """Internal document record, including where the file bytes live."""

    storage_path: str
