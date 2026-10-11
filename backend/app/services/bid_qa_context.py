"""Read-only context for bid Q&A: stored bid records, system policy notes, and query terms.

Nothing here calls a model or writes to the database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.addendum import AddendumAnalysisRun, AddendumChange
from app.db.models.bid import Bid
from app.db.models.review import HumanReviewItem
from app.schemas.citation import Citation
from app.schemas.extraction import ASSIGNMENT_FIELD_NAMES
from app.schemas.intelligence import BidIntelligenceProfile
from app.services.bid_intelligence_service import (
    BidIntelligenceError,
    BidIntelligenceService,
    SqlIntelligenceStore,
)
from app.services.citation_integrity import parse_citations

RECORD_VALUE_CHARACTERS = 300
RECORD_EXCERPT_CHARACTERS = 200
COMPLETED_ADDENDUM_STATUSES = ("completed", "partial")


@dataclass(frozen=True)
class BidRecord:
    """One stored fact about a bid, rendered for the prompt, with the chunks behind it."""

    text: str
    citations: tuple[Citation, ...] = ()


@dataclass(frozen=True)
class BidContext:
    bid_id: UUID
    label: str
    records: tuple[BidRecord, ...] = ()
    profile: BidIntelligenceProfile | None = None


class BidRecordSource(Protocol):
    async def load(self, bid_id: UUID) -> BidContext: ...


@dataclass
class EmptyRecordSource:
    """Used when no database is available; every bid has a bare label and no records."""

    labels: dict[UUID, str] = field(default_factory=dict)

    async def load(self, bid_id: UUID) -> BidContext:
        return BidContext(bid_id=bid_id, label=self.labels.get(bid_id, str(bid_id)))


class SqlBidRecordLoader:
    def __init__(
        self,
        session: AsyncSession,
        *,
        max_addendum_records: int,
        max_review_records: int,
    ) -> None:
        self._session = session
        self._max_addendum = max_addendum_records
        self._max_reviews = max_review_records

    async def load(self, bid_id: UUID) -> BidContext:
        bid = await self._session.get(Bid, bid_id)
        identifier = bid.bid_identifier if bid is not None else str(bid_id)
        try:
            profile = await BidIntelligenceService(SqlIntelligenceStore(self._session)).build(
                bid_id
            )
        except BidIntelligenceError:
            profile = None
        if profile is not None and profile.metadata.extraction_run_id is None:
            profile = None

        records: list[BidRecord] = []
        if profile is not None:
            records.extend(profile_records(profile))
        records.extend(
            await self._addendum_records(
                bid_id, None if profile is None else profile.metadata.addendum_analysis_run_id
            )
        )
        records.extend(await self._review_records(bid_id))
        return BidContext(
            bid_id=bid_id,
            label=bid_label(identifier, bid, profile),
            records=tuple(records),
            profile=profile,
        )

    async def _addendum_records(self, bid_id: UUID, run_id: UUID | None) -> list[BidRecord]:
        if self._max_addendum == 0:
            return []
        if run_id is None:
            run_id = await self._session.scalar(
                select(AddendumAnalysisRun.id)
                .where(
                    AddendumAnalysisRun.bid_id == bid_id,
                    AddendumAnalysisRun.status.in_(COMPLETED_ADDENDUM_STATUSES),
                )
                .order_by(AddendumAnalysisRun.started_at.desc())
                .limit(1)
            )
        if run_id is None:
            return []
        rows = (
            await self._session.scalars(
                select(AddendumChange)
                .where(AddendumChange.bid_id == bid_id, AddendumChange.analysis_run_id == run_id)
                .order_by(AddendumChange.created_at)
                .limit(self._max_addendum)
            )
        ).all()
        return [addendum_record(row) for row in rows]

    async def _review_records(self, bid_id: UUID) -> list[BidRecord]:
        if self._max_reviews == 0:
            return []
        rows = (
            await self._session.scalars(
                select(HumanReviewItem)
                .where(HumanReviewItem.bid_id == bid_id, HumanReviewItem.status == "pending")
                .order_by(HumanReviewItem.created_at.desc())
                .limit(self._max_reviews)
            )
        ).all()
        return [review_record(row) for row in rows]


def bid_label(identifier: str, bid: Bid | None, profile: BidIntelligenceProfile | None) -> str:
    title = bid.title if bid is not None else None
    number = bid.solicitation_number if bid is not None else None
    if profile is not None:
        title = title or _text(profile.fields["Title"].value)
        number = number or _text(profile.fields["Bid Number"].value)
    parts = [part for part in (number, title) if part]
    return " - ".join(parts) if parts else identifier


def profile_records(profile: BidIntelligenceProfile) -> list[BidRecord]:
    records = []
    for name, item in profile.fields.items():
        parts = [
            f'Profile field "{name}": value={_short(item.value)}',
            f"status={item.validation_status.value}",
        ]
        if item.confidence_score is not None:
            parts.append(f"confidence={item.confidence_score:.2f}")
        if item.extraction_notes:
            parts.append(f"note={_clip(item.extraction_notes, RECORD_VALUE_CHARACTERS)}")
        if item.contradicting_citations:
            quotes = "; ".join(
                f"{_where(citation)}: \"{_focus(citation.excerpt, RECORD_EXCERPT_CHARACTERS)}\""
                for citation in item.contradicting_citations
            )
            parts.append(f"contradicting evidence: {quotes}")
        for change in item.addendum_changes:
            parts.append(
                f"addendum {change.classification}: {_short(change.original_value)} -> "
                f"{_short(change.amended_value)}"
            )
        records.append(
            BidRecord(
                text="; ".join(parts),
                citations=tuple(
                    _unique([*item.supporting_citations, *item.contradicting_citations])
                ),
            )
        )
    return records


def addendum_record(row: AddendumChange) -> BidRecord:
    parts = [f'Addendum finding ({row.status}) on "{row.field_name}"']
    if row.original_value is not None:
        parts.append(f"original={_short(row.original_value)}")
    if row.amended_value is not None:
        parts.append(f"amended={_short(row.amended_value)}")
    if row.severity:
        parts.append(f"severity={row.severity}")
    if row.notes:
        parts.append(f"explanation={_clip(row.notes, RECORD_VALUE_CHARACTERS)}")
    return BidRecord(
        text="; ".join(parts),
        citations=tuple(_unique(parse_citations(list(row.sources or [])))),
    )


def review_record(row: HumanReviewItem) -> BidRecord:
    parts = [
        f"Open review item ({row.category}, severity {row.severity}, status {row.status})",
    ]
    if row.field_name:
        parts.append(f'field "{row.field_name}"')
    if row.proposed_value is not None:
        parts.append(f"proposed value={_short(row.proposed_value)}")
    if row.validation_status:
        parts.append(f"validation status={row.validation_status}")
    parts.append(f"reason={_clip(row.reason, RECORD_VALUE_CHARACTERS)}")
    citations = parse_citations(
        [*(row.supporting_citations or []), *(row.contradicting_citations or [])]
    )
    return BidRecord(text="; ".join(parts), citations=tuple(_unique(citations)))


POLICY_NOTES: tuple[str, ...] = (
    (
        "This chat is read-only. It cannot create, approve, reject, re-evaluate, or resolve "
        "review items, and it cannot start ingestion, extraction, or workflow runs. Those "
        "actions happen on the Reviews and bid pages and are recorded there."
    ),
    (
        "A profile field is marked validated only when every stored citation resolves to a real "
        "source chunk of the same bid (same chunk, document, file, page, and section, with the "
        "quoted excerpt present in that chunk) and the cited text supports the value. If any "
        "citation does not resolve, the field is set to needs_review regardless of its "
        "confidence score; a high confidence score cannot make an unsupported value validated."
    ),
    (
        "Conflicting values, including unresolved addendum statements that disagree with the "
        "extracted value, are marked conflicting or needs_review. An addendum is not assumed to "
        "supersede earlier text automatically."
    ),
    (
        "Review items are created for fields that are conflicting, unsupported, partially "
        "supported, need review, have low confidence, or are critical and not found, and for "
        "agent evidence issues: invalid or cross-bid citations, missing evidence, and "
        "conflicting findings between agents (category inconsistent_agent_findings, high "
        "severity). A disagreement stays visible as a pending review item until a reviewer "
        "decides it."
    ),
    (
        "Review decisions are approve, reject, request re-evaluation, or mark unresolved. Only "
        "pending items can change. Each decision records the reviewer, the previous and new "
        "status, comments, and any corrected value in the decision history; the original "
        "proposed value is kept. An item in the invalid_citation category cannot be approved."
    ),
    (
        "Requesting re-evaluation is a request, not a completed operation. It is allowed only "
        "for validation-stage items of a bid workflow and a limited number of times. The system "
        "then re-runs validation and returns the item to pending; the decision response reports "
        "workflow_resumed=true only if the workflow actually resumed."
    ),
    (
        "Approving a review item applies to that single item. The workflow resumes only when no "
        "review items are pending or awaiting re-evaluation. Approving a field never approves "
        "the bid or makes a procurement or award decision."
    ),
    (
        "The specialized agents (requirements, product, commercial, compliance, evidence "
        "review) run inside the bid processing workflow only when enabled, and their findings "
        "are stored with that workflow run. The chat cannot run them."
    ),
    (
        "Repeating a review decision with the same idempotency key returns the original "
        "decision and creates no new record."
    ),
)

_POLICY_TRIGGER = re.compile(
    r"\b(agents?|review(er|s|ed)?|approv\w*|reject\w*|validat\w*|workflow|re-?evaluat\w*|"
    r"resum\w*|escalat\w*|citation id|confidence score|re-?run|ingest\w*|idempoten\w*|"
    r"human|audit|decision history)\b",
    re.IGNORECASE,
)


def needs_policy_notes(question: str) -> bool:
    return bool(_POLICY_TRIGGER.search(question))


_EXPANSIONS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (
        re.compile(r"\b(deadline|due|closing|submission date|submit by|cutoff)\b", re.IGNORECASE),
        ("solicitation due", "closing date", "due date"),
    ),
    (re.compile(r"\boriginal(ly)?\b", re.IGNORECASE), ("solicitation due", "issue date")),
    (re.compile(r"\bdeliver\w*", re.IGNORECASE), ("delivery within", "days of award", "delivery schedule")),
    (re.compile(r"\b(payment|pay|invoice\w*|net)\b", re.IGNORECASE), ("payment terms", "invoice")),
    (
        re.compile(r"\b(phone|contact|buyer|email|point of contact)\b", re.IGNORECASE),
        ("telephone", "email", "buyer", "contact"),
    ),
    (re.compile(r"\bbond\w*", re.IGNORECASE), ("bid bond", "surety")),
    (re.compile(r"\bwarrant\w*", re.IGNORECASE), ("warranty", "year warranty")),
    (re.compile(r"\b(term|duration|renew\w*)\b", re.IGNORECASE), ("initial term", "renewal")),
)


def expand_query(query: str, *, limit: int = 2000) -> str:
    """Append procurement wording that documents use for the concepts in the query."""
    lowered = query.lower()
    extra: list[str] = []
    for pattern, terms in _EXPANSIONS:
        if pattern.search(query):
            extra.extend(term for term in terms if term not in lowered and term not in extra)
    if not extra:
        return query
    return f"{query}\n{' '.join(extra)}"[:limit]


_FIELD_PATTERNS = tuple(
    re.compile(re.escape(name).replace("_", "[ _]").replace(r"\ ", r"[\s_]+"), re.IGNORECASE)
    for name in ASSIGNMENT_FIELD_NAMES
)
_ALL_FIELDS = re.compile(r"\b(all\s+)?(20|twenty)\s+(required\s+)?fields\b", re.IGNORECASE)
PROFILE_REQUEST_MIN_FIELDS = 5


def is_profile_request(question: str) -> bool:
    if _ALL_FIELDS.search(question):
        return True
    named = sum(1 for pattern in _FIELD_PATTERNS if pattern.search(question))
    return named >= PROFILE_REQUEST_MIN_FIELDS


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return text or None


def _short(value: Any) -> str:
    if value is None:
        return "null"
    return _clip(str(value), RECORD_VALUE_CHARACTERS)


def _clip(text: str, limit: int) -> str:
    value = " ".join(text.split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


_DATE = (
    r"(\d{1,2}[-/ ][A-Za-z]{3,9}[-/ ,]+\d{4}|\d{1,2}/\d{1,2}/\d{2,4}|"
    r"[A-Z][a-z]+ \d{1,2}, \d{4}|\d{1,2}:\d{2}(:\d{2})?\s?(AM|PM)?)"
)
_DATE_OR_TIME = re.compile(rf"\b{_DATE}")
_DUE_DATE = re.compile(rf"\b(due|deadline|closing)\b[^.]{{0,40}}?{_DATE}", re.IGNORECASE)


def _focus(text: str, limit: int) -> str:
    """Clip around a due date, or the first date or time, so a quote shows the differing value."""
    value = " ".join(text.split())
    match = _DUE_DATE.search(value) or _DATE_OR_TIME.search(value)
    if len(value) <= limit or match is None or match.start() < limit // 2:
        return _clip(value, limit)
    start = max(0, match.start() - limit // 3)
    return "…" + _clip(value[start:], limit - 1)


def _where(citation: Citation) -> str:
    page = f" page {citation.page_number}" if citation.page_number else ""
    return f"{citation.file_name}{page}"


def _unique(citations: list[Citation]) -> list[Citation]:
    seen: set[UUID] = set()
    unique = []
    for citation in citations:
        if citation.chunk_id in seen:
            continue
        seen.add(citation.chunk_id)
        unique.append(citation)
    return unique
