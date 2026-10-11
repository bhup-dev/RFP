"""Evidence-grounded structured extraction for one ingested bid."""

from __future__ import annotations

import asyncio
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
from app.db.models.bid import Bid
from app.db.models.chunk import DocumentChunk
from app.db.models.document import Document
from app.db.models.extraction import ExtractedField as ExtractedFieldRecord
from app.db.models.extraction import ExtractionRun
from app.schemas.citation import Citation
from app.schemas.extraction import (
    ASSIGNMENT_FIELD_NAMES,
    BidExtraction,
    ExtractedField,
    ExtractionResponse,
    ExtractionRunStatus,
    FieldValidationStatus,
    LLMExtractedField,
    LLMFieldStatus,
)
from app.schemas.retrieval import RetrievalRequest, RetrievedChunk

logger = logging.getLogger(__name__)

ELIGIBLE_DOCUMENT_STATUSES = ("indexed", "partial", "completed")

@dataclass(frozen=True)
class FieldGroup:
    """Fields extracted together from evidence retrieved by their own queries."""

    name: str
    fields: tuple[str, ...]
    queries: tuple[str, ...]


FIELD_GROUPS: tuple[FieldGroup, ...] = (
    FieldGroup(
        "identity",
        ("Bid Number", "Title", "company_name", "contact_info"),
        (
            "solicitation number, bid number, RFP number and title",
            "issuing organization or agency that issued the solicitation",
            "buyer or procurement officer contact name, email address and phone number",
        ),
    ),
    FieldGroup(
        "schedule",
        ("Due Date", "Bid Submission Type", "Pre Bid Meeting"),
        (
            "proposal due date, closing date and time, including addenda that change it",
            "how responses must be submitted: electronic portal, email, sealed envelope or mail",
            "pre-bid or pre-proposal conference or meeting date, time and location",
        ),
    ),
    FieldGroup(
        "terms",
        ("Term of Bid", "Contract or Cooperative to use", "Payment Terms", "Bid Bond Requirement"),
        (
            "contract term, initial period, renewal options and extensions",
            "cooperative purchasing agreement or existing master contract to be used",
            "payment terms, invoicing instructions and when invoices are paid",
            "bid bond, proposal guarantee, performance bond or insurance requirement",
        ),
    ),
    FieldGroup(
        "delivery",
        ("Delivery Date", "Installation"),
        (
            "delivery deadline or delivery timeframe after award",
            "installation, deployment, configuration, imaging or white glove services",
        ),
    ),
    FieldGroup(
        "documents",
        ("Additional Documentation Required", "MFG for Registration"),
        (
            "required forms, affidavits, certifications and documents to submit with the response",
            "manufacturer registration, authorized reseller or manufacturer certification requirement",
        ),
    ),
    FieldGroup(
        "products",
        ("Product", "Model_no", "Part_no", "Product Specification"),
        (
            "line item table: product name, description, model number and quantity of each item",
            "model number and part number of each requested item",
            "minimum technical specifications: processor, memory, storage, display, warranty",
        ),
    ),
    FieldGroup(
        "summary",
        ("Bid Summary",),
        (
            "purpose and scope of this solicitation and what is being purchased",
            "background and business need for the purchase",
        ),
    ),
)

