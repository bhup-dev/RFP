"""Layered citation, evidence, and conflict validation for extraction history."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import bind_operation_id, reset_operation_id
from app.core.prompt_security import UNTRUSTED_DOCUMENT_POLICY
from app.db.models.addendum import AddendumAnalysisRun, AddendumChange
from app.db.models.bid import Bid
from app.db.models.chunk import DocumentChunk
from app.db.models.document import Document
from app.db.models.extraction import ExtractedField, ExtractionRun
from app.db.models.validation import FieldValidationRecord, ValidationRun
from app.schemas.citation import Citation
from app.schemas.extraction import ASSIGNMENT_FIELD_NAMES, FieldValidationStatus
from app.schemas.validation import (
    AddendumCheckStatus,
    ConfidenceCategory,
    ExtractionValidationReport,
    FieldValidationOutcome,
    LLMEvidenceVerdict,
    OverallValidationStatus,
    ValidatedField,
    ValidationRunStatus,
    ValidationSummary,
)
from app.services.citation_integrity import citation_matches, parse_citations
from app.services.field_topics import field_matches_topic

logger = logging.getLogger(__name__)

_EXACT_FIELDS = {"Bid Number", "Model_no", "Part_no"}
_SEMANTIC_FIELDS = {
    "Due Date",
    "Bid Submission Type",
    "Delivery Date",
    "Model_no",
    "Part_no",
    "Term of Bid",
    "Pre Bid Meeting",
    "Installation",
    "Bid Bond Requirement",
    "Payment Terms",
    "Additional Documentation Required",
    "MFG for Registration",
    "Contract or Cooperative to use",
    "Product",
    "contact_info",
    "company_name",
    "Bid Summary",
    "Product Specification",
}
SEMANTIC_SYSTEM_PROMPT = UNTRUSTED_DOCUMENT_POLICY + """Validate one extracted field against only the supplied persisted evidence.
Return one raw JSON object with exactly these keys and types:
{"status": "validated" | "partially_supported" | "unsupported" | "conflicting" | "not_found" | "needs_review",
 "directness": number from 0 to 1 (1 = the evidence states the value directly),
 "completeness": number from 0 to 1 (1 = the value includes everything the evidence requires),
 "supporting_chunk_ids": [chunk_id strings from the supplied evidence],
 "contradicting_chunk_ids": [chunk_id strings from the supplied evidence],
 "reasons": [one to ten short strings],
 "recommended_action": "one short sentence"}
