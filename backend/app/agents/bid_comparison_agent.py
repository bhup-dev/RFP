"""LangGraph-based comparison of persisted bid intelligence profiles."""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.comparison_state import BidComparisonState
from app.core.redaction import redact_text
from app.core.prompt_security import UNTRUSTED_DOCUMENT_POLICY
from app.schemas.comparison import (
    BidComparisonReport,
    BidComparisonRequest,
    ComparisonDimension,
    ComparisonProfileReference,
    ComparisonRow,
    ComparisonStatus,
    ComparisonSummary,
    DifferenceClassification,
    LLMSemanticComparisonOutput,
    PerBidComparisonValue,
)
from app.schemas.intelligence import BidIntelligenceProfile
from app.schemas.extraction import ASSIGNMENT_FIELD_NAMES
from app.services.specialized_agent_store import CitationCheck
from app.observability.instrumentation import NodeObserver, instrument_node


class ProfileLoader(Protocol):
    async def build(
        self, bid_id: UUID, extraction_run_id: UUID | None = None
    ) -> BidIntelligenceProfile: ...


class ComparisonPersistence(Protocol):
    async def existing_bid_ids(self, bid_ids: list[UUID]) -> set[UUID]: ...

    async def save_profile_references(
        self,
        comparison_id: UUID,
        references: list[ComparisonProfileReference],
    ) -> None: ...

    async def save_report(self, report: BidComparisonReport) -> None: ...


class ComparisonEvidence(Protocol):
    async def validate_citations(
        self, bid_id: UUID, citations: list
    ) -> list[CitationCheck]: ...


class ComparisonLLM(Protocol):
    async def generate_response(self, prompt: str, **kwargs: object) -> str: ...


class ComparisonHumanReview(Protocol):
    async def create_items(self, state: BidComparisonState) -> list[UUID]: ...

    async def all_resolved(self, comparison_id: UUID) -> bool: ...


class ComparisonDependencies:
    def __init__(
        self,
        profiles: ProfileLoader,
        persistence: ComparisonPersistence,
        evidence: ComparisonEvidence,
        llm: ComparisonLLM,
        *,
        max_bids: int,
        semantic_timeout_seconds: float,
        max_semantic_rows: int,
        max_output_tokens: int,
        observer: NodeObserver | None = None,
        review_manager: ComparisonHumanReview | None = None,
        human_review_interrupt_enabled: bool = False,
    ) -> None:
        self.profiles = profiles
        self.persistence = persistence
        self.evidence = evidence
        self.llm = llm
        self.max_bids = max_bids
        self.semantic_timeout_seconds = semantic_timeout_seconds
        self.max_semantic_rows = max_semantic_rows
        self.max_output_tokens = max_output_tokens
        self.observer = observer
        self.review_manager = review_manager
        self.human_review_interrupt_enabled = human_review_interrupt_enabled


FIELD_DIMENSIONS: dict[ComparisonDimension, tuple[str, ...]] = {
    ComparisonDimension.SCOPE: ("Bid Number", "Title", "Bid Summary"),
    ComparisonDimension.PRODUCTS: (
        "Product",
        "Model_no",
        "Part_no",
        "Product Specification",
        "Installation",
    ),
    ComparisonDimension.COMMERCIAL_TERMS: (
        "Due Date",
        "Pre Bid Meeting",
        "Term of Bid",
        "Delivery Date",
        "Payment Terms",
        "Bid Bond Requirement",
        "Contract or Cooperative to use",
    ),
    ComparisonDimension.COMPLIANCE: (
        "Bid Submission Type",
        "Additional Documentation Required",
        "MFG for Registration",
    ),
    ComparisonDimension.ADDENDA: (),
}

SEMANTIC_ATTRIBUTES = {
    "Title",
    "Bid Summary",
    "Product Specification",
    "Installation",
    "Payment Terms",
    "Additional Documentation Required",
}
EXACT_IDENTIFIERS = {"Bid Number", "Model_no", "Part_no"}
DEADLINE_ATTRIBUTES = {"Due Date", "Delivery Date", "Pre Bid Meeting"}


