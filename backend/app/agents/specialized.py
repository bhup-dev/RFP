"""Bounded, evidence-grounded specialized RFP agents."""

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from app.core.prompt_security import UNTRUSTED_DOCUMENT_POLICY
from app.core.redaction import redact_text
from app.schemas.citation import Citation
from app.schemas.retrieval import RetrievalRequest, RetrievalResponse, RetrievedChunk
from app.schemas.specialized_agents import (
    AgentExecutionStatus,
    EvidenceIssueType,
    EvidenceReviewIssue,
    EvidenceReviewResult,
    FindingClassification,
    LLMSpecializedAgentOutput,
    SpecializedAgentInput,
    SpecializedAgentName,
    SpecializedAgentResult,
    SpecializedFinding,
)
from app.services.specialized_agent_store import CitationCheck


class Retrieval(Protocol):
    async def search(self, request: RetrievalRequest) -> RetrievalResponse: ...


class LLM(Protocol):
    async def generate_response(self, prompt: str, **kwargs: object) -> str: ...


class AgentStore(Protocol):
    async def load_result(
        self, workflow_id: UUID, agent_name: SpecializedAgentName
    ) -> SpecializedAgentResult | None: ...

    async def save_result(self, result: SpecializedAgentResult) -> None: ...

    async def save_review(self, result: EvidenceReviewResult) -> None: ...

    async def validate_citations(
        self, bid_id: UUID, citations: list[Citation]
    ) -> list[CitationCheck]: ...


@dataclass(frozen=True)
class AgentDefinition:
    name: SpecializedAgentName
    query: str
    system: str
    attribute_guidance: str


AGENT_DEFINITIONS = {
    SpecializedAgentName.REQUIREMENTS: AgentDefinition(
        SpecializedAgentName.REQUIREMENTS,
        "scope mandatory requirements obligations evaluation submission exclusions",
        (
            "You analyze explicit RFP requirements. Distinguish mandatory, optional, "
            "informational, and unclear statements. Never infer common procurement "
            "requirements that are absent from the supplied evidence."
        ),
        "scope, obligation, responsible_party, trigger, exclusion",
    ),
    SpecializedAgentName.PRODUCT_SPECIFICATION: AgentDefinition(
        SpecializedAgentName.PRODUCT_SPECIFICATION,
        (
            "products manufacturers model numbers part numbers quantities units "
            "technical specifications warranty compatibility accessories"
        ),
        (
            "You analyze products and technical specifications. Preserve model numbers, "
            "part numbers, quantities, units, and product-to-specification associations "
            "exactly. Never attach a specification to a different product."
        ),
        (
            "product_name, manufacturer, model_number, part_number, quantity, unit, "
            "specifications, warranty, compatibility, accessories"
        ),
    ),
    SpecializedAgentName.COMMERCIAL_TERMS: AgentDefinition(
        SpecializedAgentName.COMMERCIAL_TERMS,
        (
            "submission due date time timezone contract duration renewal payment delivery "
            "warranty bid bond security penalties cooperative purchasing"
        ),
        (
            "You analyze explicit commercial terms. Preserve dates, times, timezones, "
            "deadline triggers, initial terms, renewals, delivery triggers, payment terms, "
            "and bond language. Do not choose between contradictory statements."
        ),
        (
            "term_type, value, date, time, timezone, trigger_event, initial_duration, "
            "renewal_options, payment_terms, bond_requirement"
        ),
    ),
    SpecializedAgentName.COMPLIANCE_DOCUMENTATION: AgentDefinition(
        SpecializedAgentName.COMPLIANCE_DOCUMENTATION,
        (
            "mandatory forms affidavits certifications eligibility signatures registration "
            "submission format channel supporting documents informational attachments"
        ),
        (
            "You analyze submission compliance and documentation. Distinguish required "
            "documents from reference material and blank forms. Do not infer execution, "
            "approval, eligibility, or manufacturer registration."
        ),
        (
            "document_name, requirement_type, deadline_context, submission_channel, "
            "signature_required, registration_type, eligibility_condition"
        ),
    ),
}


