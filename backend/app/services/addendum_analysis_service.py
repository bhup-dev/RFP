"""Requirement-level addendum analysis with authoritative citation validation."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import bind_operation_id, reset_operation_id
from app.core.prompt_security import UNTRUSTED_DOCUMENT_POLICY
from app.db.models.addendum import AddendumAnalysisRun, AddendumChange
from app.db.models.bid import Bid
from app.db.models.chunk import DocumentChunk
from app.db.models.document import Document
from app.schemas.addendum import (
    AddendumAnalysisResponse,
    AddendumAnalysisStatus,
    AddendumDocumentInfo,
    AddendumDocumentRole,
    AddendumFinding,
    LLMAddendumFinding,
    LLMAddendumTopics,
)
from app.schemas.citation import Citation
from app.schemas.retrieval import RetrievalRequest, RetrievedChunk
from app.services.extraction_service import ELIGIBLE_DOCUMENT_STATUSES

logger = logging.getLogger(__name__)

MAX_FINDINGS_PER_ADDENDUM = 50

_ORIGINAL_TYPES = {
    "rfp",
    "solicitation",
    "original",
    "original_rfp",
    "request_for_proposal",
    "invitation_for_bid",
}
_ADDENDUM_TYPES = {"addendum", "amendment", "amended_solicitation"}
_CLARIFICATION_TYPES = {
    "clarification",
    "questions_and_answers",
    "question_and_answer",
    "q_and_a",
    "qa",
}
_SUPPORTING_TYPES = {
    "attachment",
    "supporting",
    "specification",
    "form",
    "pricing_sheet",
}
_ADDENDUM_CONTENT = re.compile(
    r"(?i)\b(?:this\s+)?(?:addendum|amendment)"
    r"(?:\s+(?:no\.?|number|#)\s*\d+|\s+to\s+(?:the\s+)?(?:rfp|solicitation))\b"
)
_CLARIFICATION_CONTENT = re.compile(
    r"(?i)\b(?:questions?\s+(?:and|&)\s+answers?|official\s+responses?\s+to\s+questions?|"
    r"clarification\s+(?:notice|document))\b"
)
_ORIGINAL_CONTENT = re.compile(
    r"(?i)\b(?:request\s+for\s+proposals?|invitation\s+for\s+bids?|"
    r"solicitation\s+(?:no\.?|number|#))\b"
)

DISCOVERY_SYSTEM_PROMPT = UNTRUSTED_DOCUMENT_POLICY + """Identify only explicit requirement-level statements in one addendum.
Return raw JSON: {"topics":[{"topic":"...", "retrieval_query":"...", "addendum_chunk_ids":["..."]}]}.
Use only supplied addendum chunks and only their chunk IDs.
Create concise retrieval queries that can find the corresponding earlier requirement, preserving exact
dates, quantities, model numbers, part numbers, contract clauses, forms, and units.
Include explicit additions, revisions, deletions, clarifications, conflicts, and statements of no change.
Absence from the addendum is not a deletion. Do not infer changes from filenames or general knowledge.
Return at most 20 topics and no Markdown."""

COMPARISON_SYSTEM_PROMPT = UNTRUSTED_DOCUMENT_POLICY + """Compare addendum statements with supplied earlier source evidence at requirement level.
Return raw JSON: {"findings":[{...}]} where every finding contains:
topic, classification, original_value, amended_value, explanation, impact_note, severity,
confidence, original_chunk_ids, addendum_chunk_ids.
classification must be one of new_requirement, modified_requirement, removed_requirement,
clarification, conflict, ambiguous, no_change.

Rules:
- Use only supplied chunk IDs and source text. Never invent a missing side.
- Every finding needs addendum evidence. Modified, removed, clarification, conflict, and no_change
  also need earlier evidence. A new requirement must have no original source.
- If no supplied earlier chunk addresses the topic, original_chunk_ids must be [] and classification
  must be new_requirement or ambiguous, never clarification or modified_requirement.