class BidComparisonGraph:
    def __init__(self, dependencies: ComparisonDependencies) -> None:
        self._deps = dependencies

    async def validate_comparison_request(
        self, state: BidComparisonState
    ) -> dict[str, Any]:
        request = BidComparisonRequest.model_validate(state["request"])
        if len(request.bid_ids) > self._deps.max_bids:
            return _failure(
                state,
                "validate_comparison_request",
                "too_many_bids",
                f"At most {self._deps.max_bids} bids may be compared",
            )
        existing = await self._deps.persistence.existing_bid_ids(request.bid_ids)
        missing = [item for item in request.bid_ids if item not in existing]
        if missing:
            return _failure(
                state,
                "validate_comparison_request",
                "bid_not_found",
                f"{len(missing)} requested bid(s) do not exist",
            )
        return _success(state, "validate_comparison_request")

    async def load_profiles(self, state: BidComparisonState) -> dict[str, Any]:
        request = BidComparisonRequest.model_validate(state["request"])
        profiles: list[BidIntelligenceProfile] = []
        references: list[ComparisonProfileReference] = []
        warnings = list(state["warnings"])
        try:
            for bid_id in request.bid_ids:
                profile = await self._deps.profiles.build(
                    bid_id, request.extraction_run_ids.get(bid_id)
                )
                unusable = (
                    profile.metadata.extraction_run_id is None
                    or profile.metadata.validation_run_id is None
                    or profile.metadata.status.value in {"failed", "incomplete"}
                )
                if unusable and not request.allow_incomplete_profiles:
                    return _failure(
                        state,
                        "load_profiles",
                        "profile_unavailable",
                        f"Bid {bid_id} has no complete validated profile",
                    )
                if unusable:
                    warnings.append(f"Bid {bid_id} has an incomplete profile")
                profiles.append(profile)
                references.append(_reference(profile))
            await self._deps.persistence.save_profile_references(
                state["comparison_id"], references
            )
        except Exception as exc:
            return _failure(
                state,
                "load_profiles",
                "profile_load_failed",
                redact_text(str(exc)) or type(exc).__name__,
            )
        return {
            **_success(state, "load_profiles"),
            "profiles": [item.model_dump(mode="json") for item in profiles],
            "profile_references": [
                item.model_dump(mode="json") for item in references
            ],
            "warnings": warnings,
            "requires_human_review": state["requires_human_review"]
            or bool(warnings),
        }

    async def compare_structured_fields(
        self, state: BidComparisonState
    ) -> dict[str, Any]:
        request = BidComparisonRequest.model_validate(state["request"])
        profiles = [
            BidIntelligenceProfile.model_validate(item) for item in state["profiles"]
        ]
        rows: list[ComparisonRow] = []
        semantic: list[str] = []
        for dimension in request.dimensions:
            attributes = FIELD_DIMENSIONS[dimension]
            if dimension == ComparisonDimension.ADDENDA:
                attributes = tuple(
                    name
                    for name in ASSIGNMENT_FIELD_NAMES
                    if any(profile.fields[name].addendum_changes for profile in profiles)
                )
            for attribute in attributes:
                row = _compare_field(dimension, attribute, profiles)
                rows.append(row)
                if (
                    row.classification == DifferenceClassification.UNCERTAIN
                    and attribute in SEMANTIC_ATTRIBUTES
                ):
                    semantic.append(attribute)
        return {
            **_success(state, "compare_structured_fields"),
            "comparison_rows": [item.model_dump(mode="json") for item in rows],
            "semantic_attributes": semantic[: self._deps.max_semantic_rows],
            "requires_human_review": state["requires_human_review"]
            or any(item.requires_human_review for item in rows),
        }

    async def compare_semantic_requirements(
        self, state: BidComparisonState
    ) -> dict[str, Any]:
        stage = "compare_semantic_requirements"
        if not state["semantic_attributes"]:
            return {
                **_success(state, stage, status="skipped"),
                "warnings": state["warnings"],
            }
        rows = [ComparisonRow.model_validate(item) for item in state["comparison_rows"]]
        candidates = [
            item for item in rows if item.attribute in state["semantic_attributes"]
        ]
        try:
            async with asyncio.timeout(self._deps.semantic_timeout_seconds):
                raw = await self._deps.llm.generate_response(
                    _semantic_prompt(candidates),
                    system=(
                        UNTRUSTED_DOCUMENT_POLICY
                        + "Compare only the supplied profile values. Do not invent values, "
                        "citations, equivalence, or requirements. Missing and conflicting "
                        "evidence cannot be overridden."
                    ),
                    temperature=0,
                    max_output_tokens=self._deps.max_output_tokens,
                )
            payload = LLMSemanticComparisonOutput.model_validate(
                json.loads(_strip_json_fence(raw))
            )
            allowed = {item.attribute for item in candidates}
            if any(item.attribute not in allowed for item in payload.comparisons):
                raise ValueError("semantic output referenced an unsupplied attribute")
            updates = {item.attribute: item for item in payload.comparisons}
            for index, row in enumerate(rows):
                semantic = updates.get(row.attribute)
                if semantic is None:
                    continue
                rows[index] = row.model_copy(
                    update={
                        "classification": semantic.classification,
                        "explanation": semantic.explanation,
                        "confidence": semantic.confidence,
                        "requires_human_review": (
                            semantic.classification
                            in {
                                DifferenceClassification.NOT_COMPARABLE,
                                DifferenceClassification.UNCERTAIN,
                            }
                        ),
                    }
                )
            warnings = [*state["warnings"], *payload.warnings]
        except Exception:
            warnings = [
                *state["warnings"],
                "Semantic comparison unavailable; deterministic results were preserved",
            ]
        return {
            **_success(state, stage),
            "comparison_rows": [item.model_dump(mode="json") for item in rows],
            "warnings": warnings,
            "requires_human_review": state["requires_human_review"]
            or any(item.requires_human_review for item in rows),
        }

    async def validate_comparison_evidence(
        self, state: BidComparisonState
    ) -> dict[str, Any]:
        stage = "validate_comparison_evidence"
        rows = [ComparisonRow.model_validate(item) for item in state["comparison_rows"]]
        warnings = list(state["warnings"])
        review = state["requires_human_review"]
        try:
            for row_index, row in enumerate(rows):
                values = []
                invalid_row = False
                for value in row.values:
                    citations = [
                        *value.supporting_citations,
                        *value.contradicting_citations,
                    ]
                    checks = await self._deps.evidence.validate_citations(
                        value.bid_id, citations
                    )
                    valid_ids = {
                        check.citation.chunk_id for check in checks if check.valid
                    }
                    invalid = [check for check in checks if not check.valid]
                    if invalid:
                        invalid_row = review = True
                        warnings.append(
                            f"{row.attribute}: removed {len(invalid)} invalid citation(s) "
                            f"for bid {value.bid_id}"
                        )
                    supporting = [
                        item
                        for item in value.supporting_citations
                        if item.chunk_id in valid_ids
                    ]
                    contradicting = [
                        item
                        for item in value.contradicting_citations
                        if item.chunk_id in valid_ids
                    ]
                    if (
                        value.original_value is not None
                        and value.field_status not in {"not_found", "unvalidated"}
                        and not supporting
                    ):
                        invalid_row = review = True
                        warnings.append(
                            f"{row.attribute}: no validated supporting citation for "
                            f"bid {value.bid_id}"
                        )
                    values.append(
                        value.model_copy(
                            update={
                                "supporting_citations": supporting,
                                "contradicting_citations": contradicting,
                            }
                        )
                    )
                if (
                    invalid_row
                    and row.classification != DifferenceClassification.CONFLICTING
                ):
                    row = row.model_copy(
                        update={
                            "classification": DifferenceClassification.UNCERTAIN,
                            "requires_human_review": True,
                            "review_recommendation": "Review source evidence before relying on this comparison.",
                        }
                    )
                rows[row_index] = row.model_copy(update={"values": values})
        except Exception as exc:
            return _failure(
                state,
                stage,
                "evidence_validation_failed",
                redact_text(str(exc)) or type(exc).__name__,
            )
        return {
            **_success(state, stage),
            "comparison_rows": [item.model_dump(mode="json") for item in rows],
            "warnings": list(dict.fromkeys(warnings)),
            "requires_human_review": review,
        }

    async def create_comparison_review_items(
        self, state: BidComparisonState
    ) -> dict[str, Any]:
        stage = "create_comparison_review_items"
        if self._deps.review_manager is None:
            return _success(state, stage)
        try:
            item_ids = await self._deps.review_manager.create_items(state)
        except Exception as exc:
            return _failure(
                state,
                stage,
                "review_item_creation_failed",
                redact_text(str(exc)) or type(exc).__name__,
            )
        return {
            **_success(state, stage),
            "status": "pending_review" if item_ids else state["status"],
            "review_item_ids": item_ids,
            "review_status": "pending" if item_ids else "not_required",
            "requires_human_review": state["requires_human_review"]
            or bool(item_ids),
        }

    async def comparison_human_review_gate(
        self, state: BidComparisonState
    ) -> dict[str, Any]:
        stage = "comparison_human_review_gate"
        if not state["review_item_ids"]:
            return _success(state, stage)
        if (
            self._deps.review_manager is None
            or not self._deps.human_review_interrupt_enabled
        ):
            return {
                **_success(state, stage),
                "status": "processing",
                "review_status": "pending",
            }
        if not await self._deps.review_manager.all_resolved(
            state["comparison_id"]
        ):
            interrupt(
                {
                    "comparison_id": str(state["comparison_id"]),
                    "review_item_ids": [
                        str(item) for item in state["review_item_ids"]
                    ],
                    "status": "pending_review",
                }
            )
        if not await self._deps.review_manager.all_resolved(
            state["comparison_id"]
        ):
            interrupt(
                {
                    "comparison_id": str(state["comparison_id"]),
                    "status": "pending_review",
                }
            )
        return {
            **_success(state, stage),
            "status": "processing",
            "review_status": "resolved",
        }

    async def finalize_comparison(
        self, state: BidComparisonState
    ) -> dict[str, Any]:
        request = BidComparisonRequest.model_validate(state["request"])
        rows = [ComparisonRow.model_validate(item) for item in state["comparison_rows"]]
        references = [
            ComparisonProfileReference.model_validate(item)
            for item in state["profile_references"]
        ]
        failed = bool(state["errors"])
        incomplete = any(
            item.profile_status in {"failed", "incomplete"}
            or item.extraction_run_id is None
            or item.validation_run_id is None
            for item in references
        )
        if failed:
            status = ComparisonStatus.FAILED
        elif incomplete:
            status = ComparisonStatus.INCOMPLETE
        elif state["warnings"] or state["requires_human_review"]:
            status = ComparisonStatus.COMPLETED_WITH_WARNINGS
        else:
            status = ComparisonStatus.COMPLETED
        completed = datetime.now(UTC)
        report = _report(state, request, references, rows, status, completed)
        await self._deps.persistence.save_report(report)
        return {
            "status": status.value,
            "current_stage": "finalize_comparison",
            "stage_statuses": _stage(state, "finalize_comparison", "completed"),
            "completed_at": completed,
        }