SYSTEM_PROMPT = UNTRUSTED_DOCUMENT_POLICY + """You extract structured procurement data only from supplied evidence.
Return one JSON object with exactly the requested field names and no other keys.
Each field value must be an object with exactly: value, confidence, chunk_ids, notes, status.
status must be extracted, not_found, ambiguous, or conflicting.
When a value is a list, its items are plain strings or objects describing the item; status,
confidence, and chunk_ids belong only on the field object, never inside the items.

Rules:
- Never use general knowledge or invent a value. Use null with not_found when unsupported.
- Every extracted, ambiguous, or conflicting field must cite only supplied chunk_id values.
- An explicit negative statement is an extracted value, not missing information.
- Preserve exact identifiers, product model/part numbers, dates, times, and timezones.
- Keep solicitation, portal reference, and header numbers distinct when the source does.
- Distinguish initial contract term, renewals, and maximum duration.
- Do not infer a pre-bid meeting, bond, installation, manufacturer registration, or payment term.
- Payment Terms are when and how the buyer pays the vendor (for example net 30 or payment after
  acceptance). Invoicing instructions alone and prime-to-subcontractor payment obligations are not
  payment terms.
- Distinguish delivery, installation, deployment, and configuration.
- Delivery Date is when goods must be delivered, either a date or a period such as "within 45 days of award".
  Never reuse the proposal due date as the delivery date. When a line-item table date and a delivery
  clause both give delivery timing, report both with their citations and use status ambiguous.
- Distinguish mandatory documents from optional or informational attachments.
- Additional Documentation Required lists every form, affidavit, certificate, or document the bidder
  must submit or provide, including items due at award, each as a separate entry.
- Product lists every requested line item with its quantity when stated, not only the first one.
- Keep product identifiers and specifications associated with the correct product.
- Model_no lists the model number of every requested item as written in the source (for example a
  "Model #" column), each paired with its product. Part_no lists manufacturer part or SKU codes only.
- Product Specification lists the key required specifications for each product.
- Distinguish buyer, contact, vendor, manufacturer, and issuing organization.
- company_name is the organization that issued the solicitation (agency, district, or office),
  never the manufacturer of the requested products or a prospective vendor.
- contact_info is an object with the name, role, email and phone that the source states.
- Report conflicting source statements instead of silently selecting one.
- Bid Summary is two to four sentences on what is purchased, by whom, and the key requirements,
  citing every chunk that supports its claims.
- confidence is a number from 0 to 1 or null. notes must be brief and factual.
- not_found requires value=null and chunk_ids=[].
Return raw JSON only, without Markdown fences or commentary."""


class ExtractionError(RuntimeError):
    """Base class for public, non-sensitive extraction failures."""


class BidNotFound(ExtractionError):
    pass


class BidNotReady(ExtractionError):
    pass


class ExtractionUnavailable(ExtractionError):
    pass


class InvalidExtractionResponse(ExtractionError):
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


class EvidenceRetriever(Protocol):
    async def search(self, request: RetrievalRequest): ...


@dataclass(frozen=True)
class EligibleBid:
    id: UUID
    document_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class RunHandle:
    id: UUID
    started_at: datetime


class ExtractionStore(Protocol):
    async def load_eligible_bid(self, bid_id: UUID) -> EligibleBid | None: ...

    async def start_run(
        self,
        bid_id: UUID,
        document_ids: tuple[UUID, ...],
        model_name: str,
    ) -> RunHandle: ...

    async def resolve_citations(
        self,
        bid_id: UUID,
        document_ids: tuple[UUID, ...],
        chunk_ids: set[UUID],
    ) -> dict[UUID, Citation]: ...

    async def complete_run(
        self,
        run_id: UUID,
        fields: BidExtraction,
        status: ExtractionRunStatus,
        completed_at: datetime,
    ) -> None: ...

    async def fail_run(self, run_id: UUID, detail: str, completed_at: datetime) -> None: ...