- Absence from an addendum never removes an original requirement.
- Use removed_requirement only for explicit withdrawal, deletion, or replacement.
- Added detail is a clarification unless it actually changes the obligation.
- Do not assume a later-looking document supersedes an earlier statement without explicit language.
- Preserve exact dates, triggering events, quantities, units, identifiers, specifications, and terms.
- Use conflict only when applicable statements cannot reasonably be satisfied together.
- Use ambiguous for uncertain document relationships, ordering, missing context, or unreconciled wording.
- Produce no speculative no_change findings.
- severity is low, medium, high, critical, review, or null; confidence is 0..1 or null.
Return no Markdown or commentary."""


class AddendumAnalysisError(RuntimeError):
    pass


class AddendumBidNotFound(AddendumAnalysisError):
    pass


class AddendumBidNotReady(AddendumAnalysisError):
    pass


class NoAddendaAvailable(AddendumAnalysisError):
    pass


class InvalidAddendumResponse(AddendumAnalysisError):
    pass


class AddendumAnalysisUnavailable(AddendumAnalysisError):
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
class AddendumContext:
    bid_id: UUID
    original_documents: tuple[AddendumDocumentInfo, ...]
    addendum_documents: tuple[AddendumDocumentInfo, ...]
    relationship_uncertain: bool
    ordering_uncertain: bool


@dataclass(frozen=True)
class AnalysisRunHandle:
    id: UUID
    started_at: datetime


class AddendumStore(Protocol):
    async def load_context(self, bid_id: UUID) -> AddendumContext | None: ...

    async def load_document_chunks(
        self, bid_id: UUID, document_id: UUID, limit: int
    ) -> list[RetrievedChunk]: ...

    async def start_run(
        self, context: AddendumContext, model_name: str
    ) -> AnalysisRunHandle: ...

    async def resolve_citations(
        self,
        bid_id: UUID,
        document_ids: set[UUID],
        chunk_ids: set[UUID],
    ) -> dict[UUID, Citation]: ...

    async def complete_run(
        self,
        run_id: UUID,
        findings: list[AddendumFinding],
        status: AddendumAnalysisStatus,
        completed_at: datetime,
    ) -> None: ...

    async def fail_run(self, run_id: UUID, detail: str, completed_at: datetime) -> None: ...


class SqlAddendumStore:
    """Load authoritative documents/chunks and persist append-only analyses."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def load_context(self, bid_id: UUID) -> AddendumContext | None:
        if await self._session.get(Bid, bid_id) is None:
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
        infos: list[AddendumDocumentInfo] = []
        for document in documents:
            sample = ""
            if _declared_type(document) not in (
                _ORIGINAL_TYPES
                | _ADDENDUM_TYPES
                | _CLARIFICATION_TYPES
                | _SUPPORTING_TYPES
            ) and document.addendum_number is None:
                sample_rows = (
                    await self._session.scalars(
                        select(DocumentChunk.text)
                        .where(DocumentChunk.document_id == document.id)
                        .order_by(DocumentChunk.chunk_index)
                        .limit(3)
                    )
                ).all()
                sample = "\n".join(sample_rows)
            infos.append(_document_info(document, sample))
        info_tuple = tuple(infos)
        addenda = tuple(
            item
            for item in info_tuple
            if item.role
            in {AddendumDocumentRole.ADDENDUM, AddendumDocumentRole.CLARIFICATION}
        )
        originals = tuple(item for item in info_tuple if item not in addenda)
        explicitly_original = any(
            item.role == AddendumDocumentRole.ORIGINAL for item in originals
        )
        ordered, ordering_uncertain = _order_addenda(addenda)
        return AddendumContext(
            bid_id=bid_id,
            original_documents=originals,
            addendum_documents=ordered,
            relationship_uncertain=bool(addenda) and not explicitly_original,
            ordering_uncertain=ordering_uncertain,
        )

    async def load_document_chunks(
        self, bid_id: UUID, document_id: UUID, limit: int
    ) -> list[RetrievedChunk]:
        rows = (
            await self._session.execute(
                select(DocumentChunk, Document)
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(Document.bid_id == bid_id, Document.id == document_id)
                .order_by(DocumentChunk.chunk_index, DocumentChunk.id)
                .limit(limit)
            )
        ).all()
        return [_retrieved_chunk(chunk, document) for chunk, document in rows]

    async def start_run(
        self, context: AddendumContext, model_name: str
    ) -> AnalysisRunHandle:
        run = AddendumAnalysisRun(
            bid_id=context.bid_id,
            status=AddendumAnalysisStatus.PROCESSING.value,
            model_name=model_name,
            original_document_ids=[
                str(item.document_id) for item in context.original_documents
            ],
            addendum_document_ids=[
                str(item.document_id) for item in context.addendum_documents
            ],
            relationship_uncertain=context.relationship_uncertain,
            ordering_uncertain=context.ordering_uncertain,
        )
        self._session.add(run)
        await self._session.flush()
        handle = AnalysisRunHandle(run.id, run.started_at)
        await self._session.commit()
        return handle

    async def resolve_citations(
        self,
        bid_id: UUID,
        document_ids: set[UUID],
        chunk_ids: set[UUID],
    ) -> dict[UUID, Citation]:
        if not chunk_ids or not document_ids:
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
        return {
            chunk.id: _citation(chunk, document)
            for chunk, document in rows
        }

    async def complete_run(
        self,
        run_id: UUID,
        findings: list[AddendumFinding],
        status: AddendumAnalysisStatus,
        completed_at: datetime,
    ) -> None:
        run = await self._session.get(AddendumAnalysisRun, run_id)
        if run is None:
            raise RuntimeError("addendum analysis run disappeared before completion")
        for finding in findings:
            source_rows = [
                {"side": "original", **item.model_dump(mode="json")}
                for item in finding.original_sources
            ]
            source_rows.extend(
                {"side": "addendum", **item.model_dump(mode="json")}
                for item in finding.addendum_sources
            )
            self._session.add(
                AddendumChange(
                    id=finding.id,
                    bid_id=finding.bid_id,
                    analysis_run_id=run_id,
                    original_document_id=finding.original_document_id,
                    addendum_document_id=finding.addendum_document_id,
                    field_name=finding.topic,
                    original_value=finding.original_value,
                    amended_value=finding.amended_value,
                    status=finding.classification.value,
                    notes=finding.explanation,
                    impact_note=finding.impact_note,
                    severity=(
                        None if finding.severity is None else finding.severity.value
                    ),
                    confidence=(
                        None
                        if finding.confidence is None
                        else Decimal(str(finding.confidence))
                    ),
                    sources=source_rows,
                    created_at=finding.created_at,
                )
            )
        run.status = status.value
        run.completed_at = completed_at
        run.error_message = None
        await self._session.commit()

    async def fail_run(self, run_id: UUID, detail: str, completed_at: datetime) -> None:
        if self._session.in_transaction():
            await self._session.rollback()
        run = await self._session.get(AddendumAnalysisRun, run_id)
        if run is None:
            return
        run.status = AddendumAnalysisStatus.FAILED.value
        run.completed_at = completed_at
        run.error_message = detail[:2000]
        await self._session.commit()