def build_bid_comparison_graph(
    dependencies: ComparisonDependencies, checkpointer: object | None = None
):
    nodes = BidComparisonGraph(dependencies)
    graph = StateGraph(BidComparisonState)
    observe = lambda name, node: instrument_node(
        name,
        node,
        dependencies.observer,
        operation_type="bid_comparison",
    )
    graph.add_node(
        "validate_comparison_request",
        observe("validate_comparison_request", nodes.validate_comparison_request),
    )
    graph.add_node("load_profiles", observe("load_profiles", nodes.load_profiles))
    graph.add_node(
        "compare_structured_fields",
        observe("compare_structured_fields", nodes.compare_structured_fields),
    )
    graph.add_node(
        "compare_semantic_requirements",
        observe(
            "compare_semantic_requirements",
            nodes.compare_semantic_requirements,
        ),
    )
    graph.add_node(
        "validate_comparison_evidence",
        observe(
            "validate_comparison_evidence",
            nodes.validate_comparison_evidence,
        ),
    )
    graph.add_node(
        "finalize_comparison",
        observe("finalize_comparison", nodes.finalize_comparison),
    )
    graph.add_node(
        "create_comparison_review_items",
        observe(
            "create_comparison_review_items",
            nodes.create_comparison_review_items,
        ),
    )
    graph.add_node(
        "comparison_human_review_gate",
        observe(
            "comparison_human_review_gate",
            nodes.comparison_human_review_gate,
        ),
    )
    graph.add_edge(START, "validate_comparison_request")
    for source, target in (
        ("validate_comparison_request", "load_profiles"),
        ("load_profiles", "compare_structured_fields"),
        ("compare_structured_fields", "compare_semantic_requirements"),
        ("compare_semantic_requirements", "validate_comparison_evidence"),
        ("validate_comparison_evidence", "create_comparison_review_items"),
        ("create_comparison_review_items", "comparison_human_review_gate"),
        ("comparison_human_review_gate", "finalize_comparison"),
    ):
        graph.add_conditional_edges(
            source,
            _route,
            {"continue": target, "finalize": "finalize_comparison"},
        )
    graph.add_edge("finalize_comparison", END)
    return graph.compile(checkpointer=checkpointer)