class SqlExtractionStore:
    """PostgreSQL persistence with append-only extraction runs."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def load_eligible_bid(self, bid_id: UUID) -> EligibleBid | None:
        bid = await self._session.get(Bid, bid_id)
        if bid is None:
            return None
        documents = (
            await self._session.scalars(
                select(Document)
                .join(DocumentChunk, DocumentChunk.document_id == Document.id)
                .where(
                    Document.bid_id == bid_id,
                    Document.ingestion_status.in_(ELIGIBLE_DOCUMENT_STATUSES),
                )
                .order_by(Document.created_at, Document.id)
            )
        ).unique().all()
        return EligibleBid(
            id=bid.id,
            document_ids=tuple(document.id for document in documents),
        )

    async def start_run(
        self,
        bid_id: UUID,
        document_ids: tuple[UUID, ...],
        model_name: str,
    ) -> RunHandle:
        run = ExtractionRun(
            bid_id=bid_id,
            status=ExtractionRunStatus.PROCESSING.value,
            model_name=model_name,
            document_ids=[str(value) for value in document_ids],
        )
        self._session.add(run)
        await self._session.flush()
        handle = RunHandle(id=run.id, started_at=run.started_at)
        await self._session.commit()
        return handle

    async def resolve_citations(
        self,
        bid_id: UUID,
        document_ids: tuple[UUID, ...],
        chunk_ids: set[UUID],
    ) -> dict[UUID, Citation]:
        if not chunk_ids:
            return {}
        rows = (
            await self._session.execute(
                select(DocumentChunk, Document)
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(
                    DocumentChunk.id.in_(chunk_ids),
                    Document.bid_id == bid_id,
                    Document.id.in_(document_ids),
                )
            )
        ).all()
        resolved: dict[UUID, Citation] = {}
        for chunk, document in rows:
            section_title = (chunk.chunk_metadata or {}).get(
                "section_title", chunk.section_title
            )
            if not isinstance(section_title, str) or len(section_title) > 512:
                section_title = chunk.section_title
            resolved[chunk.id] = Citation(
                bid_id=document.bid_id,
                document_id=document.id,
                chunk_id=chunk.id,
                file_name=document.file_name,
                page_number=chunk.page_number,
                section_title=section_title,
                excerpt=chunk.text,
            )
        return resolved

    async def complete_run(
        self,
        run_id: UUID,
        fields: BidExtraction,
        status: ExtractionRunStatus,
        completed_at: datetime,
    ) -> None:
        run = await self._session.get(ExtractionRun, run_id)
        if run is None:
            raise RuntimeError("extraction run disappeared before completion")
        for field_name, field in fields.model_dump(by_alias=True, mode="json").items():
            assert isinstance(field, dict)
            self._session.add(
                ExtractedFieldRecord(
                    extraction_run_id=run_id,
                    field_name=field_name,
                    value=field["value"],
                    confidence=(
                        None
                        if field["confidence"] is None
                        else Decimal(str(field["confidence"]))
                    ),
                    notes=field["notes"],
                    validation_status=field["validation_status"],
                    citations=field["citations"],
                )
            )
        run.status = status.value
        run.completed_at = completed_at
        run.error_message = None
        await self._session.commit()

    async def fail_run(self, run_id: UUID, detail: str, completed_at: datetime) -> None:
        if self._session.in_transaction():
            await self._session.rollback()
        run = await self._session.get(ExtractionRun, run_id)
        if run is None:
            return
        run.status = ExtractionRunStatus.FAILED.value
        run.completed_at = completed_at
        run.error_message = detail[:2000]
        await self._session.commit()


class ExtractionService:
    """Retrieve field-group evidence, validate model claims, and persist a run."""

    def __init__(
        self,
        store: ExtractionStore,
        retriever: EvidenceRetriever,
        llm: TextGenerator,
        *,
        model_name: str,
        query_top_k: int = 12,
        max_evidence_chunks: int = 60,
        max_output_tokens: int = 8000,
    ) -> None:
        self._store = store
        self._retriever = retriever
        self._llm = llm
        self._model_name = model_name
        self._query_top_k = query_top_k
        self._max_evidence_chunks = max_evidence_chunks
        self._max_output_tokens = max_output_tokens

    async def extract(self, bid_id: UUID) -> ExtractionResponse:
        context = await self._store.load_eligible_bid(bid_id)
        if context is None:
            raise BidNotFound("bid is not registered")
        if not context.document_ids:
            raise BidNotReady("bid has no eligible ingested documents with chunks")

        run = await self._store.start_run(
            bid_id,
            context.document_ids,
            self._model_name,
        )
        token = bind_operation_id(str(run.id))
        try:
            evidence_by_group = {
                group.name: await self._retrieve_evidence(context, group)
                for group in FIELD_GROUPS
            }
            evidence = {
                item.chunk_id: item
                for items in evidence_by_group.values()
                for item in items
            }
            fields = (
                _missing_extraction("No relevant source chunks were retrieved.")
                if not evidence
                else await self._extract_fields(context, evidence_by_group)
            )
            status = (
                ExtractionRunStatus.PARTIAL
                if _contains_unsupported(fields)
                else ExtractionRunStatus.COMPLETED
            )
            completed_at = datetime.now(UTC)
            await self._store.complete_run(run.id, fields, status, completed_at)
            logger.info(
                "Extraction completed run_id=%s bid_id=%s status=%s documents=%s evidence_chunks=%s",
                run.id,
                bid_id,
                status.value,
                len(context.document_ids),
                len(evidence),
            )
            return ExtractionResponse(
                run_id=run.id,
                bid_id=bid_id,
                status=status,
                model_name=self._model_name,
                document_ids=list(context.document_ids),
                started_at=run.started_at,
                completed_at=completed_at,
                fields=fields,
            )
        except (BidNotFound, BidNotReady):
            raise
        except InvalidExtractionResponse as exc:
            await self._store.fail_run(run.id, str(exc), datetime.now(UTC))
            raise
        except Exception as exc:
            await self._store.fail_run(
                run.id,
                "Extraction provider or retrieval failed",
                datetime.now(UTC),
            )
            logger.error(
                "Extraction failed run_id=%s bid_id=%s error_type=%s",
                run.id,
                bid_id,
                type(exc).__name__,
            )
            raise ExtractionUnavailable("extraction service is unavailable") from exc
        finally:
            reset_operation_id(token)

    async def _retrieve_evidence(
        self, context: EligibleBid, group: FieldGroup
    ) -> list[RetrievedChunk]:
        """Search each of the group's queries and interleave results so every query is represented."""
        ranked: list[list[RetrievedChunk]] = []
        for query in group.queries:
            response = await self._retriever.search(
                RetrievalRequest(
                    query=query,
                    bid_id=context.id,
                    document_ids=list(context.document_ids),
                    top_k=self._query_top_k,
                    semantic_candidates=max(30, self._query_top_k),
                    keyword_candidates=max(30, self._query_top_k),
                )
            )
            ranked.append(
                [
                    result
                    for result in response.results
                    if result.bid_id == context.id
                    and result.document_id in context.document_ids
                ]
            )
        found: dict[UUID, RetrievedChunk] = {}
        for position in range(max((len(items) for items in ranked), default=0)):
            for items in ranked:
                if position < len(items):
                    found.setdefault(items[position].chunk_id, items[position])
                if len(found) >= self._max_evidence_chunks:
                    return list(found.values())
        return list(found.values())

    async def _extract_fields(
        self,
        context: EligibleBid,
        evidence_by_group: dict[str, list[RetrievedChunk]],
    ) -> BidExtraction:
        groups = [group for group in FIELD_GROUPS if evidence_by_group[group.name]]
        responses = await asyncio.gather(
            *[
                self._extract_group(group, evidence_by_group[group.name])
                for group in groups
            ]
        )
        claims: dict[str, dict[str, Any]] = {}
        evidence_for_field: dict[str, set[UUID]] = {}
        for group, group_claims in zip(groups, responses, strict=True):
            evidence_ids = {item.chunk_id for item in evidence_by_group[group.name]}
            for name in group.fields:
                claims[name] = group_claims[name]
                evidence_for_field[name] = evidence_ids
        for group in FIELD_GROUPS:
            if group.name not in {item.name for item in groups}:
                for name in group.fields:
                    claims[name] = _NOT_FOUND_CLAIM
                    evidence_for_field[name] = set()
        parsed: dict[str, LLMExtractedField] = {}
        rejected: dict[str, list[str]] = {}
        for name in ASSIGNMENT_FIELD_NAMES:
            try:
                parsed[name] = LLMExtractedField.model_validate(claims[name])
            except ValidationError as exc:
                rejected[name] = sorted({error["msg"] for error in exc.errors()})[:3]
        if rejected:
            logger.warning(
                "Extraction rejected invalid field claims bid_id=%s fields=%s",
                context.id,
                sorted(rejected),
            )

        requested_ids = {
            chunk_id
            for name, claim in parsed.items()
            for chunk_id in claim.chunk_ids
            if chunk_id in evidence_for_field[name]
        }
        authoritative = await self._store.resolve_citations(
            context.id,
            context.document_ids,
            requested_ids,
        )
        normalized = {
            name: _normalize_claim(
                claim,
                evidence_ids=evidence_for_field[name],
                authoritative=authoritative,
            )
            for name, claim in parsed.items()
        }
        for name, reasons in rejected.items():
            normalized[name] = ExtractedField(
                value=None,
                confidence=None,
                citations=[],
                notes="The model returned a claim that broke the extraction contract: "
                + "; ".join(reasons),
                validation_status=FieldValidationStatus.UNSUPPORTED,
            )
        return BidExtraction.model_validate(normalized)

    async def _extract_group(
        self, group: FieldGroup, evidence: list[RetrievedChunk]
    ) -> dict[str, Any]:
        """Return the raw claims for the group's fields; other keys in the response are ignored.

        A response whose claims break the field contract is retried once with the errors listed;
        claims that still break it are rejected per field by the caller.
        """
        prompt = _build_prompt(evidence, group.fields)
        claims = await self._request_group(prompt, group)
        errors = _contract_errors(claims)
        if not errors:
            return claims
        repair = (
            prompt
            + "\nYour previous answer broke the output contract for these fields: "
            + json.dumps(errors, ensure_ascii=False)
            + "\nReturn the complete JSON object again with those fields corrected."
        )
        retried = await self._request_group(repair, group)
        return retried if len(_contract_errors(retried)) <= len(errors) else claims

    async def _request_group(self, prompt: str, group: FieldGroup) -> dict[str, Any]:
        raw = await self._llm.generate_response(
            prompt,
            system=SYSTEM_PROMPT,
            temperature=0.0,
            max_output_tokens=self._max_output_tokens,
        )
        try:
            body = json.loads(_strip_json_fence(raw))
        except ValueError as exc:
            raise InvalidExtractionResponse(
                "LLM returned malformed structured extraction data"
            ) from exc
        if not isinstance(body, dict) or any(name not in body for name in group.fields):
            raise InvalidExtractionResponse(
                "LLM returned malformed structured extraction data"
            )
        return {name: body[name] for name in group.fields}