class AddendumAnalysisService:
    """Discover and compare requirement-level topics one addendum at a time."""

    def __init__(
        self,
        store: AddendumStore,
        retriever: EvidenceRetriever,
        llm: TextGenerator,
        *,
        model_name: str,
        max_chunks_per_document: int = 30,
        related_top_k: int = 8,
        max_evidence_chunks: int = 60,
        max_output_tokens: int = 8000,
    ) -> None:
        self._store = store
        self._retriever = retriever
        self._llm = llm
        self._model_name = model_name
        self._max_chunks_per_document = max_chunks_per_document
        self._related_top_k = related_top_k
        self._max_evidence_chunks = max_evidence_chunks
        self._max_output_tokens = max_output_tokens

    async def analyze(self, bid_id: UUID) -> AddendumAnalysisResponse:
        context = await self._store.load_context(bid_id)
        if context is None:
            raise AddendumBidNotFound("bid is not registered")
        if not context.original_documents and not context.addendum_documents:
            raise AddendumBidNotReady("bid has no eligible ingested documents with chunks")
        if not context.addendum_documents:
            raise NoAddendaAvailable("bid has no explicitly identified addenda or clarifications")

        run = await self._store.start_run(context, self._model_name)
        token = bind_operation_id(str(run.id))
        warnings: list[str] = []
        rejected = 0
        findings: list[AddendumFinding] = []
        base_ids = {item.document_id for item in context.original_documents}
        prior_addendum_ids: set[UUID] = set()
        try:
            for addendum in context.addendum_documents:
                addendum_chunks = await self._store.load_document_chunks(
                    bid_id,
                    addendum.document_id,
                    self._max_chunks_per_document,
                )
                if not addendum_chunks:
                    warnings.append(
                        f"Addendum document {addendum.document_id} has no readable chunks"
                    )
                    rejected += 1
                    continue

                topics = await self._discover_topics(addendum, addendum_chunks)
                valid_topics = []
                addendum_evidence_ids = {item.chunk_id for item in addendum_chunks}
                for topic in topics:
                    if all(
                        chunk_id in addendum_evidence_ids
                        for chunk_id in topic.addendum_chunk_ids
                    ):
                        valid_topics.append(topic)
                    else:
                        rejected += 1
                        warnings.append(
                            f"Rejected a topic with invalid evidence for document {addendum.document_id}"
                        )
                if not valid_topics:
                    continue

                prior_ids = set(base_ids)
                if not context.ordering_uncertain:
                    prior_ids.update(prior_addendum_ids)
                prior_evidence = await self._retrieve_prior_evidence(
                    context.bid_id,
                    prior_ids,
                    [topic.retrieval_query for topic in valid_topics],
                )
                claims, malformed = await self._compare(
                    context,
                    addendum,
                    addendum_chunks,
                    prior_evidence,
                )
                rejected += len(malformed)
                warnings.extend(malformed)
                accepted, rejected_here, warning_rows = await self._validate_findings(
                    run.id,
                    context,
                    addendum,
                    addendum_chunks,
                    prior_evidence,
                    claims,
                )
                findings.extend(accepted)
                rejected += rejected_here
                warnings.extend(warning_rows)
                if not context.ordering_uncertain:
                    prior_addendum_ids.add(addendum.document_id)

            if context.relationship_uncertain:
                warnings.append(
                    "Original-to-addendum relationship is uncertain because no eligible document "
                    "has explicit original solicitation metadata"
                )
            if context.ordering_uncertain:
                warnings.append(
                    "Addendum order is uncertain; addenda were compared to base documents "
                    "without assuming that one supersedes another"
                )
            status = (
                AddendumAnalysisStatus.PARTIAL
                if rejected
                else AddendumAnalysisStatus.COMPLETED
            )
            completed_at = datetime.now(UTC)
            await self._store.complete_run(run.id, findings, status, completed_at)
            logger.info(
                "Addendum analysis completed run_id=%s bid_id=%s status=%s addenda=%s findings=%s rejected=%s",
                run.id,
                bid_id,
                status.value,
                len(context.addendum_documents),
                len(findings),
                rejected,
            )
            return AddendumAnalysisResponse(
                run_id=run.id,
                bid_id=bid_id,
                status=status,
                model_name=self._model_name,
                original_documents=list(context.original_documents),
                addendum_documents=list(context.addendum_documents),
                relationship_uncertain=context.relationship_uncertain,
                ordering_uncertain=context.ordering_uncertain,
                findings=findings,
                warnings=warnings,
                started_at=run.started_at,
                completed_at=completed_at,
            )
        except InvalidAddendumResponse as exc:
            await self._store.fail_run(run.id, str(exc), datetime.now(UTC))
            raise
        except Exception as exc:
            await self._store.fail_run(
                run.id,
                "Addendum analysis provider or retrieval failed",
                datetime.now(UTC),
            )
            logger.error(
                "Addendum analysis failed run_id=%s bid_id=%s error_type=%s",
                run.id,
                bid_id,
                type(exc).__name__,
            )
            raise AddendumAnalysisUnavailable(
                "addendum analysis service is unavailable"
            ) from exc
        finally:
            reset_operation_id(token)

    async def _discover_topics(
        self,
        addendum: AddendumDocumentInfo,
        chunks: list[RetrievedChunk],
    ):
        prompt = json.dumps(
            {
                "document": addendum.model_dump(mode="json"),
                "chunks": [_chunk_payload(item) for item in chunks],
            },
            ensure_ascii=False,
        )
        try:
            raw = await self._llm.generate_response(
                prompt,
                system=DISCOVERY_SYSTEM_PROMPT,
                temperature=0.0,
                max_output_tokens=self._max_output_tokens,
            )
            return LLMAddendumTopics.model_validate_json(
                _strip_json_fence(raw)
            ).topics
        except (ValidationError, ValueError) as exc:
            raise InvalidAddendumResponse(
                "LLM returned malformed addendum topic data"
            ) from exc

    async def _retrieve_prior_evidence(
        self,
        bid_id: UUID,
        document_ids: set[UUID],
        queries: list[str],
    ) -> list[RetrievedChunk]:
        if not document_ids:
            return []
        found: dict[UUID, RetrievedChunk] = {}
        for query in queries:
            response = await self._retriever.search(
                RetrievalRequest(
                    query=query,
                    bid_id=bid_id,
                    document_ids=sorted(document_ids, key=str),
                    top_k=self._related_top_k,
                    semantic_candidates=max(30, self._related_top_k),
                    keyword_candidates=max(30, self._related_top_k),
                )
            )
            for result in response.results:
                if result.bid_id == bid_id and result.document_id in document_ids:
                    found.setdefault(result.chunk_id, result)
                if len(found) >= self._max_evidence_chunks:
                    return list(found.values())
        return list(found.values())

    async def _compare(
        self,
        context: AddendumContext,
        addendum: AddendumDocumentInfo,
        addendum_chunks: list[RetrievedChunk],
        prior_evidence: list[RetrievedChunk],
    ) -> tuple[list[LLMAddendumFinding], list[str]]:
        prompt = json.dumps(
            {
                "relationship_uncertain": context.relationship_uncertain,
                "ordering_uncertain": context.ordering_uncertain,
                "addendum_document": addendum.model_dump(mode="json"),
                "earlier_evidence": [_chunk_payload(item) for item in prior_evidence],
                "addendum_evidence": [_chunk_payload(item) for item in addendum_chunks],
            },
            ensure_ascii=False,
        )
        raw = await self._llm.generate_response(
            prompt,
            system=COMPARISON_SYSTEM_PROMPT,
            temperature=0.0,
            max_output_tokens=self._max_output_tokens,
        )
        try:
            payload = json.loads(_strip_json_fence(raw))
        except ValueError as exc:
            raise InvalidAddendumResponse(
                "LLM returned malformed addendum finding data"
            ) from exc
        items = payload.get("findings") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise InvalidAddendumResponse("LLM returned malformed addendum finding data")

        max_findings = MAX_FINDINGS_PER_ADDENDUM
        claims: list[LLMAddendumFinding] = []
        warnings: list[str] = []
        for item in items[:max_findings]:
            try:
                claims.append(LLMAddendumFinding.model_validate(item))
            except ValidationError:
                topic = item.get("topic") if isinstance(item, dict) else None
                label = repr(topic[:128]) if isinstance(topic, str) and topic else "(untitled)"
                warnings.append(
                    f"Rejected malformed finding {label} for document {addendum.document_id}"
                )
        if len(items) > max_findings:
            warnings.append(
                f"Ignored {len(items) - max_findings} findings beyond the limit of "
                f"{max_findings} for document {addendum.document_id}"
            )
        return claims, warnings

    async def _validate_findings(
        self,
        run_id: UUID,
        context: AddendumContext,
        addendum: AddendumDocumentInfo,
        addendum_chunks: list[RetrievedChunk],
        prior_evidence: list[RetrievedChunk],
        claims: list[LLMAddendumFinding],
    ) -> tuple[list[AddendumFinding], int, list[str]]:
        addendum_ids = {item.chunk_id for item in addendum_chunks}
        prior_ids = {item.chunk_id for item in prior_evidence}
        claimed_addendum_ids = {
            value for claim in claims for value in claim.addendum_chunk_ids
        }
        claimed_prior_ids = {
            value for claim in claims for value in claim.original_chunk_ids
        }
        authoritative_addendum = await self._store.resolve_citations(
            context.bid_id,
            {addendum.document_id},
            claimed_addendum_ids & addendum_ids,
        )
        prior_document_ids = {item.document_id for item in prior_evidence}
        authoritative_prior = await self._store.resolve_citations(
            context.bid_id,
            prior_document_ids,
            claimed_prior_ids & prior_ids,
        )

        accepted: list[AddendumFinding] = []
        warnings: list[str] = []
        for claim in claims:
            invalid_addendum = [
                value
                for value in claim.addendum_chunk_ids
                if value not in addendum_ids or value not in authoritative_addendum
            ]
            invalid_prior = [
                value
                for value in claim.original_chunk_ids
                if value not in prior_ids or value not in authoritative_prior
            ]
            if invalid_addendum or invalid_prior:
                warnings.append(
                    f"Rejected unsupported finding {claim.topic!r} for document "
                    f"{addendum.document_id}"
                )
                continue
            original_sources = [
                authoritative_prior[value] for value in claim.original_chunk_ids
            ]
            addendum_sources = [
                authoritative_addendum[value]
                for value in claim.addendum_chunk_ids
            ]
            created_at = datetime.now(UTC)
            accepted.append(
                AddendumFinding(
                    id=uuid4(),
                    analysis_run_id=run_id,
                    bid_id=context.bid_id,
                    original_document_id=(
                        original_sources[0].document_id if original_sources else None
                    ),
                    addendum_document_id=addendum.document_id,
                    topic=claim.topic,
                    classification=claim.classification,
                    original_value=claim.original_value,
                    amended_value=claim.amended_value,
                    explanation=claim.explanation,
                    impact_note=claim.impact_note,
                    severity=claim.severity,
                    confidence=claim.confidence,
                    original_sources=original_sources,
                    addendum_sources=addendum_sources,
                    created_at=created_at,
                )
            )
        return accepted, len(claims) - len(accepted), warnings


