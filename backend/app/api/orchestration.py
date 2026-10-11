"""LangGraph bid-intelligence workflow endpoint."""

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.specialized import EvidenceReviewAgent, build_specialized_agents
from app.agents.workflow import WorkflowDependencies
from app.api.addendum_analysis import get_addendum_analysis_service
from app.api.extraction import get_extraction_service
from app.api.extraction_validation import get_extraction_validation_service
from app.api.intelligence_profile import get_bid_intelligence_service
from app.config import settings
from app.db.session import get_session_factory
from app.retrieval.citations import CitationResolver
from app.retrieval.keyword_search import KeywordSearch
from app.retrieval.retrieval_service import RetrievalService
from app.retrieval.semantic_search import SemanticSearch
from app.schemas.orchestration import BidWorkflowResponse
from app.schemas.specialized_agents import SpecializedAgentName
from app.services.embedding_service import get_embedding_service
from app.services.llm_service import get_llm_service
from app.services.orchestration_store import SqlOrchestrationStore
from app.services.qdrant_service import get_qdrant_service
from app.services.specialized_agent_store import SqlSpecializedAgentStore
from app.observability.instrumentation import SqlNodeObserver
from app.services.review_service import ReviewService
from app.services.workflow_review_manager import WorkflowReviewManager
from app.services.workflow_runner import BidNotFound, execute_bid_workflow

router = APIRouter(prefix=f"{settings.api_prefix}/v1/bids", tags=["orchestration"])


async def get_orchestration_session() -> AsyncIterator[AsyncSession]:
    """One sequential session shared by graph nodes and existing Task 3 stores."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            if session.in_transaction():
                await session.rollback()


def get_workflow_dependencies(
    session: AsyncSession = Depends(get_orchestration_session),
) -> WorkflowDependencies:
    store = SqlOrchestrationStore(session)
    retrieval = RetrievalService(
        SemanticSearch(
            get_embedding_service(),
            get_qdrant_service(),
            settings.embedding_dimension,
        ),
        KeywordSearch(session),
        rrf_constant=settings.search_rrf_constant,
        allow_degraded=settings.search_allow_degraded,
        citation_validator=CitationResolver(session),
    )
    agent_store = SqlSpecializedAgentStore(session)
    agents = build_specialized_agents(
        retrieval,
        get_llm_service(),
        agent_store,
        model_name=settings.llm_model,
        max_evidence_chunks=settings.specialized_agent_max_evidence_chunks,
        max_output_tokens=settings.specialized_agent_max_output_tokens,
        timeout_seconds=settings.specialized_agent_timeout_seconds,
    )
    review_manager = WorkflowReviewManager(
        ReviewService(session),
        confidence_threshold=settings.human_review_confidence_threshold,
        critical_fields={
            item.strip()
            for item in settings.human_review_critical_fields.split(",")
            if item.strip()
        },
    )
    return WorkflowDependencies(
        context_store=store,
        workflow_store=store,
        extraction=get_extraction_service(session),
        addendum=get_addendum_analysis_service(session),
        validation=get_extraction_validation_service(session),
        profile=get_bid_intelligence_service(session),
        requirements_agent=agents[SpecializedAgentName.REQUIREMENTS],
        product_agent=agents[SpecializedAgentName.PRODUCT_SPECIFICATION],
        commercial_agent=agents[SpecializedAgentName.COMMERCIAL_TERMS],
        compliance_agent=agents[SpecializedAgentName.COMPLIANCE_DOCUMENTATION],
        evidence_review_agent=EvidenceReviewAgent(agent_store),
        specialized_agents_enabled=settings.specialized_agents_enabled,
        observer=SqlNodeObserver(session),
        review_manager=review_manager,
        human_review_interrupt_enabled=settings.human_review_enabled,
    )


@router.post("/{bid_id}/workflow", response_model=BidWorkflowResponse)
async def run_bid_workflow(
    bid_id: UUID,
    dependencies: WorkflowDependencies = Depends(get_workflow_dependencies),
) -> BidWorkflowResponse:
    """Create a new append-only run and execute the compiled StateGraph."""
    try:
        result = await execute_bid_workflow(bid_id, dependencies)
    except BidNotFound as exc:
        raise HTTPException(status_code=404, detail="bid not found") from exc
    return BidWorkflowResponse.from_state(result)
