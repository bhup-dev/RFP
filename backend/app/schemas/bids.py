"""Contracts for bid creation, uploads, browsing, and background processing jobs."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.()\-]{0,127}$")

JobType = Literal["ingest_and_analyze", "analyze_only"]


class BidCreate(BaseModel):
    bid_identifier: str = Field(min_length=1, max_length=128)
    title: str | None = Field(default=None, max_length=2000)
    solicitation_number: str | None = Field(default=None, max_length=255)
    issuing_organization: str | None = Field(default=None, max_length=512)

    @field_validator("bid_identifier")
    @classmethod
    def identifier_is_folder_safe(cls, value: str) -> str:
        """The identifier becomes a storage folder name, so keep it portable."""
        identifier = value.strip()
        if not _IDENTIFIER_PATTERN.fullmatch(identifier) or ".." in identifier:
            raise ValueError(
                "bid identifier must start with a letter or digit and use only letters, "
                "digits, spaces, and . _ - ( )"
            )
        return identifier

    @field_validator("title", "solicitation_number", "issuing_organization")
    @classmethod
    def blank_is_unset(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    bid_id: UUID
    job_type: str
    status: str
    stage: str
    attempts: int
    max_attempts: int
    error_code: str | None = None
    error_detail: str | None = None
    result_summary: dict[str, Any] = Field(default_factory=dict)
    workflow_id: UUID | None = None
    available_at: datetime | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime | None = None


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    bid_id: UUID
    file_name: str
    original_filename: str | None = None
    document_type: str | None = None
    document_role: str | None = None
    addendum_number: int | None = None
    file_hash: str | None = None
    ingestion_status: str
    uploaded_at: datetime | None = None
    created_at: datetime


class BidSummary(BaseModel):
    id: UUID
    bid_identifier: str
    title: str | None = None
    solicitation_number: str | None = None
    issuing_organization: str | None = None
    created_at: datetime
    document_count: int = 0
    indexed_document_count: int = 0
    latest_job: JobRead | None = None
    latest_workflow_status: str | None = None


class BidListResponse(BaseModel):
    items: list[BidSummary]
    total: int
    limit: int
    offset: int


class BidDetail(BidSummary):
    documents: list[DocumentRead] = Field(default_factory=list)
    jobs: list[JobRead] = Field(default_factory=list)


class UploadResultItem(BaseModel):
    original_filename: str
    outcome: Literal["registered", "duplicate", "rejected"]
    document_id: UUID | None = None
    stored_name: str | None = None
    size_bytes: int | None = None
    role: str | None = None
    reason: str | None = None


class UploadResponse(BaseModel):
    bid_id: UUID
    registered: int
    duplicates: int
    rejected: int
    results: list[UploadResultItem]


class ProcessRequest(BaseModel):
    job_type: JobType = "ingest_and_analyze"


class UploadLimits(BaseModel):
    max_file_bytes: int
    max_files_per_request: int
    allowed_extensions: list[str]
    roles: list[str]
