"""LangGraph StateGraph coordinating the existing Task 3 services."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.state import BidIntelligenceState
from app.core.redaction import redact_text
from app.services.addendum_analysis_service import NoAddendaAvailable
from app.schemas.specialized_agents import (
    AgentExecutionStatus,
    EvidenceReviewResult,
    SpecializedAgentInput,
    SpecializedAgentName,
    SpecializedAgentResult,
    SpecializedFinding,
)
from app.services.orchestration_store import BidWorkflowContext
from app.observability.instrumentation import NodeObserver, instrument_node

logger = logging.getLogger(__name__)


class ContextStore(Protocol):
    async def load_bid_context(self, bid_id: UUID) -> BidWorkflowContext: ...


class WorkflowStore(Protocol):
    async def save_state(self, state: BidIntelligenceState) -> None: ...


class ExtractionService(Protocol):
    async def extract(self, bid_id: UUID): ...


class AddendumService(Protocol):
    async def analyze(self, bid_id: UUID): ...


class ValidationService(Protocol):
    async def validate(self, bid_id: UUID, extraction_run_id: UUID): ...


class ProfileService(Protocol):
    async def build(self, bid_id: UUID, extraction_run_id: UUID | None = None): ...


class DomainAgent(Protocol):
    async def execute(
        self, request: SpecializedAgentInput
    ) -> SpecializedAgentResult: ...


class ReviewAgent(Protocol):
    async def execute(self, request: SpecializedAgentInput) -> EvidenceReviewResult: ...


class HumanReviewManager(Protocol):
    async def create_items(self, state: BidIntelligenceState) -> list[UUID]: ...

    async def all_resolved(self, workflow_id: UUID) -> bool: ...


@dataclass(frozen=True)
class WorkflowDependencies:
    context_store: ContextStore
    workflow_store: WorkflowStore
    extraction: ExtractionService
    addendum: AddendumService
    validation: ValidationService
    profile: ProfileService
    requirements_agent: DomainAgent | None = None
    product_agent: DomainAgent | None = None
    commercial_agent: DomainAgent | None = None
    compliance_agent: DomainAgent | None = None
    evidence_review_agent: ReviewAgent | None = None
    specialized_agents_enabled: bool = False
    observer: NodeObserver | None = None
    review_manager: HumanReviewManager | None = None
    human_review_interrupt_enabled: bool = False


class BidIntelligenceGraph:
    """Typed graph nodes. Runtime clients remain dependencies, never graph state."""

    def __init__(self, dependencies: WorkflowDependencies) -> None:
        self._deps = dependencies

    async def validate_bid_context(
        self, state: BidIntelligenceState
    ) -> dict[str, Any]:
        stage = "validate_bid_context"
        await self._mark_running(state, stage)
        try:
            context = await self._deps.context_store.load_bid_context(state["bid_id"])
            if not context.exists:
                return await self._failure(
                    state, stage, "bid_not_found", "Bid does not exist"
                )
            if not context.eligible_document_ids:
                return await self._failure(
                    state,
                    stage,
                    "no_eligible_documents",
                    "Bid has no eligible persisted documents",
                    document_ids=context.document_ids,
                )
            warnings = list(state["warnings"])
            if context.failed_document_ids:
                warnings.append(
                    f"{len(context.failed_document_ids)} document(s) failed ingestion"
                )
            update = {
                "current_stage": stage,
                "stage_statuses": _stage(state, stage, "completed"),
                "document_ids": context.document_ids,
                "eligible_document_count": len(context.eligible_document_ids),
                "warnings": warnings,
            }
            await self._save(state, update)
            self._log(state, stage, "completed")
            return update
        except Exception as exc:
            return await self._failure(state, stage, "context_validation_failed", exc)

    async def extract_rfp_fields(
        self, state: BidIntelligenceState
    ) -> dict[str, Any]:
        stage = "extract_rfp_fields"
        await self._mark_running(state, stage)
        try:
            result = await self._deps.extraction.extract(state["bid_id"])
            fields = result.fields.model_dump(by_alias=True)
            extracted = sum(
                1
                for field in fields.values()
                if field is not None and field.get("value") is not None
            )
            warnings = list(state["warnings"])
            if result.status.value != "completed":
                warnings.append(f"Extraction finished with status {result.status.value}")
            update = {
                "current_stage": stage,
                "stage_statuses": _stage(state, stage, "completed"),
                "extraction_run_id": result.run_id,
                "extraction_summary": {
                    "status": result.status.value,
                    "field_count": len(fields),
                    "fields_with_values": extracted,
                },
                "warnings": warnings,
            }
            await self._save(state, update)
            self._log(state, stage, "completed", extraction_run_id=result.run_id)
            return update
        except Exception as exc:
            return await self._failure(state, stage, "extraction_failed", exc)

    async def analyze_addenda(
        self, state: BidIntelligenceState
    ) -> dict[str, Any]:
        stage = "analyze_addenda"
        await self._mark_running(state, stage)
        try:
            result = await self._deps.addendum.analyze(state["bid_id"])
            conflict_count = sum(
                finding.classification.value in {"conflict", "ambiguous"}
                for finding in result.findings
            )
            warnings = [*state["warnings"], *result.warnings]
            update = {
                "current_stage": stage,
                "stage_statuses": _stage(state, stage, "completed"),
                "addendum_analysis_run_id": result.run_id,
                "addendum_summary": {
                    "status": result.status.value,
                    "addendum_document_count": len(result.addendum_documents),
                    "finding_count": len(result.findings),
                    "conflict_or_ambiguity_count": conflict_count,
                    "relationship_uncertain": result.relationship_uncertain,
                    "ordering_uncertain": result.ordering_uncertain,
                },
                "warnings": warnings,
                "requires_human_review": (
                    state["requires_human_review"]
                    or conflict_count > 0
                    or result.relationship_uncertain
                    or result.ordering_uncertain
                ),
            }
            await self._save(state, update)
            self._log(
                state, stage, "completed", addendum_analysis_run_id=result.run_id
            )
            return update
        except NoAddendaAvailable:
            update = {
                "current_stage": stage,
                "stage_statuses": _stage(state, stage, "skipped"),
                "addendum_summary": {
                    "status": "not_applicable",
                    "reason": "no_addenda_present",
                },
                "warnings": [*state["warnings"], "no_addenda_present"],
            }
            await self._save(state, update)
            self._log(state, stage, "skipped")
            return update
        except Exception as exc:
            return await self._failure(
                state, stage, "addendum_analysis_failed", exc
            )

    async def validate_extraction(
        self, state: BidIntelligenceState
    ) -> dict[str, Any]:
        stage = "validate_extraction"
        await self._mark_running(state, stage)
        extraction_run_id = state["extraction_run_id"]
        if extraction_run_id is None:
            return await self._failure(
                state, stage, "missing_extraction_run", "Extraction run is unavailable"
            )
        try:
            result = await self._deps.validation.validate(
                state["bid_id"], extraction_run_id
            )
            summary = result.summary.model_dump(mode="json")
            review = (
                result.overall_status.value != "validated"
                or summary["conflicting"] > 0
                or summary["unsupported"] > 0
                or summary["needs_review"] > 0
            )
            update = {
                "current_stage": stage,
                "stage_statuses": _stage(state, stage, "completed"),
                "validation_run_id": result.validation_run_id,
                "addendum_analysis_run_id": (
                    result.addendum_analysis_run_id
                    or state["addendum_analysis_run_id"]
                ),
                "validation_summary": {
                    "status": result.status.value,
                    "overall_status": result.overall_status.value,
                    "addendum_check_status": result.addendum_check_status.value,
                    **summary,
                },
                "validation_findings": [
                    field.model_dump(mode="json")
                    for field in getattr(result, "fields", {}).values()
                ],
                "warnings": [*state["warnings"], *result.warnings],
                "requires_human_review": state["requires_human_review"] or review,
            }
            await self._save(state, update)
            self._log(
                state, stage, "completed", validation_run_id=result.validation_run_id
            )
            return update
        except Exception as exc:
            return await self._failure(state, stage, "validation_failed", exc)

    async def create_review_items(
        self, state: BidIntelligenceState
    ) -> dict[str, Any]:
        stage = "create_review_items"
        if self._deps.review_manager is None:
            update = {
                "current_stage": stage,
                "stage_statuses": _stage(state, stage, "completed"),
            }
            await self._save(state, update)
            return update
        try:
            review_ids = await self._deps.review_manager.create_items(state)
            update = {
                "current_stage": stage,
                "stage_statuses": _stage(state, stage, "completed"),
                "review_item_ids": review_ids,
                "review_status": "pending" if review_ids else "not_required",
                "workflow_status": "pending_review" if review_ids else "running",
                "requires_human_review": state["requires_human_review"]
                or bool(review_ids),
            }
            await self._save(state, update)
            if review_ids:
                logger.info(
                    "Human review created workflow_id=%s bid_id=%s review_ids=%s",
                    state["workflow_id"],
                    state["bid_id"],
                    [str(item) for item in review_ids],
                )
            return update
        except Exception as exc:
            return await self._failure(
                state, stage, "review_item_creation_failed", exc
            )

    async def human_review_gate(
        self, state: BidIntelligenceState
    ) -> dict[str, Any]:
        stage = "human_review_gate"
        if not state["review_item_ids"]:
            update = {
                "current_stage": stage,
                "stage_statuses": _stage(state, stage, "completed"),
            }
            await self._save(state, update)
            return update
        if (
            self._deps.review_manager is None
            or not self._deps.human_review_interrupt_enabled
        ):
            update = {
                "workflow_status": "running",
                "current_stage": stage,
                "stage_statuses": _stage(state, stage, "skipped"),
                "review_status": "pending",
            }
            await self._save(state, update)
            return update
        if not await self._deps.review_manager.all_resolved(state["workflow_id"]):
            logger.info(
                "Workflow suspended for human review workflow_id=%s bid_id=%s review_ids=%s",
                state["workflow_id"],
                state["bid_id"],
                [str(item) for item in state["review_item_ids"]],
            )
            interrupt(
                {
                    "workflow_id": str(state["workflow_id"]),
                    "bid_id": str(state["bid_id"]),
                    "review_item_ids": [
                        str(item) for item in state["review_item_ids"]
                    ],
                    "status": "pending_review",
                }
            )
        if not await self._deps.review_manager.all_resolved(state["workflow_id"]):
            interrupt(
                {
                    "workflow_id": str(state["workflow_id"]),
                    "status": "pending_review",
                }
            )
        update = {
            "workflow_status": "running",
            "current_stage": stage,
            "stage_statuses": _stage(state, stage, "completed"),
            "review_status": "resolved",
        }
        await self._save(state, update)
        return update

    async def requirements_agent(
        self, state: BidIntelligenceState
    ) -> dict[str, Any]:
        return await self._run_domain_agent(
            state,
            "requirements_agent",
            SpecializedAgentName.REQUIREMENTS,
            self._deps.requirements_agent,
            "requirements_findings",
        )

    async def product_specification_agent(
        self, state: BidIntelligenceState
    ) -> dict[str, Any]:
        return await self._run_domain_agent(
            state,
            "product_specification_agent",
            SpecializedAgentName.PRODUCT_SPECIFICATION,
            self._deps.product_agent,
            "product_findings",
        )

    async def commercial_terms_agent(
        self, state: BidIntelligenceState
    ) -> dict[str, Any]:
        return await self._run_domain_agent(
            state,
            "commercial_terms_agent",
            SpecializedAgentName.COMMERCIAL_TERMS,
            self._deps.commercial_agent,
            "commercial_findings",
        )

    async def compliance_documentation_agent(
        self, state: BidIntelligenceState
    ) -> dict[str, Any]:
        return await self._run_domain_agent(
            state,
            "compliance_documentation_agent",
            SpecializedAgentName.COMPLIANCE_DOCUMENTATION,
            self._deps.compliance_agent,
            "compliance_findings",
        )

    async def evidence_review_agent(
        self, state: BidIntelligenceState
    ) -> dict[str, Any]:
        stage = "evidence_review_agent"
        if not self._deps.specialized_agents_enabled:
            return await self._skip_agent(
                state, stage, SpecializedAgentName.EVIDENCE_REVIEW
            )
        await self._mark_running(state, stage)
        prior = [
            SpecializedFinding.model_validate(item)
            for key in (
                "requirements_findings",
                "product_findings",
                "commercial_findings",
                "compliance_findings",
            )
            for item in state[key]
        ]
        try:
            result = await self._deps.evidence_review_agent.execute(
                SpecializedAgentInput(
                    workflow_id=state["workflow_id"],
                    bid_id=state["bid_id"],
                    document_ids=state["document_ids"],
                    prior_findings=prior,
                )
            )
            if result.status == AgentExecutionStatus.FAILED:
                return await self._agent_failure(
                    state,
                    stage,
                    result.agent_name,
                    result.errors or ["Evidence review failed"],
                )
            statuses = {
                **state["agent_statuses"],
                result.agent_name.value: result.status.value,
            }
            issues = [item.model_dump(mode="json") for item in result.issues]
            update = {
                "current_stage": stage,
                "stage_statuses": _stage(state, stage, "completed"),
                "agent_statuses": statuses,
                "evidence_review_findings": issues,
                "agent_warnings": [*state["agent_warnings"], *result.warnings],
                "warnings": [*state["warnings"], *result.warnings],
                "requires_human_review": (
                    state["requires_human_review"] or bool(result.issues)
                ),
            }
            await self._save(state, update)
            self._log(state, stage, result.status.value)
            return update
        except Exception as exc:
            return await self._agent_failure(
                state,
                stage,
                SpecializedAgentName.EVIDENCE_REVIEW,
                [redact_text(str(exc)) or type(exc).__name__],
            )

    async def generate_profile(
        self, state: BidIntelligenceState
    ) -> dict[str, Any]:
        stage = "generate_profile"
        await self._mark_running(state, stage)
        try:
            result = await self._deps.profile.build(
                state["bid_id"], state["extraction_run_id"]
            )
            metadata = result.metadata
            if metadata.extraction_run_id != state["extraction_run_id"]:
                raise ValueError("Profile selected a different extraction run")
            if (
                state["validation_run_id"] is not None
                and metadata.validation_run_id != state["validation_run_id"]
            ):
                raise ValueError("Profile selected a different validation run")
            update = {
                "current_stage": stage,
                "stage_statuses": _stage(state, stage, "completed"),
                "profile_id": metadata.profile_id,
                "profile_status": metadata.status.value,
                "warnings": [*state["warnings"], *metadata.warnings],
                "requires_human_review": (
                    state["requires_human_review"] or metadata.review_required
                ),
            }
            await self._save(state, update)
            self._log(state, stage, "completed", profile_id=metadata.profile_id)
            return update
        except Exception as exc:
            return await self._failure(state, stage, "profile_generation_failed", exc)

    async def finalize(self, state: BidIntelligenceState) -> dict[str, Any]:
        failed = bool(state["errors"]) or any(
            status == "failed" for status in state["stage_statuses"].values()
        )
        if failed:
            status = "failed"
        elif state["requires_human_review"]:
            status = "needs_review"
        elif state["warnings"]:
            status = "completed_with_warnings"
        else:
            status = "completed"
        update = {
            "workflow_status": status,
            "current_stage": "finalize",
            "stage_statuses": _stage(state, "finalize", "completed"),
            "completed_at": datetime.now(UTC),
        }
        await self._save(state, update)
        self._log(state, "finalize", status)
        return update

    async def _mark_running(
        self, state: BidIntelligenceState, stage: str
    ) -> None:
        await self._save(
            state,
            {
                "current_stage": stage,
                "stage_statuses": _stage(state, stage, "running"),
            },
        )

    async def _run_domain_agent(
        self,
        state: BidIntelligenceState,
        stage: str,
        name: SpecializedAgentName,
        agent: DomainAgent,
        state_key: str,
    ) -> dict[str, Any]:
        if not self._deps.specialized_agents_enabled:
            return await self._skip_agent(state, stage, name)
        await self._mark_running(state, stage)
        try:
            result = await agent.execute(
                SpecializedAgentInput(
                    workflow_id=state["workflow_id"],
                    bid_id=state["bid_id"],
                    document_ids=state["document_ids"],
                )
            )
            if result.status == AgentExecutionStatus.FAILED:
                return await self._agent_failure(
                    state,
                    stage,
                    name,
                    result.errors or [f"{name.value} agent failed"],
                )
            findings = [
                item.model_dump(mode="json") for item in result.findings
            ]
            statuses = {
                **state["agent_statuses"],
                name.value: result.status.value,
            }
            review = any(item.requires_human_review for item in result.findings)
            update = {
                "current_stage": stage,
                "stage_statuses": _stage(state, stage, "completed"),
                "agent_statuses": statuses,
                state_key: findings,
                "agent_warnings": [*state["agent_warnings"], *result.warnings],
                "warnings": [*state["warnings"], *result.warnings],
                "requires_human_review": (
                    state["requires_human_review"]
                    or review
                    or result.status == AgentExecutionStatus.PARTIAL
                ),
            }
            await self._save(state, update)
            self._log(state, stage, result.status.value)
            return update
        except Exception as exc:
            return await self._agent_failure(
                state,
                stage,
                name,
                [redact_text(str(exc)) or type(exc).__name__],
            )

    async def _skip_agent(
        self,
        state: BidIntelligenceState,
        stage: str,
        name: SpecializedAgentName,
    ) -> dict[str, Any]:
        update = {
            "current_stage": stage,
            "stage_statuses": _stage(state, stage, "skipped"),
            "agent_statuses": {**state["agent_statuses"], name.value: "skipped"},
            "agent_warnings": [
                *state["agent_warnings"],
                "specialized_agents_disabled",
            ],
            "warnings": [*state["warnings"], "specialized_agents_disabled"],
        }
        await self._save(state, update)
        return update

    async def _agent_failure(
        self,
        state: BidIntelligenceState,
        stage: str,
        name: SpecializedAgentName,
        errors: list[str],
    ) -> dict[str, Any]:
        clean = [redact_text(item) for item in errors]
        update = {
            "workflow_status": "failed",
            "current_stage": stage,
            "stage_statuses": _stage(state, stage, "failed"),
            "agent_statuses": {**state["agent_statuses"], name.value: "failed"},
            "agent_errors": [*state["agent_errors"], *clean],
            "errors": [*state["errors"], *clean],
            "error_code": f"{name.value}_agent_failed",
            "requires_human_review": True,
        }
        await self._save(state, update)
        self._log(state, stage, "failed")
        return update

    async def _failure(
        self,
        state: BidIntelligenceState,
        stage: str,
        code: str,
        error: object,
        **extra: Any,
    ) -> dict[str, Any]:
        message = redact_text(str(error)) or type(error).__name__
        update = {
            "workflow_status": "failed",
            "current_stage": stage,
            "stage_statuses": _stage(state, stage, "failed"),
            "errors": [*state["errors"], message],
            "error_code": code,
            "requires_human_review": True,
            **extra,
        }
        await self._save(state, update)
        self._log(state, stage, "failed")
        return update

    async def _save(
        self, state: BidIntelligenceState, update: dict[str, Any]
    ) -> None:
        merged = cast(BidIntelligenceState, {**state, **update})
        await self._deps.workflow_store.save_state(merged)

    @staticmethod
    def _log(
        state: BidIntelligenceState, stage: str, outcome: str, **ids: object
    ) -> None:
        logger.info(
            "Bid workflow stage workflow_id=%s bid_id=%s stage=%s outcome=%s refs=%s",
            state["workflow_id"],
            state["bid_id"],
            stage,
            outcome,
            ids,
        )


def route_after_stage(state: BidIntelligenceState) -> str:
    """Every required-stage failure goes directly to deterministic finalization."""
    return "finalize" if state["workflow_status"] == "failed" else "continue"


def build_bid_intelligence_graph(
    dependencies: WorkflowDependencies, checkpointer: object | None = None
):
    """Build and compile the mandatory StateGraph."""
    nodes = BidIntelligenceGraph(dependencies)
    graph = StateGraph(BidIntelligenceState)
    observe = lambda name, node: instrument_node(
        name,
        node,
        dependencies.observer,
        operation_type="bid_workflow",
    )
    graph.add_node(
        "validate_bid_context",
        observe("validate_bid_context", nodes.validate_bid_context),
    )
    graph.add_node(
        "extract_rfp_fields",
        observe("extract_rfp_fields", nodes.extract_rfp_fields),
    )
    graph.add_node(
        "analyze_addenda",
        observe("analyze_addenda", nodes.analyze_addenda),
    )
    graph.add_node(
        "requirements_agent",
        observe("requirements_agent", nodes.requirements_agent),
    )
    graph.add_node(
        "product_specification_agent",
        observe("product_specification_agent", nodes.product_specification_agent),
    )
    graph.add_node(
        "commercial_terms_agent",
        observe("commercial_terms_agent", nodes.commercial_terms_agent),
    )
    graph.add_node(
        "compliance_documentation_agent",
        observe(
            "compliance_documentation_agent",
            nodes.compliance_documentation_agent,
        ),
    )
    graph.add_node(
        "evidence_review_agent",
        observe("evidence_review_agent", nodes.evidence_review_agent),
    )
    graph.add_node(
        "validate_extraction",
        observe("validate_extraction", nodes.validate_extraction),
    )
    graph.add_node(
        "create_review_items",
        observe("create_review_items", nodes.create_review_items),
    )
    graph.add_node(
        "human_review_gate",
        observe("human_review_gate", nodes.human_review_gate),
    )
    graph.add_node(
        "generate_profile",
        observe("generate_profile", nodes.generate_profile),
    )
    graph.add_node(
        "finalize",
        observe("finalize", nodes.finalize),
    )
    graph.add_edge(START, "validate_bid_context")
    graph.add_conditional_edges(
        "validate_bid_context",
        route_after_stage,
        {"continue": "extract_rfp_fields", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "extract_rfp_fields",
        route_after_stage,
        {"continue": "analyze_addenda", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "analyze_addenda",
        route_after_stage,
        {"continue": "requirements_agent", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "requirements_agent",
        route_after_stage,
        {"continue": "product_specification_agent", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "product_specification_agent",
        route_after_stage,
        {"continue": "commercial_terms_agent", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "commercial_terms_agent",
        route_after_stage,
        {"continue": "compliance_documentation_agent", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "compliance_documentation_agent",
        route_after_stage,
        {"continue": "evidence_review_agent", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "evidence_review_agent",
        route_after_stage,
        {"continue": "validate_extraction", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "validate_extraction",
        route_after_stage,
        {"continue": "create_review_items", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "create_review_items",
        route_after_stage,
        {"continue": "human_review_gate", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "human_review_gate",
        route_after_stage,
        {"continue": "generate_profile", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "generate_profile",
        route_after_stage,
        {"continue": "finalize", "finalize": "finalize"},
    )
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)


def _stage(
    state: BidIntelligenceState, name: str, status: str
) -> dict[str, str]:
    return {**state["stage_statuses"], name: status}