def _contract_errors(claims: dict[str, Any]) -> dict[str, list[str]]:
    errors: dict[str, list[str]] = {}
    for name, claim in claims.items():
        try:
            LLMExtractedField.model_validate(claim)
        except ValidationError as exc:
            errors[name] = sorted(
                {f"{'.'.join(map(str, error['loc'])) or 'field'}: {error['msg']}" for error in exc.errors()}
            )[:3]
    return errors


_NOT_FOUND_CLAIM: dict[str, Any] = {
    "value": None,
    "confidence": None,
    "chunk_ids": [],
    "notes": "No relevant source chunks were retrieved for this field.",
    "status": "not_found",
}


def _build_prompt(evidence: list[RetrievedChunk], field_names: tuple[str, ...]) -> str:
    chunks = [
        {
            "chunk_id": str(item.chunk_id),
            "document_id": str(item.document_id),
            "file_name": item.file_name,
            "page_number": item.page_number,
            "section_title": item.section_title,
            "chunk_type": item.chunk_type,
            "text": item.text,
        }
        for item in evidence
    ]
    return (
        UNTRUSTED_DOCUMENT_POLICY
        + "Extract the requested fields from this evidence. The exact field names are:\n"
        + json.dumps(list(field_names), ensure_ascii=False)
        + "\nEvidence chunks:\n"
        + json.dumps(chunks, ensure_ascii=False)
    )