def _normalized_type(value: str | None) -> str:
    if value is None:
        return ""
    return "_".join(value.strip().lower().replace("-", " ").split())


def _declared_type(document: Document) -> str:
    """Prefer the uploader-declared role; ``document_type`` is usually the file format."""
    role = getattr(document, "document_role", None)
    return _normalized_type(role or document.document_type)


def _document_info(
    document: Document,
    content_sample: str = "",
) -> AddendumDocumentInfo:
    normalized = _declared_type(document)
    if document.addendum_number is not None or normalized in _ADDENDUM_TYPES:
        role = AddendumDocumentRole.ADDENDUM
    elif normalized in _CLARIFICATION_TYPES:
        role = AddendumDocumentRole.CLARIFICATION
    elif normalized in _ORIGINAL_TYPES:
        role = AddendumDocumentRole.ORIGINAL
    elif normalized in _SUPPORTING_TYPES:
        role = AddendumDocumentRole.SUPPORTING
    elif _ADDENDUM_CONTENT.search(content_sample):
        role = AddendumDocumentRole.ADDENDUM
    elif _CLARIFICATION_CONTENT.search(content_sample):
        role = AddendumDocumentRole.CLARIFICATION
    elif _ORIGINAL_CONTENT.search(content_sample):
        role = AddendumDocumentRole.ORIGINAL
    else:
        role = AddendumDocumentRole.OTHER
    return AddendumDocumentInfo(
        document_id=document.id,
        file_name=document.file_name,
        role=role,
        document_type=document.document_type,
        addendum_number=document.addendum_number,
        document_date=document.document_date,
    )