Use only those status words; "validated" and "partially_supported" need at least one supporting chunk id.
Check that the value answers this field, not a different one: a proposal due date is not a delivery
date, a quote or solicitation reference is not a model number unless the source labels it so, and a
prime-to-subcontractor payment obligation is not the buyer's payment terms.
Do not re-extract or invent evidence. Preserve identifiers, dates, times, timezones, quantities,
units, triggering events, mandatory/optional distinctions, product associations, and explicit negatives.
Use only supplied chunk IDs. A summary is supported only to the extent that all material claims appear
in evidence. Treat unresolved applicable addendum statements as conflicts, not automatic supersession.
Return no Markdown."""


class ExtractionValidationError(RuntimeError):
    pass


class ValidationBidNotFound(ExtractionValidationError):
    pass


class ExtractionRunNotFound(ExtractionValidationError):
    pass


class ExtractionRunBidMismatch(ExtractionValidationError):
    pass


class ValidationUnavailable(ExtractionValidationError):
    pass


class TextGenerator(Protocol):
    async def generate_response(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> str: ...


@dataclass(frozen=True)
class StoredField:
    field_name: str
    value: Any
    citations: list[dict[str, Any]]
    extraction_status: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class ExtractionSnapshot:
    run_id: UUID
    bid_id: UUID
    fields: tuple[StoredField, ...]


@dataclass(frozen=True)
class StoredChange:
    topic: str
    classification: str
    original_document_id: UUID | None
    addendum_document_id: UUID | None
    original_value: Any
    amended_value: Any
    severity: str | None
    sources: list[dict[str, Any]]


@dataclass(frozen=True)
class AddendumSnapshot:
    run_id: UUID
    changes: tuple[StoredChange, ...]


@dataclass(frozen=True)
class ValidationRunHandle:
    id: UUID
    started_at: datetime


class ValidationStore(Protocol):
    async def bid_exists(self, bid_id: UUID) -> bool: ...
    async def load_extraction(self, run_id: UUID) -> ExtractionSnapshot | None: ...
    async def load_latest_addendum(self, bid_id: UUID) -> AddendumSnapshot | None: ...
    async def resolve_chunks(self, chunk_ids: set[UUID]) -> dict[UUID, Citation]: ...
    async def start_run(
        self,
        snapshot: ExtractionSnapshot,
        addendum: AddendumSnapshot | None,
        model_name: str,
        thresholds: dict[str, float],
    ) -> ValidationRunHandle: ...
    async def complete_run(
        self,
        run_id: UUID,
        fields: dict[str, ValidatedField],
        status: ValidationRunStatus,
        overall_status: OverallValidationStatus,
        completed_at: datetime,
    ) -> None: ...
    async def fail_run(self, run_id: UUID, detail: str, completed_at: datetime) -> None: ...


class SqlValidationStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bid_exists(self, bid_id: UUID) -> bool:
        return await self._session.get(Bid, bid_id) is not None

    async def load_extraction(self, run_id: UUID) -> ExtractionSnapshot | None:
        run = await self._session.get(ExtractionRun, run_id)
        if run is None:
            return None
        rows = (
            await self._session.scalars(
                select(ExtractedField)
                .where(ExtractedField.extraction_run_id == run_id)
                .order_by(ExtractedField.created_at, ExtractedField.id)
            )
        ).all()
        return ExtractionSnapshot(
            run_id=run.id,
            bid_id=run.bid_id,
            fields=tuple(
                StoredField(
                    row.field_name,
                    row.value,
                    list(row.citations or []),
                    row.validation_status,
                    row.notes,
                )
                for row in rows
            ),
        )

    async def load_latest_addendum(self, bid_id: UUID) -> AddendumSnapshot | None:
        run = await self._session.scalar(
            select(AddendumAnalysisRun)
            .where(
                AddendumAnalysisRun.bid_id == bid_id,
                AddendumAnalysisRun.status.in_(("completed", "partial")),
            )
            .order_by(AddendumAnalysisRun.created_at.desc(), AddendumAnalysisRun.id.desc())
            .limit(1)
        )
        if run is None:
            return None
        rows = (
            await self._session.scalars(
                select(AddendumChange)
                .where(AddendumChange.analysis_run_id == run.id)
                .order_by(AddendumChange.created_at, AddendumChange.id)
            )
        ).all()
        return AddendumSnapshot(
            run.id,
            tuple(
                StoredChange(
                    row.field_name,
                    row.status,
                    row.original_document_id,
                    row.addendum_document_id,
                    row.original_value,
                    row.amended_value,
                    row.severity,
                    list(row.sources or []),
                )
                for row in rows
            ),
        )

    async def resolve_chunks(self, chunk_ids: set[UUID]) -> dict[UUID, Citation]:
        if not chunk_ids:
            return {}
        rows = (
            await self._session.execute(
                select(DocumentChunk, Document)
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(DocumentChunk.id.in_(chunk_ids))
            )
        ).all()
        resolved = {}
        for chunk, document in rows:
            title = (chunk.chunk_metadata or {}).get("section_title", chunk.section_title)
            if not isinstance(title, str) or len(title) > 512:
                title = chunk.section_title
            resolved[chunk.id] = Citation(
                bid_id=document.bid_id,
                document_id=document.id,
                chunk_id=chunk.id,
                file_name=document.file_name,
                page_number=chunk.page_number,
                section_title=title,
                excerpt=chunk.text,
            )
        return resolved

    async def start_run(
        self,
        snapshot: ExtractionSnapshot,
        addendum: AddendumSnapshot | None,
        model_name: str,
        thresholds: dict[str, float],
    ) -> ValidationRunHandle:
        run = ValidationRun(
            bid_id=snapshot.bid_id,
            extraction_run_id=snapshot.run_id,
            addendum_analysis_run_id=None if addendum is None else addendum.run_id,
            status=ValidationRunStatus.PROCESSING.value,
            model_name=model_name,
            thresholds=thresholds,
            addendum_check_status=(
                AddendumCheckStatus.NOT_RUN.value
                if addendum is None
                else AddendumCheckStatus.CHECKED.value
            ),
        )
        self._session.add(run)
        await self._session.flush()
        handle = ValidationRunHandle(run.id, run.started_at)
        await self._session.commit()
        return handle

    async def complete_run(
        self,
        run_id: UUID,
        fields: dict[str, ValidatedField],
        status: ValidationRunStatus,
        overall_status: OverallValidationStatus,
        completed_at: datetime,
    ) -> None:
        run = await self._session.get(ValidationRun, run_id)
        if run is None:
            raise RuntimeError("validation run disappeared before completion")
        for name, field in fields.items():
            self._session.add(
                FieldValidationRecord(
                    validation_run_id=run_id,
                    field_name=name,
                    extracted_value=field.extracted_value,
                    status=field.validation_status.value,
                    confidence_score=(
                        None
                        if field.confidence_score is None
                        else Decimal(str(field.confidence_score))
                    ),
                    confidence_category=field.confidence_category.value,
                    supporting_citations=[
                        item.model_dump(mode="json") for item in field.supporting_citations
                    ],
                    contradicting_citations=[
                        item.model_dump(mode="json")
                        for item in field.contradicting_citations
                    ],
                    reasons=field.validation_reasons,
                    deterministic_checks=field.deterministic_checks,
                    recommended_action=field.recommended_action,
                )
            )
        run.status = status.value
        run.overall_status = overall_status.value
        run.completed_at = completed_at
        await self._session.commit()

    async def fail_run(self, run_id: UUID, detail: str, completed_at: datetime) -> None:
        if self._session.in_transaction():
            await self._session.rollback()
        run = await self._session.get(ValidationRun, run_id)
        if run is not None:
            run.status = ValidationRunStatus.FAILED.value
            run.completed_at = completed_at
            run.error_message = detail[:2000]
            await self._session.commit()


class ExtractionValidationService:
    def __init__(
        self,
        store: ValidationStore,
        llm: TextGenerator,
        *,
        model_name: str,
        semantic_enabled: bool = True,
        high_threshold: float = 0.80,
        medium_threshold: float = 0.55,
        max_output_tokens: int = 2000,
    ) -> None:
        if not 0 <= medium_threshold < high_threshold <= 1:
            raise ValueError("confidence thresholds must satisfy 0 <= medium < high <= 1")
        self._store = store
        self._llm = llm
        self._model_name = model_name
        self._semantic_enabled = semantic_enabled
        self._high = high_threshold
        self._medium = medium_threshold
        self._max_output_tokens = max_output_tokens

    async def validate(self, bid_id: UUID, extraction_run_id: UUID) -> ExtractionValidationReport:
        if not await self._store.bid_exists(bid_id):
            raise ValidationBidNotFound("bid is not registered")
        snapshot = await self._store.load_extraction(extraction_run_id)
        if snapshot is None:
            raise ExtractionRunNotFound("extraction run does not exist")
        if snapshot.bid_id != bid_id:
            raise ExtractionRunBidMismatch("extraction run does not belong to the requested bid")
        addendum = await self._store.load_latest_addendum(bid_id)
        thresholds = {"high": self._high, "medium": self._medium}
        run = await self._store.start_run(snapshot, addendum, self._model_name, thresholds)
        token = bind_operation_id(str(run.id))
        try:
            fields, warnings = await self._validate_fields(snapshot, addendum, run.id)
            summary = _summary(fields)
            overall = _overall(summary)
            status = (
                ValidationRunStatus.PARTIAL
                if any(
                    field.validation_status == FieldValidationOutcome.NEEDS_REVIEW
                    for field in fields.values()
                )
                else ValidationRunStatus.COMPLETED
            )
            completed_at = datetime.now(UTC)
            await self._store.complete_run(run.id, fields, status, overall, completed_at)
            return ExtractionValidationReport(
                validation_run_id=run.id,
                extraction_run_id=extraction_run_id,
                bid_id=bid_id,
                status=status,
                overall_status=overall,
                addendum_check_status=(
                    AddendumCheckStatus.NOT_RUN
                    if addendum is None
                    else AddendumCheckStatus.CHECKED
                ),
                addendum_analysis_run_id=None if addendum is None else addendum.run_id,
                thresholds=thresholds,
                fields=fields,
                summary=summary,
                warnings=warnings,
                started_at=run.started_at,
                completed_at=completed_at,
            )
        except Exception as exc:
            await self._store.fail_run(
                run.id, "Extraction validation failed", datetime.now(UTC)
            )
            logger.error(
                "Validation failed run_id=%s extraction_run_id=%s error_type=%s",
                run.id,
                extraction_run_id,
                type(exc).__name__,
            )
            raise ValidationUnavailable("extraction validation is unavailable") from exc
        finally:
            reset_operation_id(token)

    async def _validate_fields(
        self,
        snapshot: ExtractionSnapshot,
        addendum: AddendumSnapshot | None,
        validation_run_id: UUID,
    ) -> tuple[dict[str, ValidatedField], list[str]]:
        by_name = {field.field_name: field for field in snapshot.fields}
        warnings = []
        if addendum is None:
            warnings.append(
                "No completed addendum analysis run was available; cross-document "
                "addendum validation was not performed"
            )
        all_raw_citations = [
            item
            for field in snapshot.fields
            for item in field.citations
            if isinstance(item, dict)
        ]
        if addendum is not None:
            all_raw_citations.extend(
                source
                for change in addendum.changes
                for source in change.sources
                if isinstance(source, dict)
            )
        parsed = parse_citations(all_raw_citations)
        authoritative = await self._store.resolve_chunks(
            {item.chunk_id for item in parsed}
        )
        results: dict[str, ValidatedField] = {}
        for name in ASSIGNMENT_FIELD_NAMES:
            stored = by_name.get(name)
            if stored is None:
                results[name] = _missing_record(
                    name, snapshot.run_id, validation_run_id, "Field result was not persisted."
                )
                continue
            results[name] = await self._validate_one(
                stored,
                snapshot,
                addendum,
                authoritative,
                validation_run_id,
            )
        return results, warnings

    async def _validate_one(
        self,
        stored: StoredField,
        snapshot: ExtractionSnapshot,
        addendum: AddendumSnapshot | None,
        authoritative: dict[UUID, Citation],
        validation_run_id: UUID,
    ) -> ValidatedField:
        if stored.value is None:
            return _missing_record(
                stored.field_name,
                snapshot.run_id,
                validation_run_id,
                "Extracted value is null; no factual claim was available to validate.",
            )
        if stored.extraction_status == FieldValidationStatus.NOT_STATED.value:
            examined = [
                citation
                for citation in parse_citations(stored.citations)
                if citation_matches(citation, authoritative.get(citation.chunk_id), snapshot.bid_id)
            ]
            return _not_stated_record(stored, examined, snapshot.run_id, validation_run_id)
        claimed = parse_citations(stored.citations)
        malformed = len(stored.citations) - len(claimed)
        valid = [
            citation
            for citation in claimed
            if citation_matches(citation, authoritative.get(citation.chunk_id), snapshot.bid_id)
        ]
        checks: dict[str, Any] = {
            "claimed_citations": len(stored.citations),
            "valid_citations": len(valid),
            "malformed_citations": malformed,
            "exact_identifier_check": stored.field_name in _EXACT_FIELDS,
        }
        if not valid:
            return _field_result(
                stored,
                snapshot.run_id,
                validation_run_id,
                FieldValidationOutcome.UNSUPPORTED,
                0.0,
                ConfidenceCategory.LOW,
                [],
                [],
                ["No citation passed authoritative chunk, document, bid, excerpt, and location checks."],
                "Locate valid source evidence and re-run extraction before using this value.",
                checks,
            )

        atoms = _atomic_values(stored.value)
        evidence_text = "\n".join(item.excerpt for item in valid)
        matched = [
            atom
            for atom in atoms
            if _contains_atom(
                evidence_text, atom, exact=stored.field_name in _EXACT_FIELDS
            )
        ]
        literal_fraction = 1.0 if not atoms else len(matched) / len(atoms)
        checks["literal_values"] = atoms
        checks["literal_values_matched"] = matched
        checks["literal_support_fraction"] = literal_fraction
        integrity = len(valid) / max(1, len(stored.citations))

        relevant_changes = _relevant_changes(stored.field_name, addendum)
        conflict_sources = _validated_change_sources(
            relevant_changes, authoritative, snapshot.bid_id
        )
        hard_conflict = any(
            change.classification in {"conflict", "ambiguous"}
            or (
                change.classification in {"modified_requirement", "removed_requirement"}
                and _json_equal(stored.value, change.original_value)
                and not _json_equal(stored.value, change.amended_value)
            )
            for change in relevant_changes
        )
        if hard_conflict:
            score = min(
                0.35,
                _score(integrity, literal_fraction, literal_fraction, 0.0),
            )
            return _field_result(
                stored,
                snapshot.run_id,
                validation_run_id,
                FieldValidationOutcome.CONFLICTING,
                score,
                _category(score, self._high, self._medium),
                valid,
                conflict_sources,
                ["A relevant unresolved addendum finding conflicts with the extracted value."],
                "Resolve the cited source conflict with a human reviewer; do not automate this field.",
                checks,
            )

        needs_semantic = stored.field_name in _SEMANTIC_FIELDS or literal_fraction < 1
        if needs_semantic and self._semantic_enabled:
            try:
                verdict = await self._semantic_verdict(
                    stored, valid, conflict_sources
                )
                allowed = {item.chunk_id for item in (*valid, *conflict_sources)}
                if any(
                    value not in allowed
                    for value in (
                        *verdict.supporting_chunk_ids,
                        *verdict.contradicting_chunk_ids,
                    )
                ):
                    raise ValueError("semantic verifier cited unsupplied evidence")
                supporting = [
                    authoritative[value]
                    for value in verdict.supporting_chunk_ids
                    if value in authoritative
                ]
                contradicting = [
                    authoritative[value]
                    for value in verdict.contradicting_chunk_ids
                    if value in authoritative
                ]
                agreement = {
                    FieldValidationOutcome.VALIDATED: 1.0,
                    FieldValidationOutcome.PARTIALLY_SUPPORTED: 0.6,
                    FieldValidationOutcome.NEEDS_REVIEW: 0.4,
                    FieldValidationOutcome.CONFLICTING: 0.0,
                    FieldValidationOutcome.UNSUPPORTED: 0.0,
                    FieldValidationOutcome.NOT_FOUND: 0.0,
                    FieldValidationOutcome.NOT_STATED: 0.0,
                }[verdict.status]
                score = _score(
                    integrity, verdict.directness, verdict.completeness, agreement
                )
                if len(valid) < len(stored.citations):
                    score = min(score, 0.20)
                if verdict.status == FieldValidationOutcome.CONFLICTING:
                    score = min(score, 0.35)
                return _field_result(
                    stored,
                    snapshot.run_id,
                    validation_run_id,
                    verdict.status,
                    score,
                    _category(score, self._high, self._medium),
                    supporting or valid,
                    contradicting,
                    verdict.reasons,
                    verdict.recommended_action,
                    checks,
                )
            except Exception as exc:  # noqa: BLE001 - any verifier failure means human review
                logger.warning(
                    "Semantic validation failed field=%s error_type=%s",
                    stored.field_name,
                    type(exc).__name__,
                )
                score = min(
                    self._medium - 0.01,
                    _score(integrity, literal_fraction, literal_fraction, 0.4),
                )
                return _field_result(
                    stored,
                    snapshot.run_id,
                    validation_run_id,
                    FieldValidationOutcome.NEEDS_REVIEW,
                    max(0.0, score),
                    ConfidenceCategory.LOW,
                    valid,
                    conflict_sources,
                    ["Semantic verification failed or returned malformed evidence references."],
                    "Review this field manually using the validated citations.",
                    checks,
                )

        outcome = (
            FieldValidationOutcome.VALIDATED
            if literal_fraction == 1.0
            else FieldValidationOutcome.PARTIALLY_SUPPORTED
            if literal_fraction > 0
            else FieldValidationOutcome.UNSUPPORTED
        )
        agreement = 1.0 if outcome == FieldValidationOutcome.VALIDATED else 0.6 if literal_fraction else 0.0
        score = _score(integrity, literal_fraction, literal_fraction, agreement)
        if len(valid) < len(stored.citations):
            outcome = FieldValidationOutcome.NEEDS_REVIEW
            score = min(score, 0.20)
        return _field_result(
            stored,
            snapshot.run_id,
            validation_run_id,
            outcome,
            score,
            _category(score, self._high, self._medium),
            valid,
            [],
            ["Deterministic literal and citation checks completed."],
            _action(outcome),
            checks,
        )

    async def _semantic_verdict(
        self,
        stored: StoredField,
        supporting: list[Citation],
        conflict_sources: list[Citation],
    ) -> LLMEvidenceVerdict:
        prompt = json.dumps(
            {
                "field_name": stored.field_name,
                "extracted_value": stored.value,
                "supporting_evidence": [
                    item.model_dump(mode="json") for item in supporting
                ],
                "potentially_contradicting_evidence": [
                    item.model_dump(mode="json") for item in conflict_sources
                ],
            },
            ensure_ascii=False,
        )
        raw = await self._llm.generate_response(
            prompt,
            system=SEMANTIC_SYSTEM_PROMPT,
            temperature=0.0,
            max_output_tokens=self._max_output_tokens,
        )
        try:
            return LLMEvidenceVerdict.model_validate_json(_strip_json_fence(raw))
        except (ValidationError, ValueError) as exc:
            raise ValueError("malformed semantic validation response") from exc


def _atomic_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, bool):
        return ["true" if value else "false"]
    if isinstance(value, str | int | float):
        return [str(value)]
    if isinstance(value, list):
        return [item for value_item in value for item in _atomic_values(value_item)]
    if isinstance(value, dict):
        return [item for value_item in value.values() for item in _atomic_values(value_item)]
    return []


def _contains_atom(text: str, atom: str, *, exact: bool) -> bool:
    if atom in {"true", "false"}:
        negative = ("not required", "no requirement", "is optional", "is not")
        positive = ("required", "must", "shall")
        return any(value in text.lower() for value in (positive if atom == "true" else negative))
    return atom in text if exact else atom.casefold() in text.casefold()


def _relevant_changes(
    field_name: str, addendum: AddendumSnapshot | None
) -> list[StoredChange]:
    if addendum is None:
        return []
    return [
        change
        for change in addendum.changes
        if field_matches_topic(field_name, change.topic)
    ]


def _validated_change_sources(
    changes: list[StoredChange],
    authoritative: dict[UUID, Citation],
    bid_id: UUID,
) -> list[Citation]:
    found = {}
    for change in changes:
        for row in change.sources:
            if not isinstance(row, dict):
                continue
            side = row.get("side")
            expected_document = (
                change.original_document_id
                if side == "original"
                else change.addendum_document_id
                if side == "addendum"
                else None
            )
            parsed = parse_citations([row])
            if not parsed or expected_document is None:
                continue
            source = parsed[0]
            stored = authoritative.get(source.chunk_id)
            if (
                source.document_id == expected_document
                and citation_matches(source, stored, bid_id)
            ):
                found[source.chunk_id] = stored
    return list(found.values())


def _json_equal(first: Any, second: Any) -> bool:
    return json.dumps(first, sort_keys=True, default=str) == json.dumps(
        second, sort_keys=True, default=str
    )


def _score(integrity: float, directness: float, completeness: float, agreement: float) -> float:
    return round(
        max(
            0.0,
            min(
                1.0,
                0.30 * integrity
                + 0.30 * directness
                + 0.20 * completeness
                + 0.20 * agreement,
            ),
        ),
        4,
    )


def _category(score: float | None, high: float, medium: float) -> ConfidenceCategory:
    if score is None:
        return ConfidenceCategory.UNAVAILABLE
    if score >= high:
        return ConfidenceCategory.HIGH
    if score >= medium:
        return ConfidenceCategory.MEDIUM
    return ConfidenceCategory.LOW


def _field_result(
    stored: StoredField,
    extraction_run_id: UUID,
    validation_run_id: UUID,
    outcome: FieldValidationOutcome,
    score: float | None,
    category: ConfidenceCategory,
    supporting: list[Citation],
    contradicting: list[Citation],
    reasons: list[str],
    action: str,
    checks: dict[str, Any],
) -> ValidatedField:
    return ValidatedField(
        field_name=stored.field_name,
        extracted_value=stored.value,
        validation_status=outcome,
        confidence_score=score,
        confidence_category=category,
        supporting_citations=supporting,
        contradicting_citations=contradicting,
        validation_reasons=reasons,
        recommended_action=action,
        extraction_run_id=extraction_run_id,
        validation_run_id=validation_run_id,
        deterministic_checks=checks,
    )


def _missing_record(
    name: str, extraction_run_id: UUID, validation_run_id: UUID, reason: str
) -> ValidatedField:
    return ValidatedField(
        field_name=name,
        extracted_value=None,
        validation_status=FieldValidationOutcome.NOT_FOUND,
        confidence_score=None,
        confidence_category=ConfidenceCategory.UNAVAILABLE,
        validation_reasons=[reason],
        recommended_action="No action unless this field is required; inspect source documents manually.",
        extraction_run_id=extraction_run_id,
        validation_run_id=validation_run_id,
        deterministic_checks={"null_value": True},
    )


def _not_stated_record(
    stored: StoredField,
    examined: list[Citation],
    extraction_run_id: UUID,
    validation_run_id: UUID,
) -> ValidatedField:
    return ValidatedField(
        field_name=stored.field_name,
        extracted_value=stored.value,
        validation_status=FieldValidationOutcome.NOT_STATED,
        confidence_score=None,
        confidence_category=ConfidenceCategory.UNAVAILABLE,
        supporting_citations=examined,
        validation_reasons=[
            stored.notes
            or "A keyword search of the bid documents found no statement of this field."
        ],
        recommended_action=(
            "No action: the bid documents do not state this field. "
            "Ask the issuer if the field matters for the response."
        ),
        extraction_run_id=extraction_run_id,
        validation_run_id=validation_run_id,
        deterministic_checks={"not_stated": True, "examined_citations": len(examined)},
    )


def _action(outcome: FieldValidationOutcome) -> str:
    if outcome == FieldValidationOutcome.VALIDATED:
        return "Eligible for routine downstream use, subject to the documented heuristic limits."
    if outcome == FieldValidationOutcome.PARTIALLY_SUPPORTED:
        return "Review the unsupported portion before downstream use."
    return "Human review is required before using this value."


def _summary(fields: dict[str, ValidatedField]) -> ValidationSummary:
    status_counts = {
        value: sum(field.validation_status == value for field in fields.values())
        for value in FieldValidationOutcome
    }
    category_counts = {
        value: sum(field.confidence_category == value for field in fields.values())
        for value in ConfidenceCategory
    }
    review = [
        name
        for name, field in fields.items()
        if field.validation_status
        in {
            FieldValidationOutcome.PARTIALLY_SUPPORTED,
            FieldValidationOutcome.UNSUPPORTED,
            FieldValidationOutcome.CONFLICTING,
            FieldValidationOutcome.NEEDS_REVIEW,
            FieldValidationOutcome.NOT_FOUND,
        }
    ]
    critical = [
        name
        for name, field in fields.items()
        if field.validation_status == FieldValidationOutcome.CONFLICTING
    ]
    return ValidationSummary(
        total_fields=len(fields),
        validated=status_counts[FieldValidationOutcome.VALIDATED],
        partially_supported=status_counts[FieldValidationOutcome.PARTIALLY_SUPPORTED],
        unsupported=status_counts[FieldValidationOutcome.UNSUPPORTED],
        conflicting=status_counts[FieldValidationOutcome.CONFLICTING],
        not_found=status_counts[FieldValidationOutcome.NOT_FOUND],
        not_stated=status_counts[FieldValidationOutcome.NOT_STATED],
        needs_review=status_counts[FieldValidationOutcome.NEEDS_REVIEW],
        high_confidence=category_counts[ConfidenceCategory.HIGH],
        medium_confidence=category_counts[ConfidenceCategory.MEDIUM],
        low_confidence=category_counts[ConfidenceCategory.LOW],
        unavailable_confidence=category_counts[ConfidenceCategory.UNAVAILABLE],
        critical_unresolved_conflicts=critical,
        fields_recommended_for_review=review,
    )


def _overall(summary: ValidationSummary) -> OverallValidationStatus:
    if summary.conflicting or summary.unsupported:
        return OverallValidationStatus.BLOCKED
    if summary.partially_supported or summary.needs_review or summary.not_found:
        return OverallValidationStatus.NEEDS_REVIEW
    return OverallValidationStatus.VALIDATED


def _strip_json_fence(raw: str) -> str:
    value = raw.strip()
    if value.startswith("```") and value.endswith("```"):
        lines = value.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return value
