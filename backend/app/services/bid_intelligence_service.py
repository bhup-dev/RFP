"""Read-only consolidation of persisted extraction, validation, and addendum results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.addendum import AddendumAnalysisRun, AddendumChange
from app.db.models.bid import Bid
from app.db.models.document import Document
from app.db.models.extraction import ExtractedField, ExtractionRun
from app.db.models.validation import FieldValidationRecord, ValidationRun
from app.schemas.citation import Citation
from app.schemas.extraction import ASSIGNMENT_FIELD_NAMES
from app.schemas.intelligence import (
    BidIntelligenceProfile,
    IntelligenceProfileField,
    IntelligenceProfileMetadata,
    IntelligenceProfileStatus,
    ProfileAddendumChange,
    ProfileFieldStatus,
)
from app.services.citation_integrity import citation_matches, parse_citations
from app.services.extraction_validation_service import SqlValidationStore
from app.services.field_topics import field_matches_topic

_CRITICAL_FIELDS = {
    "Due Date",
    "Term of Bid",
    "Contract or Cooperative to use",
}
_ADDENDUM_TYPES = {"addendum", "amendment", "clarification", "qa", "q_and_a"}


class BidIntelligenceError(RuntimeError):
    pass


class IntelligenceBidNotFound(BidIntelligenceError):
    pass


class IntelligenceExtractionNotFound(BidIntelligenceError):
    pass


class IntelligenceExtractionBidMismatch(BidIntelligenceError):
    pass


@dataclass(frozen=True)
class BidSource:
    bid_id: UUID
    bid_identifier: str
    solicitation_number: str | None
    document_ids: tuple[UUID, ...]
    has_explicit_addenda: bool


@dataclass(frozen=True)
class ExtractionFieldSource:
    name: str
    value: Any
    notes: str | None
    citations: list[dict[str, Any]]


@dataclass(frozen=True)
class ExtractionSource:
    run_id: UUID
    bid_id: UUID
    document_ids: tuple[UUID, ...]
    fields: tuple[ExtractionFieldSource, ...]


@dataclass(frozen=True)
class ValidationFieldSource:
    name: str
    extracted_value: Any
    status: str
    confidence_score: float | None
    supporting: list[dict[str, Any]]
    contradicting: list[dict[str, Any]]
    recommendation: str


@dataclass(frozen=True)
class ValidationSource:
    run_id: UUID
    extraction_run_id: UUID
    addendum_analysis_run_id: UUID | None
    fields: tuple[ValidationFieldSource, ...]


@dataclass(frozen=True)
class AddendumFindingSource:
    id: UUID
    run_id: UUID
    topic: str
    classification: str
    original_value: Any
    amended_value: Any
    explanation: str | None
    severity: str | None
    original_document_id: UUID | None
    addendum_document_id: UUID | None
    sources: list[dict[str, Any]]


class IntelligenceStore(Protocol):
    async def load_bid(self, bid_id: UUID) -> BidSource | None: ...
    async def load_extraction(
        self, bid_id: UUID, run_id: UUID | None
    ) -> ExtractionSource | None: ...
    async def extraction_exists(self, run_id: UUID) -> bool: ...
    async def load_validation(self, extraction_run_id: UUID) -> ValidationSource | None: ...
    async def load_addendum(
        self, bid_id: UUID, run_id: UUID | None
    ) -> tuple[UUID | None, tuple[AddendumFindingSource, ...]]: ...
    async def resolve_chunks(self, chunk_ids: set[UUID]) -> dict[UUID, Citation]: ...


class SqlIntelligenceStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def load_bid(self, bid_id: UUID) -> BidSource | None:
        bid = await self._session.get(Bid, bid_id)
        if bid is None:
            return None
        documents = (
            await self._session.scalars(
                select(Document).where(Document.bid_id == bid_id).order_by(Document.created_at)
            )
        ).all()
        return BidSource(
            bid.id,
            bid.bid_identifier,
            bid.solicitation_number,
            tuple(item.id for item in documents),
            any(
                item.addendum_number is not None
                or _normalized_type(item.document_role or item.document_type)
                in _ADDENDUM_TYPES
                for item in documents
            ),
        )

    async def extraction_exists(self, run_id: UUID) -> bool:
        return await self._session.get(ExtractionRun, run_id) is not None

    async def load_extraction(
        self, bid_id: UUID, run_id: UUID | None
    ) -> ExtractionSource | None:
        if run_id is None:
            run = await self._session.scalar(
                select(ExtractionRun)
                .where(
                    ExtractionRun.bid_id == bid_id,
                    ExtractionRun.status.in_(("completed", "partial")),
                )
                .order_by(ExtractionRun.created_at.desc(), ExtractionRun.id.desc())
                .limit(1)
            )
        else:
            run = await self._session.get(ExtractionRun, run_id)
        if (
            run is None
            or run.bid_id != bid_id
            or run.status not in {"completed", "partial"}
        ):
            return None
        rows = (
            await self._session.scalars(
                select(ExtractedField)
                .where(ExtractedField.extraction_run_id == run.id)
                .order_by(ExtractedField.created_at, ExtractedField.id)
            )
        ).all()
        return ExtractionSource(
            run.id,
            run.bid_id,
            tuple(UUID(value) for value in (run.document_ids or [])),
            tuple(
                ExtractionFieldSource(
                    row.field_name, row.value, row.notes, list(row.citations or [])
                )
                for row in rows
            ),
        )

    async def load_validation(self, extraction_run_id: UUID) -> ValidationSource | None:
        run = await self._session.scalar(
            select(ValidationRun)
            .where(
                ValidationRun.extraction_run_id == extraction_run_id,
                ValidationRun.status.in_(("completed", "partial")),
            )
            .order_by(ValidationRun.created_at.desc(), ValidationRun.id.desc())
            .limit(1)
        )
        if run is None:
            return None
        rows = (
            await self._session.scalars(
                select(FieldValidationRecord)
                .where(FieldValidationRecord.validation_run_id == run.id)
                .order_by(FieldValidationRecord.created_at, FieldValidationRecord.id)
            )
        ).all()
        return ValidationSource(
            run.id,
            run.extraction_run_id,
            run.addendum_analysis_run_id,
            tuple(
                ValidationFieldSource(
                    row.field_name,
                    row.extracted_value,
                    row.status,
                    None if row.confidence_score is None else float(row.confidence_score),
                    list(row.supporting_citations or []),
                    list(row.contradicting_citations or []),
                    row.recommended_action,
                )
                for row in rows
            ),
        )

    async def load_addendum(
        self, bid_id: UUID, run_id: UUID | None
    ) -> tuple[UUID | None, tuple[AddendumFindingSource, ...]]:
        if run_id is None:
            run = await self._session.scalar(
                select(AddendumAnalysisRun)
                .where(
                    AddendumAnalysisRun.bid_id == bid_id,
                    AddendumAnalysisRun.status.in_(("completed", "partial")),
                )
                .order_by(AddendumAnalysisRun.created_at.desc(), AddendumAnalysisRun.id.desc())
                .limit(1)
            )
        else:
            run = await self._session.get(AddendumAnalysisRun, run_id)
            if run is not None and run.bid_id != bid_id:
                run = None
        if run is None:
            return None, ()
        rows = (
            await self._session.scalars(
                select(AddendumChange)
                .where(AddendumChange.analysis_run_id == run.id)
                .order_by(AddendumChange.created_at, AddendumChange.id)
            )
        ).all()
        return run.id, tuple(
            AddendumFindingSource(
                row.id,
                run.id,
                row.field_name,
                row.status,
                row.original_value,
                row.amended_value,
                row.notes,
                row.severity,
                row.original_document_id,
                row.addendum_document_id,
                list(row.sources or []),
            )
            for row in rows
        )

    async def resolve_chunks(self, chunk_ids: set[UUID]) -> dict[UUID, Citation]:
        return await SqlValidationStore(self._session).resolve_chunks(chunk_ids)


class BidIntelligenceService:
    """Build a profile from stable persisted run references without invoking models."""

    def __init__(self, store: IntelligenceStore) -> None:
        self._store = store

    async def build(
        self, bid_id: UUID, extraction_run_id: UUID | None = None
    ) -> BidIntelligenceProfile:
        bid = await self._store.load_bid(bid_id)
        if bid is None:
            raise IntelligenceBidNotFound("bid is not registered")
        extraction = await self._store.load_extraction(bid_id, extraction_run_id)
        if extraction_run_id is not None and extraction is None:
            if await self._store.extraction_exists(extraction_run_id):
                raise IntelligenceExtractionBidMismatch(
                    "extraction run does not belong to the requested bid or is incomplete"
                )
            raise IntelligenceExtractionNotFound("extraction run does not exist")
        validation = (
            None
            if extraction is None
            else await self._store.load_validation(extraction.run_id)
        )
        if validation is not None and validation.addendum_analysis_run_id is None:
            addendum_run_id, addendum_findings = None, ()
        else:
            addendum_run_id, addendum_findings = await self._store.load_addendum(
                bid_id,
                None if validation is None else validation.addendum_analysis_run_id,
            )
        warnings: list[str] = []
        if extraction is None:
            warnings.append("No completed extraction run is available")
        if extraction is not None and validation is None:
            warnings.append("No validation run is available for the selected extraction")
        if bid.has_explicit_addenda and addendum_run_id is None:
            warnings.append("Explicit addendum documents exist but no addendum analysis is available")
        if not bid.document_ids:
            warnings.append("The bid has no registered source documents")
        if extraction is not None:
            unknown_documents = set(extraction.document_ids) - set(bid.document_ids)
            if unknown_documents:
                warnings.append(
                    f"The extraction references {len(unknown_documents)} document(s) outside this bid"
                )

        raw_citations = []
        if extraction is not None:
            raw_citations.extend(
                row for field in extraction.fields for row in field.citations
            )
        if validation is not None:
            raw_citations.extend(
                row
                for field in validation.fields
                for row in (*field.supporting, *field.contradicting)
            )
        raw_citations.extend(
            row for finding in addendum_findings for row in finding.sources
        )
        parsed = parse_citations(
            [row for row in raw_citations if isinstance(row, dict)]
        )
        authoritative = await self._store.resolve_chunks(
            {item.chunk_id for item in parsed}
        )

        extraction_by_name = (
            {} if extraction is None else {item.name: item for item in extraction.fields}
        )
        validation_by_name = (
            {} if validation is None else {item.name: item for item in validation.fields}
        )
        fields: dict[str, IntelligenceProfileField] = {}
        unresolved: list[str] = []
        for name in ASSIGNMENT_FIELD_NAMES:
            extracted = extraction_by_name.get(name)
            validated = validation_by_name.get(name)
            changes = [
                self._profile_change(item, bid_id, authoritative, warnings)
                for item in addendum_findings
                if field_matches_topic(name, item.topic)
            ]
            changes = [item for item in changes if item is not None]
            if extracted is None:
                fields[name] = IntelligenceProfileField(
                    value=None,
                    validation_status=ProfileFieldStatus.UNVALIDATED,
                    review_recommendation="Run structured extraction for this bid.",
                    addendum_changes=changes,
                )
                unresolved.append(f"{name}: extraction result unavailable")
                continue
            if validated is None:
                supporting = _valid_citations(
                    extracted.citations, authoritative, bid_id
                )
                if len(supporting) < len(extracted.citations):
                    warnings.append(f"{name}: one or more extraction citations are invalid")
                fields[name] = IntelligenceProfileField(
                    value=extracted.value,
                    validation_status=ProfileFieldStatus.UNVALIDATED,
                    supporting_citations=supporting,
                    extraction_notes=extracted.notes,
                    review_recommendation="Run extraction validation before relying on this value.",
                    addendum_changes=changes,
                )
                unresolved.append(f"{name}: not validated")
                continue
            supporting = _valid_citations(validated.supporting, authoritative, bid_id)
            contradicting = _valid_citations(
                validated.contradicting, authoritative, bid_id
            )
            if len(supporting) < len(validated.supporting) or len(contradicting) < len(
                validated.contradicting
            ):
                warnings.append(f"{name}: one or more persisted validation citations are invalid")
            status = ProfileFieldStatus(validated.status)
            if validated.extracted_value != extracted.value:
                status = ProfileFieldStatus.NEEDS_REVIEW
                warnings.append(
                    f"{name}: validation snapshot value does not match the extraction record"
                )
            fields[name] = IntelligenceProfileField(
                value=extracted.value,
                validation_status=status,
                confidence_score=validated.confidence_score,
                supporting_citations=supporting,
                contradicting_citations=contradicting,
                extraction_notes=extracted.notes,
                review_recommendation=validated.recommendation,
                addendum_changes=changes,
            )
            if status in {
                ProfileFieldStatus.PARTIALLY_SUPPORTED,
                ProfileFieldStatus.UNSUPPORTED,
                ProfileFieldStatus.CONFLICTING,
                ProfileFieldStatus.NEEDS_REVIEW,
            }:
                unresolved.append(f"{name}: {status.value}")

        status = _profile_status(
            fields,
            extraction_present=extraction is not None,
            validation_present=validation is not None,
            documents_present=bool(bid.document_ids),
            missing_required_addendum=bid.has_explicit_addenda
            and addendum_run_id is None,
            warnings=warnings,
        )
        profile_id = uuid5(
            NAMESPACE_URL,
            "|".join(
                [
                    str(bid_id),
                    str(None if extraction is None else extraction.run_id),
                    str(None if validation is None else validation.run_id),
                    str(addendum_run_id),
                    "v1",
                ]
            ),
        )
        return BidIntelligenceProfile(
            metadata=IntelligenceProfileMetadata(
                bid_id=bid_id,
                bid_identifier=bid.bid_identifier,
                solicitation_number=bid.solicitation_number,
                profile_id=profile_id,
                generated_at=datetime.now(UTC),
                extraction_run_id=None if extraction is None else extraction.run_id,
                validation_run_id=None if validation is None else validation.run_id,
                addendum_analysis_run_id=addendum_run_id,
                source_document_count=len(bid.document_ids),
                source_document_ids=list(bid.document_ids),
                status=status,
                review_required=status
                in {
                    IntelligenceProfileStatus.NEEDS_REVIEW,
                    IntelligenceProfileStatus.INCOMPLETE,
                    IntelligenceProfileStatus.READY_WITH_WARNINGS,
                },
                warnings=list(dict.fromkeys(warnings)),
                unresolved_issues=list(dict.fromkeys(unresolved)),
            ),
            fields=fields,
        )

    def _profile_change(
        self,
        finding: AddendumFindingSource,
        bid_id: UUID,
        authoritative: dict[UUID, Citation],
        warnings: list[str],
    ) -> ProfileAddendumChange | None:
        original = []
        addendum = []
        for row in finding.sources:
            if not isinstance(row, dict):
                continue
            citations = _valid_citations([row], authoritative, bid_id)
            if not citations:
                warnings.append(f"Addendum finding {finding.id} has an invalid citation")
                continue
            citation = citations[0]
            side = row.get("side")
            if side == "original" and citation.document_id == finding.original_document_id:
                original.append(citation)
            elif side == "addendum" and citation.document_id == finding.addendum_document_id:
                addendum.append(citation)
            else:
                warnings.append(f"Addendum finding {finding.id} has an interchanged source side")
        return ProfileAddendumChange(
            finding_id=finding.id,
            analysis_run_id=finding.run_id,
            classification=finding.classification,
            topic=finding.topic,
            original_value=finding.original_value,
            amended_value=finding.amended_value,
            explanation=finding.explanation,
            severity=finding.severity,
            original_sources=original,
            addendum_sources=addendum,
        )


def _valid_citations(
    rows: list[dict[str, Any]],
    authoritative: dict[UUID, Citation],
    bid_id: UUID,
) -> list[Citation]:
    found = {}
    for citation in parse_citations([row for row in rows if isinstance(row, dict)]):
        if citation_matches(citation, authoritative.get(citation.chunk_id), bid_id):
            found[citation.chunk_id] = authoritative[citation.chunk_id]
    return list(found.values())


def _profile_status(
    fields: dict[str, IntelligenceProfileField],
    *,
    extraction_present: bool,
    validation_present: bool,
    documents_present: bool,
    missing_required_addendum: bool,
    warnings: list[str],
) -> IntelligenceProfileStatus:
    if (
        not extraction_present
        or not validation_present
        or not documents_present
        or missing_required_addendum
    ):
        return IntelligenceProfileStatus.INCOMPLETE
    critical = any(
        fields[name].validation_status
        in {
            ProfileFieldStatus.PARTIALLY_SUPPORTED,
            ProfileFieldStatus.UNSUPPORTED,
            ProfileFieldStatus.CONFLICTING,
            ProfileFieldStatus.NOT_FOUND,
            ProfileFieldStatus.NOT_STATED,
            ProfileFieldStatus.NEEDS_REVIEW,
            ProfileFieldStatus.UNVALIDATED,
        }
        for name in _CRITICAL_FIELDS
    )
    any_blocking = any(
        item.validation_status
        in {
            ProfileFieldStatus.UNSUPPORTED,
            ProfileFieldStatus.CONFLICTING,
            ProfileFieldStatus.NEEDS_REVIEW,
        }
        for item in fields.values()
    )
    if critical or any_blocking:
        return IntelligenceProfileStatus.NEEDS_REVIEW
    if warnings or any(
        item.validation_status
        in {ProfileFieldStatus.PARTIALLY_SUPPORTED, ProfileFieldStatus.NOT_FOUND}
        for item in fields.values()
    ):
        return IntelligenceProfileStatus.READY_WITH_WARNINGS
    return IntelligenceProfileStatus.READY


def _normalized_type(value: str | None) -> str:
    return "" if value is None else "_".join(value.lower().replace("-", " ").split())