def report_from_state(state: BidComparisonState) -> BidComparisonReport:
    request = BidComparisonRequest.model_validate(state["request"])
    references = [
        ComparisonProfileReference.model_validate(item)
        for item in state["profile_references"]
    ]
    rows = [ComparisonRow.model_validate(item) for item in state["comparison_rows"]]
    return _report(
        state,
        request,
        references,
        rows,
        ComparisonStatus(state["status"]),
        state["completed_at"] or datetime.now(UTC),
    )


def _compare_field(
    dimension: ComparisonDimension,
    attribute: str,
    profiles: list[BidIntelligenceProfile],
) -> ComparisonRow:
    values: list[PerBidComparisonValue] = []
    for profile in profiles:
        field = profile.fields[attribute]
        values.append(
            PerBidComparisonValue(
                bid_id=profile.metadata.bid_id,
                original_value=field.value,
                normalized_value=_normalize(attribute, field.value),
                field_status=field.validation_status.value,
                supporting_citations=field.supporting_citations,
                contradicting_citations=field.contradicting_citations,
                addendum_changes=[
                    item.model_dump(mode="json") for item in field.addendum_changes
                ],
            )
        )
    classification, explanation = _classify(attribute, values)
    review = classification in {
        DifferenceClassification.MISSING,
        DifferenceClassification.CONFLICTING,
        DifferenceClassification.NOT_COMPARABLE,
        DifferenceClassification.UNCERTAIN,
    }
    return ComparisonRow(
        dimension=dimension,
        attribute=attribute,
        values=values,
        classification=classification,
        explanation=explanation,
        confidence=1.0
        if classification
        in {
            DifferenceClassification.EQUIVALENT,
            DifferenceClassification.DIFFERENT,
        }
        else None,
        requires_human_review=review,
        review_recommendation=(
            "No additional review indicated by deterministic comparison."
            if not review
            else "Review missing, conflicting, or semantically uncertain evidence."
        ),
    )