def _order_addenda(
    documents: tuple[AddendumDocumentInfo, ...],
) -> tuple[tuple[AddendumDocumentInfo, ...], bool]:
    if len(documents) <= 1:
        return documents, False
    numbers = [item.addendum_number for item in documents]
    if all(value is not None for value in numbers) and len(set(numbers)) == len(numbers):
        return tuple(sorted(documents, key=lambda item: item.addendum_number or 0)), False
    dates = [item.document_date for item in documents]
    if all(value is not None for value in dates) and len(set(dates)) == len(dates):
        return tuple(sorted(documents, key=lambda item: item.document_date or date.max)), False
    return tuple(sorted(documents, key=lambda item: str(item.document_id))), True


def _retrieved_chunk(chunk: DocumentChunk, document: Document) -> RetrievedChunk:
    section_title = (chunk.chunk_metadata or {}).get(
        "section_title", chunk.section_title
    )
    if not isinstance(section_title, str) or len(section_title) > 512:
        section_title = chunk.section_title
    return RetrievedChunk(
        bid_id=document.bid_id,
        document_id=document.id,
        chunk_id=chunk.id,
        file_name=document.file_name,
        text=chunk.text,
        page_number=chunk.page_number,
        section_title=section_title,
        chunk_type=chunk.chunk_type,
        metadata=dict(chunk.chunk_metadata or {}),
    )


def _citation(chunk: DocumentChunk, document: Document) -> Citation:
    retrieved = _retrieved_chunk(chunk, document)
    return Citation(
        bid_id=retrieved.bid_id,
        document_id=retrieved.document_id,
        chunk_id=retrieved.chunk_id,
        file_name=retrieved.file_name,
        page_number=retrieved.page_number,
        section_title=retrieved.section_title,
        excerpt=retrieved.text,
    )


def _chunk_payload(item: RetrievedChunk) -> dict[str, object]:
    return {
        "chunk_id": str(item.chunk_id),
        "document_id": str(item.document_id),
        "file_name": item.file_name,
        "page_number": item.page_number,
        "section_title": item.section_title,
        "chunk_type": item.chunk_type,
        "text": item.text,
    }


def _strip_json_fence(raw: str) -> str:
    value = raw.strip()
    if value.startswith("```") and value.endswith("```"):
        lines = value.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return value
