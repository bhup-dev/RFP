"""Bid API schemas.

``bid_identifier`` is the internal id, such as ``Bid1``.
``solicitation_number`` is the official solicitation number.
The buyer-facing bid number extracted from documents is a separate field on
``BidExtraction`` and is not stored in these models.
"""

from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import AwareDatetime, OrmSchema


class BidCreate(BaseModel):
    """Fields required to register a bid."""

    bid_identifier: str = Field(min_length=1, max_length=128)
    solicitation_number: str | None = Field(default=None, max_length=255)
    title: str | None = None
    issuing_organization: str | None = Field(default=None, max_length=512)


class BidUpdate(BaseModel):
    """Partial bid update. Omitted fields are left unchanged by the caller."""

    bid_identifier: str | None = Field(default=None, min_length=1, max_length=128)
    solicitation_number: str | None = Field(default=None, max_length=255)
    title: str | None = None
    issuing_organization: str | None = Field(default=None, max_length=512)


class BidRead(OrmSchema):
    """Public bid record."""

    id: UUID
    bid_identifier: str
    solicitation_number: str | None = None
    title: str | None = None
    issuing_organization: str | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class BidSummary(OrmSchema):
    """Short bid record for lists."""

    id: UUID
    bid_identifier: str
    solicitation_number: str | None = None
    title: str | None = None