def _classify(
    attribute: str, values: list[PerBidComparisonValue]
) -> tuple[DifferenceClassification, str]:
    if any(
        item.field_status == "conflicting"
        or any(
            change.get("classification") in {"conflict", "ambiguous"}
            for change in item.addendum_changes
        )
        for item in values
    ):
        return (
            DifferenceClassification.CONFLICTING,
            "At least one bid contains unresolved contradictory evidence.",
        )
    if any(item.original_value is None for item in values):
        return (
            DifferenceClassification.MISSING,
            "A usable value is missing for at least one bid; absence is not a difference.",
        )
    normalized = [item.normalized_value for item in values]
    if all(item == normalized[0] for item in normalized[1:]):
        return (
            DifferenceClassification.EQUIVALENT,
            "The normalized profile values are equivalent.",
        )
    if attribute == "Product":
        identities = [_product_identity(item.original_value) for item in values]
        if identities[0] is not None and all(
            item == identities[0] for item in identities[1:]
        ):
            return (
                DifferenceClassification.DIFFERENT,
                "The same product has different structured quantities, units, or attributes.",
            )
        return (
            DifferenceClassification.NOT_COMPARABLE,
            "The profiles identify different products and equivalence is not established.",
        )
    if attribute in SEMANTIC_ATTRIBUTES:
        return (
            DifferenceClassification.UNCERTAIN,
            "Narrative values differ and require bounded semantic comparison.",
        )
    if attribute in DEADLINE_ATTRIBUTES and any(
        not isinstance(item, dict) or not item.get("timezone_aware")
        for item in normalized
    ):
        return (
            DifferenceClassification.UNCERTAIN,
            "Deadline values differ and cannot be safely normalized with timezone context.",
        )
    return (
        DifferenceClassification.DIFFERENT,
        "The structured profile values are materially different.",
    )


def _normalize(attribute: str, value: Any) -> Any:
    if value is None:
        return None
    if attribute in EXACT_IDENTIFIERS:
        return str(value).strip()
    if attribute in DEADLINE_ATTRIBUTES and isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return {"original": " ".join(value.split()), "timezone_aware": False}
        return {
            "iso": parsed.isoformat(),
            "timezone_aware": parsed.tzinfo is not None,
        }
    if isinstance(value, str):
        return " ".join(value.split()).casefold()
    if isinstance(value, list):
        return sorted(
            (_normalize(attribute, item) for item in value),
            key=lambda item: json.dumps(item, sort_keys=True),
        )
    if isinstance(value, dict):
        return {
            key: _normalize(attribute, item)
            for key, item in sorted(value.items())
        }
    return value


