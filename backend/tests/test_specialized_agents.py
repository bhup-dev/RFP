import asyncio
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.agents.specialized import (
    AGENT_DEFINITIONS,
    EvidenceReviewAgent,
    SpecializedRfpAgent,
)
from app.schemas.citation import Citation
from app.schemas.retrieval import RetrievalResponse, RetrievedChunk
from app.schemas.specialized_agents import (
    AgentExecutionStatus,
    EvidenceIssueType,
    LLMSpecializedAgentOutput,
    SpecializedAgentInput,
    SpecializedAgentName,
    SpecializedFinding,
)
from app.services.specialized_agent_store import CitationCheck


class Store:
    def __init__(self):
        self.cached = None
        self.saved = []
        self.review = None
        self.check_reason = None

    async def load_result(self, *_args):
        return self.cached

    async def save_result(self, result):
        self.saved.append(result)
        self.cached = result

    async def save_review(self, result):
        self.review = result

    async def validate_citations(self, _bid_id, citations):
        return [
            CitationCheck(item, self.check_reason is None, self.check_reason)
            for item in citations
        ]


class Retriever:
    def __init__(self, chunk):
        self.chunk = chunk
        self.requests = []

    async def search(self, request):
        self.requests.append(request)
        return RetrievalResponse(
            query=request.query,
            bid_id=request.bid_id,
            results=[self.chunk],
            total_results=1,
        )


class LLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.last_kwargs = {}

    async def generate_response(self, *_args, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        return self.payload


class SlowLLM:
    async def generate_response(self, *_args, **_kwargs):
        await asyncio.sleep(0.05)
        return "{}"


def _chunk():
    bid_id, document_id, chunk_id = uuid4(), uuid4(), uuid4()
    return RetrievedChunk(
        bid_id=bid_id,
        document_id=document_id,
        chunk_id=chunk_id,
        file_name="unseen-rfp.pdf",
        text="Responses are due October 31 at 2:00 PM ET.",
        page_number=4,
        section_title="Submission",
        chunk_type="text",
    )


def _payload(chunk_id, *, attributes=None, chunk_ids=None):
    return json.dumps(
        {
            "findings": [
                {
                    "finding_id": "finding-1",
                    "title": "Explicit requirement",
                    "description": "Responses are due October 31 at 2:00 PM ET.",
                    "category": "submission_deadline",
                    "classification": "mandatory",
                    "attributes": attributes or {"deadline": "October 31 2:00 PM ET"},
                    "chunk_ids": chunk_ids
                    if chunk_ids is not None
                    else [str(chunk_id)],
                    "confidence": 0.9,
                    "unresolved_questions": [],
                    "notes": None,
                }
            ],
            "warnings": [],
        }
    )


def _agent(name, chunk, llm, store, timeout=1):
    return SpecializedRfpAgent(
        AGENT_DEFINITIONS[name],
        Retriever(chunk),
        llm,
        store,
        model_name="mock",
        max_evidence_chunks=8,
        max_output_tokens=1000,
        timeout_seconds=timeout,
    )


@pytest.mark.parametrize("name", list(AGENT_DEFINITIONS))
def test_each_domain_agent_returns_validated_evidence_grounded_output(name):
    chunk, store = _chunk(), Store()
    llm = LLM(_payload(chunk.chunk_id))
    request = SpecializedAgentInput(
        workflow_id=uuid4(),
        bid_id=chunk.bid_id,
        document_ids=[chunk.document_id],
    )
    result = asyncio.run(_agent(name, chunk, llm, store).execute(request))
    assert result.status == AgentExecutionStatus.COMPLETED
    assert result.agent_name == name
    assert result.findings[0].citations[0].chunk_id == chunk.chunk_id
    assert result.findings[0].citations[0].excerpt == chunk.text
    assert store.saved == [result]
    assert "untrusted document content" in llm.last_kwargs["system"]


def test_product_attributes_preserve_product_model_and_specification_association():
    chunk, store = _chunk(), Store()
    attributes = {
        "product_name": "Router X",
        "model_number": "RX-100/A",
        "part_number": "PN-0042",
        "quantity": 12,
        "unit": "each",
        "specifications": ["dual power supply"],
    }
    result = asyncio.run(
        _agent(
            SpecializedAgentName.PRODUCT_SPECIFICATION,
            chunk,
            LLM(_payload(chunk.chunk_id, attributes=attributes)),
            store,
        ).execute(
            SpecializedAgentInput(
                workflow_id=uuid4(),
                bid_id=chunk.bid_id,
                document_ids=[chunk.document_id],
            )
        )
    )
    assert result.findings[0].attributes == attributes


def test_unsupplied_chunk_reference_is_rejected_and_requires_review():
    chunk, store = _chunk(), Store()
    result = asyncio.run(
        _agent(
            SpecializedAgentName.REQUIREMENTS,
            chunk,
            LLM(_payload(chunk.chunk_id, chunk_ids=[str(uuid4())])),
            store,
        ).execute(
            SpecializedAgentInput(
                workflow_id=uuid4(),
                bid_id=chunk.bid_id,
                document_ids=[chunk.document_id],
            )
        )
    )
    assert result.status == AgentExecutionStatus.PARTIAL
    assert result.findings[0].citations == []
    assert result.findings[0].requires_human_review is True


def test_malformed_output_fails_safely_and_is_persisted():
    chunk, store = _chunk(), Store()
    result = asyncio.run(
        _agent(
            SpecializedAgentName.COMMERCIAL_TERMS,
            chunk,
            LLM("not-json"),
            store,
        ).execute(
            SpecializedAgentInput(
                workflow_id=uuid4(),
                bid_id=chunk.bid_id,
                document_ids=[chunk.document_id],
            )
        )
    )
    assert result.status == AgentExecutionStatus.FAILED
    assert result.findings == []
    assert store.saved[0].status == AgentExecutionStatus.FAILED


def test_agent_timeout_fails_without_unvalidated_findings():
    chunk, store = _chunk(), Store()
    result = asyncio.run(
        _agent(
            SpecializedAgentName.COMPLIANCE_DOCUMENTATION,
            chunk,
            SlowLLM(),
            store,
            timeout=0.001,
        ).execute(
            SpecializedAgentInput(
                workflow_id=uuid4(),
                bid_id=chunk.bid_id,
                document_ids=[chunk.document_id],
            )
        )
    )
    assert result.status == AgentExecutionStatus.FAILED
    assert result.findings == []


def test_completed_persisted_result_prevents_duplicate_llm_execution():
    chunk, store = _chunk(), Store()
    llm = LLM(_payload(chunk.chunk_id))
    agent = _agent(SpecializedAgentName.REQUIREMENTS, chunk, llm, store)
    request = SpecializedAgentInput(
        workflow_id=uuid4(),
        bid_id=chunk.bid_id,
        document_ids=[chunk.document_id],
    )
    first = asyncio.run(agent.execute(request))
    second = asyncio.run(agent.execute(request))
    assert second.run_id == first.run_id
    assert llm.calls == 1
    assert len(store.saved) == 1


def test_cached_agent_result_from_another_bid_is_rejected():
    chunk, store = _chunk(), Store()
    agent = _agent(
        SpecializedAgentName.REQUIREMENTS,
        chunk,
        LLM(_payload(chunk.chunk_id)),
        store,
    )
    original = SpecializedAgentInput(
        workflow_id=uuid4(),
        bid_id=chunk.bid_id,
        document_ids=[chunk.document_id],
    )
    asyncio.run(agent.execute(original))
    with pytest.raises(ValueError, match="another bid"):
        asyncio.run(
            agent.execute(
                original.model_copy(update={"bid_id": uuid4()})
            )
        )


def test_duplicate_llm_finding_ids_are_rejected_by_schema():
    finding = {
        "finding_id": "same",
        "title": "A",
        "description": "B",
        "category": "C",
        "classification": "unclear",
    }
    with pytest.raises(ValidationError, match="finding_id values must be unique"):
        LLMSpecializedAgentOutput.model_validate(
            {"findings": [finding, finding], "warnings": []}
        )


def test_agent_normalizes_duplicate_model_generated_finding_ids():
    chunk, store = _chunk(), Store()
    payload = json.loads(_payload(chunk.chunk_id))
    payload["findings"].append(
        {
            **payload["findings"][0],
            "title": "Second explicit requirement",
        }
    )
    result = asyncio.run(
        _agent(
            SpecializedAgentName.PRODUCT_SPECIFICATION,
            chunk,
            LLM(json.dumps(payload)),
            store,
        ).execute(
            SpecializedAgentInput(
                workflow_id=uuid4(),
                bid_id=chunk.bid_id,
                document_ids=[chunk.document_id],
            )
        )
    )

    assert result.status == AgentExecutionStatus.COMPLETED
    assert [item.finding_id for item in result.findings] == [
        "product_specification:finding-1",
        "product_specification:finding-1-2",
    ]
    assert result.warnings == [
        "Normalized 1 duplicate model-generated finding ID(s)"
    ]


def _finding(reference, *, citations=None, attributes=None, questions=None):
    return SpecializedFinding(
        finding_id=f"commercial_terms:{reference}",
        title="Deadline",
        description="Submission deadline",
        category="submission_deadline",
        classification="explicit",
        attributes=attributes or {},
        citations=citations or [],
        unresolved_questions=questions or [],
    )


def _citation(chunk, *, bid_id=None):
    return Citation(
        bid_id=bid_id or chunk.bid_id,
        document_id=chunk.document_id,
        chunk_id=chunk.chunk_id,
        file_name=chunk.file_name,
        page_number=chunk.page_number,
        section_title=chunk.section_title,
        excerpt=chunk.text,
    )


def test_evidence_review_detects_missing_evidence_duplicates_and_conflicts():
    chunk, store = _chunk(), Store()
    first = _finding(
        "a",
        citations=[_citation(chunk)],
        attributes={"deadline": "October 31 2 PM ET"},
        questions=["Which addendum controls?"],
    )
    second = _finding(
        "b",
        citations=[],
        attributes={"deadline": "November 1 4 PM ET"},
    )
    duplicate = first.model_copy(update={"finding_id": "commercial_terms:c"})
    result = asyncio.run(
        EvidenceReviewAgent(store).execute(
            SpecializedAgentInput(
                workflow_id=uuid4(),
                bid_id=chunk.bid_id,
                document_ids=[chunk.document_id],
                prior_findings=[first, second, duplicate],
            )
        )
    )
    types = {item.issue_type for item in result.issues}
    assert EvidenceIssueType.MISSING_EVIDENCE in types
    assert EvidenceIssueType.DUPLICATE_FINDING in types
    assert EvidenceIssueType.CONFLICTING_FINDINGS in types
    assert EvidenceIssueType.UNRESOLVED_QUESTION in types
    assert result.status == AgentExecutionStatus.PARTIAL


def test_evidence_review_rejects_cross_bid_citation():
    chunk, store = _chunk(), Store()
    store.check_reason = "citation belongs to another bid"
    result = asyncio.run(
        EvidenceReviewAgent(store).execute(
            SpecializedAgentInput(
                workflow_id=uuid4(),
                bid_id=chunk.bid_id,
                document_ids=[chunk.document_id],
                prior_findings=[
                    _finding("x", citations=[_citation(chunk, bid_id=uuid4())])
                ],
            )
        )
    )
    assert result.invalid_citation_count == 1
    assert result.issues[0].issue_type == EvidenceIssueType.CROSS_BID_CITATION
