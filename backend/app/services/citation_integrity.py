"""Shared validation of persisted citation payloads against source-of-truth chunks."""

from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.schemas.citation import Citation


def parse_citations(rows: list[dict[str, Any]]) -> list[Citation]:
    parsed = []
    for row in rows:
        payload = dict(row)
        payload.pop("side", None)
        try:
            parsed.append(Citation.model_validate(payload))
        except ValidationError:
            continue
    return parsed


def citation_matches(
    claimed: Citation,
    authoritative: Citation | None,
    bid_id: UUID,
) -> bool:
    if authoritative is None or claimed.bid_id != bid_id:
        return False
    return (
        claimed.chunk_id == authoritative.chunk_id
        and claimed.document_id == authoritative.document_id
        and authoritative.bid_id == bid_id
        and claimed.file_name == authoritative.file_name
        and claimed.page_number == authoritative.page_number
        and claimed.section_title == authoritative.section_title
        and bool(claimed.excerpt.strip())
        and claimed.excerpt.strip() in authoritative.excerpt
    )
