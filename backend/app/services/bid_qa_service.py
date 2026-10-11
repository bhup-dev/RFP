"""Answer questions about a bid using only its retrieved passages and stored, cited records."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal, Protocol
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.prompt_security import UNTRUSTED_DOCUMENT_POLICY
from app.core.redaction import redact_text
from app.db.models.bid import Bid
from app.db.models.document import Document
from app.schemas.citation import Citation
from app.schemas.qa import (
    AnswerSource,
    AskRequest,
    AskResponse,
    ChatTurn,
    LLMAnswerPayload,
)
from app.schemas.retrieval import (
    RetrievalMode,
    RetrievalRequest,
    RetrievalResponse,
    RetrievedChunk,
)
from app.services.bid_qa_context import (
    POLICY_NOTES,
    BidContext,
    BidRecord,
    BidRecordSource,
    EmptyRecordSource,
    expand_query,
    is_profile_request,
    needs_policy_notes,
)
from app.services.provider_errors import ProviderRequestError
from app.services.workflow_runner import BidNotFound

logger = logging.getLogger(__name__)

NO_PASSAGES_ANSWER = "No relevant passages were found in this bid's documents."
UNSUPPORTED_ANSWER = (
    "I could not find a supported answer to that question in this bid's documents."
)
NO_PROFILE_ANSWER = (
    "This bid has no stored profile yet, so its fields cannot be listed. Process the bid "
    "(extraction and validation) first; the chat does not run extraction."
)
PROFILE_WARNING = "Answered from the stored bid profile; no new extraction was run."
READ_ONLY_WARNING = (
    "This chat is read-only: nothing was created, approved, rejected, re-evaluated, or re-run. "
    "Use the Reviews and bid pages for those actions."
)
_ACTION_REQUEST = re.compile(
    r"^\W*(please\s+)?(create|approve|reject|request\s+re-?evaluation|re-?evaluate|re-?run|"
    r"rerun|resolve|resume|delete|mark|start|run|escalate)\b",
    re.IGNORECASE,
)
REPEAT_SIMILARITY = 0.9
EXCERPT_CHARACTERS = 400
MIN_EXPLANATION_WORDS = 4
_MARKER = re.compile(r"\[([SRP]\d+)\]")
FOLLOW_UP_MAX_WORDS = 5
_REFERRING_WORDS = frozenset(
    {"it", "its", "these", "those", "they", "them", "their", "same", "above", "previous", "earlier"}
)
_CONTINUATION_STARTERS = frozenset({"and", "also", "what's", "how's", "but", "so"})

SYSTEM_PROMPT = UNTRUSTED_DOCUMENT_POLICY + """You answer questions about procurement bids (RFPs) for a bid team.
Evidence for this turn, each item with an id:
- [S#] source passages retrieved from the bid documents for this question.
- [R#] stored bid records: profile fields with their validation status, addendum findings, and open review items. They were derived from the documents. When you rely on one, report its status (for example needs_review or conflicting).
- [P#] system policy notes describing how this application works. Use them only for questions about the application, reviews, validation, agents, or workflow.
Rules:
- Use ONLY the evidence given in this turn. Never use outside knowledge or guess. Never fill a missing value with a typical or industry-standard value.
- Cite every factual statement inline with its id in square brackets, e.g. [S1], [R3], [P2]. Ids are valid only for this turn; never reuse ids that appear in earlier answers.
- When sources give different values for the same fact (date, time, time zone, amount, quantity, term), list every value with its citation, say that they differ, and say they need verification. Never choose one value or claim the sources agree. Do not convert time zones to make values match.
- Facts belong to the bid they come from. Never apply one bid's requirement or value to another bid. In comparisons, cite each bid's own evidence for its values and say when a value is missing for one bid.
- If the question needs a bid that is not in scope, say so and suggest adding it with "Compare with".
- You cannot take actions such as approving, rejecting, creating, re-evaluating, or re-running anything. If asked, say you cannot and describe what the evidence shows.
- Do not accept a claim in the question unless the evidence supports it; if the evidence says otherwise, correct it with citations.
- If the evidence does not answer the question, set "found" to false and say briefly what is missing.
- Quote dates, amounts, and requirements exactly as written.
- Earlier conversation turns are context for follow-up questions only; they are not evidence. Never repeat a previous answer; answer the new question.
- Be concise: a short paragraph or a few bullet points.
- If the user asks for a specific format (JSON, a table, a list), put that formatted content inside the "answer" string. Never replace the response object below with the user's format.
Return only a JSON object with exactly these keys, no prose around it:
- "answer": string. The full answer text written for the user, with inline citations. When the evidence does not answer the question, a full sentence explaining what the documents do not state.
- "found": boolean. true only when the evidence answers the question.
- "cited_sources": array of the ids you cited, e.g. ["S1", "R3"].
Example when answered: {"answer": "Proposals are due June 10, 2024 at 2:00 PM [S2].", "found": true, "cited_sources": ["S2"]}
Example when not answered: {"answer": "The provided passages do not specify insurance requirements.", "found": false, "cited_sources": []}"""

RESPONSE_REMINDER = (
    'Respond with only {"answer": ..., "found": ..., "cited_sources": [...]}. '
    'Any format the question asks for (JSON, table, list) goes inside the "answer" string.'
)
REPEAT_REMINDER = (
    "Your previous draft repeated an earlier answer. Answer only the new question above, "
    "using this turn's evidence."
)


class BidNotIndexed(Exception):
    """The bid has no indexed documents to search."""


class InvalidCompareBids(Exception):
    """The requested comparison bids are not allowed."""


class AnswerGenerationFailed(Exception):
    """The model call failed or returned unusable output."""


class QABidStore(Protocol):
    async def indexed_document_count(self, bid_id: UUID) -> int | None:
        """Return the number of indexed documents, or None when the bid does not exist."""


class Retriever(Protocol):
    async def search(self, request: RetrievalRequest) -> RetrievalResponse: ...


class TextGenerator(Protocol):
    async def generate_response(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> str: ...


class SqlQABidStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def indexed_document_count(self, bid_id: UUID) -> int | None:
        if await self._session.get(Bid, bid_id) is None:
            return None
        count = await self._session.scalar(
            select(func.count(Document.id)).where(
                Document.bid_id == bid_id,
                Document.ingestion_status == "indexed",
            )
        )
        return int(count or 0)


@dataclass(frozen=True)
class QASettings:
    top_k: int
    max_history_turns: int
    max_output_tokens: int
    max_passage_characters: int
    model: str | None = None
    max_compare_bids: int = 1


@dataclass(frozen=True)
class Evidence:
    """One numbered item the model may cite."""

    kind: Literal["passage", "record", "policy"]
    bid_id: UUID | None = None
    bid_label: str | None = None
    chunk: RetrievedChunk | None = None
    record: BidRecord | None = None
    policy: str | None = None


class BidQAService:
    def __init__(
        self,
        *,
        store: QABidStore,
        retrieval: Retriever,
        llm: TextGenerator,
        settings: QASettings,
        records: BidRecordSource | None = None,
    ) -> None:
        self._store = store
        self._retrieval = retrieval
        self._llm = llm
        self._settings = settings
        self._records = records or EmptyRecordSource()

    async def ask(self, bid_id: UUID, request: AskRequest) -> AskResponse:
        bid_ids = await self._bids_in_scope(bid_id, request.compare_bid_ids)
        contexts = [await self._records.load(item) for item in bid_ids]
        base = {
            "bid_id": bid_id,
            "question": request.question,
            "warnings": [READ_ONLY_WARNING] if is_action_request(request.question) else [],
            "model": self._settings.model,
        }
        if is_profile_request(request.question):
            return profile_response(contexts[0], base)

        history = self._recent_history(request.history)
        query = expand_query(search_query(request.question, history))
        passages: list[Evidence] = []
        retrieval_mode: RetrievalMode | None = None
        for context in contexts:
            response = await self._retrieval.search(
                RetrievalRequest(
                    query=query,
                    bid_id=context.bid_id,
                    document_ids=list(request.document_ids) if context.bid_id == bid_id else [],
                    top_k=self._settings.top_k,
                )
            )
            retrieval_mode = retrieval_mode or response.retrieval_mode
            base["warnings"].extend(item for item in response.warnings if item not in base["warnings"])
            passages.extend(
                Evidence("passage", context.bid_id, context.label, chunk=chunk)
                for chunk in unique_passages(
                    [chunk for chunk in response.results if chunk.bid_id == context.bid_id]
                )
            )
        base["retrieval_mode"] = retrieval_mode
        passages = _unique_evidence_passages(passages)

        records = [
            Evidence("record", context.bid_id, context.label, record=record)
            for context in contexts
            for record in context.records
        ]
        policies = (
            [Evidence("policy", policy=note) for note in POLICY_NOTES]
            if needs_policy_notes(request.question)
            else []
        )
        if not passages and not records and not policies:
            return AskResponse(answer=NO_PASSAGES_ANSWER, found=False, **base)

        numbered = number_evidence(passages, records, policies)
        payload = await self._generate(request.question, history, numbered, contexts)
        return grounded_response(payload, numbered, base)

    async def _bids_in_scope(self, bid_id: UUID, compare_bid_ids: list[UUID]) -> list[UUID]:
        if bid_id in compare_bid_ids:
            raise InvalidCompareBids("a bid cannot be compared with itself")
        if len(compare_bid_ids) > self._settings.max_compare_bids:
            raise InvalidCompareBids(
                f"at most {self._settings.max_compare_bids} bid(s) can be added for comparison"
            )
        bid_ids = [bid_id, *compare_bid_ids]
        for item in bid_ids:
            indexed = await self._store.indexed_document_count(item)
            if indexed is None:
                raise BidNotFound("bid not found" if item == bid_id else "comparison bid not found")
            if indexed == 0:
                raise BidNotIndexed(
                    "bid has no indexed documents"
                    if item == bid_id
                    else "comparison bid has no indexed documents"
                )
        return bid_ids

    def _recent_history(self, history: list[ChatTurn]) -> list[ChatTurn]:
        limit = self._settings.max_history_turns
        return history[-limit:] if limit else []

    async def _generate(
        self,
        question: str,
        history: list[ChatTurn],
        numbered: dict[str, Evidence],
        contexts: list[BidContext],
    ) -> LLMAnswerPayload:
        prompt = build_prompt(
            question, history, numbered, contexts, self._settings.max_passage_characters
        )
        payload = await self._call(prompt)
        if repeats_previous_answer(payload.answer, question, history):
            logger.info("Q&A answer repeated the previous turn; regenerating once")
            payload = await self._call(f"{prompt}\n{REPEAT_REMINDER}")
            if repeats_previous_answer(payload.answer, question, history):
                return LLMAnswerPayload(answer=UNSUPPORTED_ANSWER, found=False)
        return payload

    async def _call(self, prompt: str) -> LLMAnswerPayload:
        try:
            raw = await self._llm.generate_response(
                prompt,
                system=SYSTEM_PROMPT,
                temperature=0.0,
                max_output_tokens=self._settings.max_output_tokens,
            )
        except ProviderRequestError as exc:
            raise AnswerGenerationFailed(redact_text(str(exc))) from exc
        text = _strip_json_fence(raw)
        try:
            return LLMAnswerPayload.model_validate_json(text)
        except (ValidationError, ValueError) as exc:
            repaired = _quote_bare_cited_ids(text)
            if repaired != text:
                try:
                    return LLMAnswerPayload.model_validate_json(repaired)
                except (ValidationError, ValueError):
                    pass
            logger.warning("Q&A model returned malformed JSON")
            raise AnswerGenerationFailed(
                "the model returned an answer in an unexpected format; "
                "try a narrower question or fewer fields at a time"
            ) from exc


def is_follow_up(question: str) -> bool:
    """A short or back-referencing question needs the previous turn to be searchable."""
    words = re.findall(r"[a-z0-9']+", question.lower())
    if len(words) <= FOLLOW_UP_MAX_WORDS:
        return True
    return bool(words) and (words[0] in _CONTINUATION_STARTERS or bool(_REFERRING_WORDS & set(words)))


def search_query(question: str, history: list[ChatTurn]) -> str:
    previous = next(
        (turn.content for turn in reversed(history) if turn.role == "user"), None
    )
    if previous and previous.strip() != question and is_follow_up(question):
        return f"{question}\n{previous.strip()}"[:2000]
    return question


def unique_passages(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Drop passages whose text repeats an earlier one apart from whitespace and case."""
    seen: set[str] = set()
    unique: list[RetrievedChunk] = []
    for chunk in chunks:
        key = " ".join(chunk.text.split()).casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(chunk)
    return unique


def _unique_evidence_passages(items: list[Evidence]) -> list[Evidence]:
    seen: set[UUID] = set()
    unique = []
    for item in items:
        assert item.chunk is not None
        if item.chunk.chunk_id in seen:
            continue
        seen.add(item.chunk.chunk_id)
        unique.append(item)
    return unique


def number_evidence(
    passages: list[Evidence], records: list[Evidence], policies: list[Evidence]
) -> dict[str, Evidence]:
    numbered: dict[str, Evidence] = {}
    for prefix, items in (("S", passages), ("R", records), ("P", policies)):
        for index, item in enumerate(items, start=1):
            numbered[f"{prefix}{index}"] = item
    return numbered


def strip_citation_markers(text: str) -> str:
    text = re.sub(r"[ \t]+([.,;:!?)])", r"\1", _MARKER.sub("", text))
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _normalized(text: str) -> str:
    return " ".join(strip_citation_markers(text).split()).casefold()


def is_action_request(question: str) -> bool:
    return bool(_ACTION_REQUEST.search(question))


def repeats_previous_answer(answer: str, question: str, history: list[ChatTurn]) -> bool:
    previous_answer = next((turn.content for turn in reversed(history) if turn.role == "assistant"), None)
    previous_question = next((turn.content for turn in reversed(history) if turn.role == "user"), None)
    if not previous_answer or _normalized(question) == _normalized(previous_question or ""):
        return False
    current, previous = _normalized(answer), _normalized(previous_answer)
    if not current:
        return False
    return current == previous or SequenceMatcher(None, current, previous).ratio() >= REPEAT_SIMILARITY


def build_prompt(
    question: str,
    history: list[ChatTurn],
    numbered: dict[str, Evidence],
    contexts: list[BidContext],
    max_passage_characters: int,
) -> str:
    parts: list[str] = []
    if history:
        parts.append("Conversation so far (context only, not evidence; citation ids in it are void):")
        parts.extend(
            f"{turn.role.upper()}: "
            f"{strip_citation_markers(turn.content) if turn.role == 'assistant' else turn.content}"
            for turn in history
        )
        parts.append("")

    parts.append("Bids in scope:")
    for index, context in enumerate(contexts):
        role = "the bid this chat is about" if index == 0 else "added for comparison"
        parts.append(f"- {context.label} ({role})")
    if len(contexts) == 1:
        parts.append("Only this bid is in scope.")
    parts.append("")

    records = {key: item for key, item in numbered.items() if item.kind == "record"}
    if records:
        parts.append("Bid records (stored analysis results; verify against passages when possible):")
        for source_id, item in records.items():
            assert item.record is not None
            line = f"[{source_id}] (Bid: {item.bid_label}) {item.record.text}"
            if item.record.citations:
                line += f" Sources: {'; '.join(_where(c) for c in item.record.citations)}."
            parts.append(line)
        parts.append("")

    policies = {key: item for key, item in numbered.items() if item.kind == "policy"}
    if policies:
        parts.append("System policy notes (how this application works; not bid evidence):")
        parts.extend(f"[{source_id}] {item.policy}" for source_id, item in policies.items())
        parts.append("")

    parts.append("Source passages:")
    for source_id, item in numbered.items():
        if item.kind != "passage":
            continue
        chunk = item.chunk
        assert chunk is not None
        location = [f"Bid: {item.bid_label}", chunk.file_name]
        if chunk.page_number:
            location.append(f"page {chunk.page_number}")
        if chunk.section_title:
            location.append(f"section: {chunk.section_title}")
        parts.append(f"[{source_id}] ({', '.join(location)})")
        parts.append(chunk.text[:max_passage_characters])
        parts.append("")
    parts.append(f"Question: {question}")
    parts.append("")
    parts.append(RESPONSE_REMINDER)
    return "\n".join(parts)


def grounded_response(
    payload: LLMAnswerPayload,
    numbered: dict[str, Evidence],
    base: dict,
) -> AskResponse:
    """Keep only citations that point at this turn's evidence; never return uncited claims."""
    answer = payload.answer.strip()
    cited = [*payload.cited_sources, *_MARKER.findall(answer)]
    kept = list(dict.fromkeys(item for item in cited if item in numbered))
    answer = _MARKER.sub(lambda match: match.group(0) if match.group(1) in numbered else "", answer)
    answer = re.sub(r"[ \t]{2,}", " ", answer).strip()
    sources = [source for item in kept for source in evidence_sources(item, numbered[item])]

    if not payload.found:
        return AskResponse(
            answer=answer if len(answer.split()) >= MIN_EXPLANATION_WORDS else UNSUPPORTED_ANSWER,
            found=False,
            sources=sources,
            **base,
        )
    if not kept or not answer:
        return AskResponse(answer=UNSUPPORTED_ANSWER, found=False, **base)
    return AskResponse(answer=answer, found=True, sources=sources, **base)


def evidence_sources(source_id: str, item: Evidence) -> list[AnswerSource]:
    if item.kind == "passage":
        chunk = item.chunk
        assert chunk is not None
        return [
            AnswerSource(
                source_id=source_id,
                bid_id=item.bid_id,
                bid_label=item.bid_label,
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                file_name=chunk.file_name,
                page_number=chunk.page_number,
                section_title=chunk.section_title,
                excerpt=_excerpt(chunk.text),
            )
        ]
    if item.kind == "policy":
        return [AnswerSource(source_id=source_id, kind="policy", excerpt=item.policy or "")]
    record = item.record
    assert record is not None
    if not record.citations:
        return [
            AnswerSource(
                source_id=source_id,
                kind="record",
                bid_id=item.bid_id,
                bid_label=item.bid_label,
                excerpt=_excerpt(record.text),
                record=record.text,
            )
        ]
    return [
        _citation_source(source_id, citation, item.bid_label, record.text)
        for citation in record.citations
    ]


def _citation_source(
    source_id: str, citation: Citation, bid_label: str | None, record: str | None
) -> AnswerSource:
    return AnswerSource(
        source_id=source_id,
        kind="record",
        bid_id=citation.bid_id,
        bid_label=bid_label,
        chunk_id=citation.chunk_id,
        document_id=citation.document_id,
        file_name=citation.file_name,
        page_number=citation.page_number,
        section_title=citation.section_title,
        excerpt=_excerpt(citation.excerpt),
        record=record,
    )


def profile_response(context: BidContext, base: dict) -> AskResponse:
    """Answer an all-fields request from the stored profile without calling the model."""
    profile = context.profile
    if profile is None:
        return AskResponse(answer=NO_PROFILE_ANSWER, found=False, **base)
    fields: dict[str, dict] = {}
    sources: list[AnswerSource] = []
    for index, (name, item) in enumerate(profile.fields.items(), start=1):
        source_id = f"R{index}"
        citations = [*item.supporting_citations, *item.contradicting_citations]
        fields[name] = {
            "value": item.value,
            "validation_status": item.validation_status.value,
            "confidence_score": item.confidence_score,
            "citations": [
                {
                    "source_id": source_id,
                    "file_name": citation.file_name,
                    "page_number": citation.page_number,
                    "role": "contradicting" if citation in item.contradicting_citations else "supporting",
                }
                for citation in citations
            ],
        }
        sources.extend(
            _citation_source(source_id, citation, context.label, f'Profile field "{name}"')
            for citation in citations
        )
    warnings = [*base["warnings"], PROFILE_WARNING, f"Profile status: {profile.metadata.status.value}"]
    return AskResponse(
        **{**base, "warnings": warnings},
        answer=json.dumps(fields, indent=2, ensure_ascii=False, default=str),
        found=any(item["value"] is not None for item in fields.values()),
        sources=sources,
    )


def _excerpt(text: str) -> str:
    excerpt = text.strip()
    if len(excerpt) > EXCERPT_CHARACTERS:
        excerpt = excerpt[: EXCERPT_CHARACTERS - 1].rstrip() + "…"
    return excerpt


def _where(citation: Citation) -> str:
    page = f" page {citation.page_number}" if citation.page_number else ""
    return f"{citation.file_name}{page}"


_CITED_ARRAY = re.compile(r'("cited_sources"\s*:\s*\[)([^\]"]*)(\])')


def _quote_bare_cited_ids(text: str) -> str:
    """Quote unquoted ids such as [R8] in the cited_sources array; nothing else is changed."""
    return _CITED_ARRAY.sub(
        lambda match: match.group(1)
        + re.sub(r"\b([SRP]\d+)\b", r'"\1"', match.group(2))
        + match.group(3),
        text,
    )


def _strip_json_fence(raw: str) -> str:
    value = raw.strip()
    if value.startswith("```") and value.endswith("```"):
        lines = value.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return value