def _product_identity(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("product_name", "name", "product"):
            candidate = value.get(key)
            if candidate is not None and str(candidate).strip():
                return " ".join(str(candidate).split()).casefold()
    return None


def _semantic_prompt(rows: list[ComparisonRow]) -> str:
    payload = [
        {
            "attribute": row.attribute,
            "values": [
                {"bid_id": str(value.bid_id), "value": value.original_value}
                for value in row.values
            ],
        }
        for row in rows
    ]
    return (
        UNTRUSTED_DOCUMENT_POLICY
        + "Return JSON only with shape "
        '{"comparisons":[{"attribute":"exact supplied attribute",'
        '"classification":"equivalent|different|not_comparable|uncertain",'
        '"explanation":"...","confidence":0.0}],"warnings":[]}.\n'
        "Compare material meaning only. Do not infer missing facts or product equivalence.\n"
        f"Rows:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _reference(profile: BidIntelligenceProfile) -> ComparisonProfileReference:
    metadata = profile.metadata
    return ComparisonProfileReference(
        bid_id=metadata.bid_id,
        bid_identifier=metadata.bid_identifier,
        profile_id=metadata.profile_id,
        extraction_run_id=metadata.extraction_run_id,
        addendum_analysis_run_id=metadata.addendum_analysis_run_id,
        validation_run_id=metadata.validation_run_id,
        profile_status=metadata.status.value,
    )


def _report(
    state: BidComparisonState,
    request: BidComparisonRequest,
    references: list[ComparisonProfileReference],
    rows: list[ComparisonRow],
    status: ComparisonStatus,
    created_at: datetime,
) -> BidComparisonReport:
    counts = {item: 0 for item in DifferenceClassification}
    for row in rows:
        counts[row.classification] += 1
    return BidComparisonReport(
        comparison_id=state["comparison_id"],
        status=status,
        bid_ids=request.bid_ids,
        profile_references=references,
        comparison_dimensions=request.dimensions,
        summary=ComparisonSummary(
            total_rows=len(rows),
            equivalent=counts[DifferenceClassification.EQUIVALENT],
            different=counts[DifferenceClassification.DIFFERENT],
            missing=counts[DifferenceClassification.MISSING],
            conflicting=counts[DifferenceClassification.CONFLICTING],
            not_comparable=counts[DifferenceClassification.NOT_COMPARABLE],
            uncertain=counts[DifferenceClassification.UNCERTAIN],
        ),
        comparison_rows=rows,
        key_similarities=[
            item.attribute
            for item in rows
            if item.classification == DifferenceClassification.EQUIVALENT
        ][:10],
        key_differences=[
            item.attribute
            for item in rows
            if item.classification == DifferenceClassification.DIFFERENT
        ][:10],
        missing_information=[
            item.attribute
            for item in rows
            if item.classification == DifferenceClassification.MISSING
        ],
        unresolved_conflicts=[
            item.attribute
            for item in rows
            if item.classification == DifferenceClassification.CONFLICTING
        ],
        warnings=state["warnings"],
        errors=state["errors"],
        requires_human_review=state["requires_human_review"],
        created_at=created_at,
    )


def _route(state: BidComparisonState) -> str:
    return "finalize" if state["status"] == "failed" else "continue"


def _success(
    state: BidComparisonState, stage: str, status: str = "completed"
) -> dict[str, Any]:
    return {
        "current_stage": stage,
        "stage_statuses": _stage(state, stage, status),
    }


def _failure(
    state: BidComparisonState, stage: str, code: str, message: str
) -> dict[str, Any]:
    return {
        "status": "failed",
        "current_stage": stage,
        "stage_statuses": _stage(state, stage, "failed"),
        "errors": [*state["errors"], redact_text(message)],
        "error_code": code,
        "requires_human_review": True,
    }


def _stage(
    state: BidComparisonState, stage: str, status: str
) -> dict[str, str]:
    return {**state["stage_statuses"], stage: status}


def _strip_json_fence(value: str) -> str:
    cleaned = value.strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        cleaned = cleaned[3:-3].strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].lstrip()
    return cleaned