def _strip_json_fence(raw: str) -> str:
    value = raw.strip()
    if value.startswith("```") and value.endswith("```"):
        lines = value.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return value


def _normalize_claim(
    claim: LLMExtractedField,
    *,
    evidence_ids: set[UUID],
    authoritative: dict[UUID, Citation],
) -> ExtractedField:
    if claim.status == LLMFieldStatus.NOT_FOUND:
        return ExtractedField(
            value=None,
            confidence=None,
            citations=[],
            notes=claim.notes,
            validation_status=FieldValidationStatus.MISSING,
        )

    invalid = [
        chunk_id
        for chunk_id in claim.chunk_ids
        if chunk_id not in evidence_ids or chunk_id not in authoritative
    ]
    if invalid:
        suffix = (
            f"Rejected {len(invalid)} citation(s) that were not supplied evidence "
            "for this bid and document set."
        )
        return ExtractedField(
            value=None,
            confidence=None,
            citations=[],
            notes=f"{claim.notes + ' ' if claim.notes else ''}{suffix}",
            validation_status=FieldValidationStatus.UNSUPPORTED,
        )

    status = {
        LLMFieldStatus.EXTRACTED: FieldValidationStatus.SUPPORTED,
        LLMFieldStatus.AMBIGUOUS: FieldValidationStatus.AMBIGUOUS,
        LLMFieldStatus.CONFLICTING: FieldValidationStatus.CONFLICTING,
    }[claim.status]
    return ExtractedField(
        value=claim.value,
        confidence=claim.confidence,
        citations=[authoritative[chunk_id] for chunk_id in claim.chunk_ids],
        notes=claim.notes,
        validation_status=status,
    )


def _missing_extraction(note: str) -> BidExtraction:
    field = {
        "value": None,
        "confidence": None,
        "citations": [],
        "notes": note,
        "validation_status": FieldValidationStatus.MISSING,
    }
    return BidExtraction.model_validate(
        {name: dict(field) for name in ASSIGNMENT_FIELD_NAMES}
    )


def _contains_unsupported(fields: BidExtraction) -> bool:
    return any(
        item is not None
        and item.validation_status == FieldValidationStatus.UNSUPPORTED
        for item in (
            getattr(fields, name)
            for name in type(fields).model_fields
        )
    )