class SpecializedRfpAgent:
    def __init__(
        self,
        definition: AgentDefinition,
        retrieval: Retrieval,
        llm: LLM,
        store: AgentStore,
        *,
        model_name: str,
        max_evidence_chunks: int,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> None:
        self.name = definition.name
        self._definition = definition
        self._retrieval = retrieval
        self._llm = llm
        self._store = store
        self._model_name = model_name
        self._max_evidence_chunks = max_evidence_chunks
        self._max_output_tokens = max_output_tokens
        self._timeout_seconds = timeout_seconds

    async def execute(self, request: SpecializedAgentInput) -> SpecializedAgentResult:
        cached = await self._store.load_result(request.workflow_id, self.name)
        if cached is not None:
            if cached.bid_id != request.bid_id:
                raise ValueError("persisted agent result belongs to another bid")
            return cached
        started = datetime.now(UTC)
        run_id = uuid4()
        try:
            async with asyncio.timeout(self._timeout_seconds):
                evidence = await self._retrieval.search(
                    RetrievalRequest(
                        query=self._definition.query,
                        bid_id=request.bid_id,
                        document_ids=request.document_ids,
                        top_k=self._max_evidence_chunks,
                    )
                )
                if not evidence.results:
                    result = SpecializedAgentResult(
                        run_id=run_id,
                        workflow_id=request.workflow_id,
                        bid_id=request.bid_id,
                        agent_name=self.name,
                        status=AgentExecutionStatus.PARTIAL,
                        warnings=["No relevant evidence chunks were retrieved"],
                        evidence_chunk_count=0,
                        model_name=self._model_name,
                        started_at=started,
                        completed_at=datetime.now(UTC),
                    )
                else:
                    raw = await self._llm.generate_response(
                        _prompt(request, self._definition, evidence.results),
                        system=UNTRUSTED_DOCUMENT_POLICY + self._definition.system,
                        temperature=0,
                        max_output_tokens=self._max_output_tokens,
                    )
                    raw_payload = json.loads(_strip_json_fence(raw))
                    _normalize_duplicate_finding_ids(raw_payload)
                    payload = LLMSpecializedAgentOutput.model_validate(raw_payload)
                    result = _resolve_output(
                        request,
                        self._definition,
                        payload,
                        evidence,
                        run_id,
                        started,
                        self._model_name,
                    )
        except Exception as exc:  # noqa: BLE001 - isolate untrusted provider failures
            result = SpecializedAgentResult(
                run_id=run_id,
                workflow_id=request.workflow_id,
                bid_id=request.bid_id,
                agent_name=self.name,
                status=AgentExecutionStatus.FAILED,
                errors=[redact_text(str(exc)) or type(exc).__name__],
                model_name=self._model_name,
                started_at=started,
                completed_at=datetime.now(UTC),
            )
        await self._store.save_result(result)
        return result


class EvidenceReviewAgent:
    """Deterministically verify citations and reconcile bounded agent findings."""

    name = SpecializedAgentName.EVIDENCE_REVIEW

    def __init__(self, store: AgentStore) -> None:
        self._store = store

    async def execute(
        self, request: SpecializedAgentInput
    ) -> EvidenceReviewResult:
        started = datetime.now(UTC)
        issues: list[EvidenceReviewIssue] = []
        valid_count = invalid_count = 0
        for finding in request.prior_findings:
            if not finding.citations:
                issues.append(
                    _issue(
                        finding,
                        EvidenceIssueType.MISSING_EVIDENCE,
                        "Finding has no supporting citation",
                    )
                )
            checks = await self._store.validate_citations(
                request.bid_id, finding.citations
            )
            for check in checks:
                if check.valid:
                    valid_count += 1
                else:
                    invalid_count += 1
                    issue_type = (
                        EvidenceIssueType.CROSS_BID_CITATION
                        if check.reason == "citation belongs to another bid"
                        else EvidenceIssueType.INVALID_CITATION
                    )
                    issues.append(_issue(finding, issue_type, check.reason or "invalid"))
            if finding.unresolved_questions:
                issues.append(
                    _issue(
                        finding,
                        EvidenceIssueType.UNRESOLVED_QUESTION,
                        "Finding retains unresolved questions",
                    )
                )
        issues.extend(_duplicate_issues(request.prior_findings))
        issues.extend(_conflict_issues(request.prior_findings))
        result = EvidenceReviewResult(
            run_id=uuid4(),
            workflow_id=request.workflow_id,
            bid_id=request.bid_id,
            status=(
                AgentExecutionStatus.PARTIAL
                if issues
                else AgentExecutionStatus.COMPLETED
            ),
            issues=issues,
            reviewed_finding_count=len(request.prior_findings),
            valid_citation_count=valid_count,
            invalid_citation_count=invalid_count,
            warnings=[f"{len(issues)} evidence issue(s) require review"] if issues else [],
            started_at=started,
            completed_at=datetime.now(UTC),
        )
        await self._store.save_review(result)
        return result


def build_specialized_agents(
    retrieval: Retrieval,
    llm: LLM,
    store: AgentStore,
    *,
    model_name: str,
    max_evidence_chunks: int,
    max_output_tokens: int,
    timeout_seconds: float,
) -> dict[SpecializedAgentName, SpecializedRfpAgent]:
    return {
        name: SpecializedRfpAgent(
            definition,
            retrieval,
            llm,
            store,
            model_name=model_name,
            max_evidence_chunks=max_evidence_chunks,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
        )
        for name, definition in AGENT_DEFINITIONS.items()
    }


def _prompt(
    request: SpecializedAgentInput,
    definition: AgentDefinition,
    evidence: list[RetrievedChunk],
) -> str:
    rows = [
        {
            "chunk_id": str(item.chunk_id),
            "document_id": str(item.document_id),
            "file_name": item.file_name,
            "page_number": item.page_number,
            "section_title": item.section_title,
            "text": item.text,
        }
        for item in evidence
    ]
    return (
        UNTRUSTED_DOCUMENT_POLICY
        + f"Objective: {request.workflow_objective}\n"
        f"Agent: {definition.name.value}\n"
        f"Store applicable structured attributes using these keys when present: "
        f"{definition.attribute_guidance}.\n"
        "Return JSON only with shape "
        '{"findings":[{"finding_id":"stable-reference","title":"...",'
        '"description":"...","category":"...","classification":"mandatory|optional|'
        'informational|explicit|interpretation|unclear","attributes":{},'
        '"chunk_ids":["uuid"],"confidence":0.0,"unresolved_questions":[],'
        '"notes":null}],"warnings":[]}.\n'
        "Every material claim must cite only supplied chunk IDs. Preserve exact values. "
        "Use unclear and unresolved_questions when evidence is insufficient.\n"
        f"Evidence:\n{json.dumps(rows, ensure_ascii=False)}"
    )


def _resolve_output(
    request: SpecializedAgentInput,
    definition: AgentDefinition,
    payload: LLMSpecializedAgentOutput,
    evidence: RetrievalResponse,
    run_id: UUID,
    started: datetime,
    model_name: str,
) -> SpecializedAgentResult:
    by_id = {item.chunk_id: item for item in evidence.results}
    warnings = [*evidence.warnings, *payload.warnings]
    findings: list[SpecializedFinding] = []
    partial = False
    for claim in payload.findings:
        invalid_ids = [item for item in claim.chunk_ids if item not in by_id]
        if invalid_ids:
            warnings.append(
                f"{claim.finding_id}: rejected {len(invalid_ids)} unsupplied citation(s)"
            )
            partial = True
        citations = [_citation(by_id[item]) for item in claim.chunk_ids if item in by_id]
        review = (
            not citations
            or bool(invalid_ids)
            or bool(claim.unresolved_questions)
            or claim.classification == FindingClassification.UNCLEAR
        )
        if not citations:
            partial = True
            warnings.append(f"{claim.finding_id}: finding has no valid evidence")
        findings.append(
            SpecializedFinding(
                finding_id=f"{definition.name.value}:{claim.finding_id}",
                title=claim.title,
                description=claim.description,
                category=claim.category,
                classification=claim.classification,
                attributes=claim.attributes,
                citations=citations,
                confidence=claim.confidence,
                unresolved_questions=claim.unresolved_questions,
                notes=claim.notes,
                requires_human_review=review,
            )
        )
    return SpecializedAgentResult(
        run_id=run_id,
        workflow_id=request.workflow_id,
        bid_id=request.bid_id,
        agent_name=definition.name,
        status=AgentExecutionStatus.PARTIAL if partial else AgentExecutionStatus.COMPLETED,
        findings=findings,
        warnings=warnings,
        evidence_chunk_count=len(evidence.results),
        model_name=model_name,
        started_at=started,
        completed_at=datetime.now(UTC),
    )


def _citation(item: RetrievedChunk) -> Citation:
    return Citation(
        bid_id=item.bid_id,
        document_id=item.document_id,
        chunk_id=item.chunk_id,
        file_name=item.file_name,
        page_number=item.page_number,
        section_title=item.section_title,
        excerpt=item.text,
    )


def _issue(
    finding: SpecializedFinding,
    issue_type: EvidenceIssueType,
    message: str,
    related: list[str] | None = None,
) -> EvidenceReviewIssue:
    agent_name = SpecializedAgentName(finding.finding_id.split(":", 1)[0])
    return EvidenceReviewIssue(
        issue_type=issue_type,
        agent_name=agent_name,
        finding_id=finding.finding_id,
        message=message,
        related_finding_ids=related or [],
    )


def _duplicate_issues(findings: list[SpecializedFinding]) -> list[EvidenceReviewIssue]:
    seen: dict[tuple[str, str, str], SpecializedFinding] = {}
    issues: list[EvidenceReviewIssue] = []
    for finding in findings:
        key = (
            finding.category.casefold(),
            finding.title.casefold(),
            finding.description.casefold(),
        )
        prior = seen.get(key)
        if prior is not None:
            issues.append(
                _issue(
                    finding,
                    EvidenceIssueType.DUPLICATE_FINDING,
                    "Finding duplicates another specialized-agent finding",
                    [prior.finding_id],
                )
            )
        else:
            seen[key] = finding
    return issues


def _conflict_issues(findings: list[SpecializedFinding]) -> list[EvidenceReviewIssue]:
    keys = {
        "due_date",
        "deadline",
        "delivery_deadline",
        "contract_duration",
        "payment_terms",
        "bond_requirement",
        "model_number",
        "part_number",
    }
    observed: dict[tuple[str, str, str], tuple[object, SpecializedFinding]] = {}
    issues: list[EvidenceReviewIssue] = []
    for finding in findings:
        product = str(finding.attributes.get("product_name", "")).casefold()
        for key in keys & finding.attributes.keys():
            value = finding.attributes[key]
            identity = (finding.category.casefold(), product, key)
            previous = observed.get(identity)
            if previous is not None and previous[0] != value:
                issues.append(
                    _issue(
                        finding,
                        EvidenceIssueType.CONFLICTING_FINDINGS,
                        f"Conflicting values for {key}",
                        [previous[1].finding_id],
                    )
                )
            else:
                observed[identity] = (value, finding)
    return issues


def _strip_json_fence(value: str) -> str:
    cleaned = value.strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        cleaned = cleaned[3:-3].strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].lstrip()
    return cleaned


def _normalize_duplicate_finding_ids(payload: object) -> None:
    """Repair only duplicate model-generated references before strict validation."""
    if not isinstance(payload, dict) or not isinstance(payload.get("findings"), list):
        return
    used: set[str] = set()
    normalized = 0
    for item in payload["findings"]:
        if not isinstance(item, dict) or not isinstance(item.get("finding_id"), str):
            continue
        reference = item["finding_id"]
        if reference not in used:
            used.add(reference)
            continue
        suffix = 2
        while True:
            marker = f"-{suffix}"
            candidate = f"{reference[: 128 - len(marker)]}{marker}"
            if candidate not in used:
                item["finding_id"] = candidate
                used.add(candidate)
                normalized += 1
                break
            suffix += 1
    warnings = payload.get("warnings")
    if normalized and isinstance(warnings, list) and len(warnings) < 20:
        warnings.append(
            f"Normalized {normalized} duplicate model-generated finding ID(s)"
        )
