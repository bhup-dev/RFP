"""PostgreSQL history and citation integrity for specialized agents."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.chunk import DocumentChunk
from app.db.models.document import Document
from app.db.models.specialized_agent import (
    SpecializedAgentFinding,
    SpecializedAgentRun,
)
from app.schemas.citation import Citation
from app.schemas.specialized_agents import (
    AgentExecutionStatus,
    EvidenceReviewResult,
    FindingClassification,
    SpecializedAgentName,
    SpecializedAgentResult,
    SpecializedFinding,
)


@dataclass(frozen=True)
class CitationCheck:
    citation: Citation
    valid: bool
    reason: str | None = None


class SqlSpecializedAgentStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def load_result(
        self, workflow_id: UUID, agent_name: SpecializedAgentName
    ) -> SpecializedAgentResult | None:
        run = await self._session.scalar(
            select(SpecializedAgentRun)
            .options(selectinload(SpecializedAgentRun.findings))
            .where(
                SpecializedAgentRun.workflow_id == workflow_id,
                SpecializedAgentRun.agent_name == agent_name.value,
                SpecializedAgentRun.status.in_(["completed", "partial"]),
            )
        )
        if run is None or run.completed_at is None:
            return None
        return SpecializedAgentResult(
            run_id=run.id,
            workflow_id=run.workflow_id,
            bid_id=run.bid_id,
            agent_name=SpecializedAgentName(run.agent_name),
            status=AgentExecutionStatus(run.status),
            findings=[
                SpecializedFinding(
                    finding_id=item.finding_key,
                    title=item.title,
                    description=item.description,
                    category=item.category,
                    classification=FindingClassification(item.classification),
                    attributes=item.attributes,
                    citations=[Citation.model_validate(value) for value in item.citations],
                    confidence=float(item.confidence)
                    if item.confidence is not None
                    else None,
                    unresolved_questions=item.unresolved_questions,
                    notes=item.notes,
                    requires_human_review=item.requires_human_review,
                )
                for item in sorted(run.findings, key=lambda row: row.finding_key)
            ],
            warnings=run.warnings,
            errors=run.errors,
            evidence_chunk_count=run.evidence_chunk_count,
            model_name=run.model_name,
            started_at=run.started_at,
            completed_at=run.completed_at,
        )

    async def save_result(self, result: SpecializedAgentResult) -> None:
        run = await self._session.scalar(
            select(SpecializedAgentRun)
            .options(selectinload(SpecializedAgentRun.findings))
            .where(
                SpecializedAgentRun.workflow_id == result.workflow_id,
                SpecializedAgentRun.agent_name == result.agent_name.value,
            )
        )
        if run is None:
            run = SpecializedAgentRun(
                id=result.run_id,
                workflow_id=result.workflow_id,
                bid_id=result.bid_id,
                agent_name=result.agent_name.value,
                status=result.status.value,
                model_name=result.model_name,
                evidence_chunk_count=result.evidence_chunk_count,
                warnings=result.warnings,
                errors=result.errors,
                started_at=result.started_at,
                completed_at=result.completed_at,
            )
            self._session.add(run)
        else:
            if run.bid_id != result.bid_id:
                raise ValueError("agent run bid does not match workflow bid")
            run.status = result.status.value
            run.model_name = result.model_name
            run.evidence_chunk_count = result.evidence_chunk_count
            run.warnings = result.warnings
            run.errors = result.errors
            run.completed_at = result.completed_at
            run.findings.clear()
        for finding in result.findings:
            run.findings.append(
                SpecializedAgentFinding(
                    bid_id=result.bid_id,
                    finding_key=finding.finding_id,
                    title=finding.title,
                    description=finding.description,
                    category=finding.category,
                    classification=finding.classification.value,
                    attributes=finding.attributes,
                    citations=[
                        citation.model_dump(mode="json")
                        for citation in finding.citations
                    ],
                    confidence=finding.confidence,
                    unresolved_questions=finding.unresolved_questions,
                    notes=finding.notes,
                    requires_human_review=finding.requires_human_review,
                )
            )
        await self._session.commit()

    async def save_review(self, result: EvidenceReviewResult) -> None:
        run = await self._session.scalar(
            select(SpecializedAgentRun).where(
                SpecializedAgentRun.workflow_id == result.workflow_id,
                SpecializedAgentRun.agent_name == result.agent_name.value,
            )
        )
        values = [issue.model_dump(mode="json") for issue in result.issues]
        if run is None:
            run = SpecializedAgentRun(
                id=result.run_id,
                workflow_id=result.workflow_id,
                bid_id=result.bid_id,
                agent_name=result.agent_name.value,
                status=result.status.value,
                model_name=None,
                evidence_chunk_count=result.valid_citation_count,
                warnings=result.warnings,
                errors=result.errors,
                review_issues=values,
                started_at=result.started_at,
                completed_at=result.completed_at,
            )
            self._session.add(run)
        else:
            if run.bid_id != result.bid_id:
                raise ValueError("evidence review bid does not match workflow bid")
            run.status = result.status.value
            run.evidence_chunk_count = result.valid_citation_count
            run.warnings = result.warnings
            run.errors = result.errors
            run.review_issues = values
            run.completed_at = result.completed_at
        await self._session.commit()

    async def validate_citations(
        self, bid_id: UUID, citations: list[Citation]
    ) -> list[CitationCheck]:
        if not citations:
            return []
        ids = list(dict.fromkeys(item.chunk_id for item in citations))
        rows = (
            await self._session.execute(
                select(DocumentChunk, Document)
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(DocumentChunk.id.in_(ids))
            )
        ).all()
        stored = {chunk.id: (chunk, document) for chunk, document in rows}
        checks: list[CitationCheck] = []
        for citation in citations:
            row = stored.get(citation.chunk_id)
            reason: str | None = None
            if row is None:
                reason = "chunk does not exist"
            else:
                chunk, document = row
                if document.bid_id != bid_id or citation.bid_id != bid_id:
                    reason = "citation belongs to another bid"
                elif citation.document_id != document.id:
                    reason = "document id does not match chunk"
                elif citation.file_name != document.file_name:
                    reason = "file name does not match document"
                elif citation.excerpt not in chunk.text:
                    reason = "excerpt does not match persisted chunk"
                elif citation.page_number != chunk.page_number:
                    reason = "page number does not match chunk"
            checks.append(CitationCheck(citation, reason is None, reason))
        return checks
