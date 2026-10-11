"""Measure Recall@k, MRR, Hit@k and nDCG@k for each retrieval configuration.

Usage, from the backend directory:
    python -m scripts.retrieval_eval [--dataset evaluation/retrieval_queries.json] \
        [--output-dir ../deliverables/retrieval_evaluation]

Every configuration runs the labelled queries through the real retrieval code against the live
PostgreSQL and Qdrant data. Single-backend runs replace the other backend with one that is
switched off, so ranking goes through the service's own degraded path. Reranking is reported
as not run when its runtime is not installed; no number is filled in for it. A hand-written
``findings.md`` in the output directory, if present, is appended to ``report.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from app.config import settings
from app.db.session import get_session_factory
from app.retrieval.base import BackendResult, RetrievalBackendError
from app.retrieval.evaluation import EvaluationRun, compare_runs, load_dataset
from app.retrieval.keyword_search import KeywordSearch
from app.retrieval.query_rewriter import QueryRewriter
from app.retrieval.query_service import PreparedQuery
from app.retrieval.retrieval_service import RetrievalService
from app.retrieval.semantic_search import SemanticSearch
from app.schemas.retrieval import RetrievalRequest
from app.services.bid_qa_context import expand_query
from app.services.embedding_service import get_embedding_service
from app.services.llm_service import get_llm_service
from app.services.qdrant_service import get_qdrant_service

K_VALUES = (1, 3, 5, 10)
RETRIEVE_TOP_K = max(K_VALUES)
METRICS = ("recall_at_k", "mrr", "hit_rate_at_k", "ndcg_at_k", "precision_at_k")


class SwitchedOff:
    """A backend that is deliberately unavailable, to measure the other backend alone."""

    def __init__(self, name: str) -> None:
        self._name = name

    async def search(self, query: PreparedQuery) -> BackendResult:
        raise RetrievalBackendError(self._name, f"{self._name} switched off for evaluation")


CONFIGURATIONS = {
    "keyword_only": {"semantic": False, "keyword": True, "rewriting": False, "expansion": False},
    "semantic_only": {"semantic": True, "keyword": False, "rewriting": False, "expansion": False},
    "hybrid_rrf": {"semantic": True, "keyword": True, "rewriting": False, "expansion": False},
    "hybrid_rrf_rewriting": {"semantic": True, "keyword": True, "rewriting": True, "expansion": False},
    "hybrid_rrf_expansion": {"semantic": True, "keyword": True, "rewriting": False, "expansion": True},
}
NOT_RUN = {
    "hybrid_rrf_reranking": "sentence_transformers (cross-encoder runtime) is not installed",
    "hybrid_rrf_rewriting_reranking": "sentence_transformers (cross-encoder runtime) is not installed",
}


def build_service(session, options: dict) -> RetrievalService:
    semantic = (
        SemanticSearch(get_embedding_service(), get_qdrant_service(), settings.embedding_dimension)
        if options["semantic"]
        else SwitchedOff("semantic")
    )
    keyword = KeywordSearch(session) if options["keyword"] else SwitchedOff("keyword")
    rewriter = (
        QueryRewriter(
            get_llm_service(),
            enabled=True,
            max_variants=settings.query_rewrite_max_variants,
            timeout_seconds=settings.query_rewrite_timeout_seconds,
        )
        if options["rewriting"]
        else None
    )
    return RetrievalService(
        semantic,
        keyword,
        rrf_constant=settings.search_rrf_constant,
        allow_degraded=True,
        rewriter=rewriter,
    )


async def run_configuration(name: str, options: dict, queries, bid_ids: dict[str, UUID]) -> EvaluationRun:
    retrieved, latencies, counts, warnings = {}, {}, {}, {}
    async with get_session_factory()() as session:
        service = build_service(session, options)
        for query in queries:
            text = expand_query(query.query) if options["expansion"] else query.query
            started = time.perf_counter()
            response = await service.search(
                RetrievalRequest(query=text, bid_id=bid_ids[query.query_id], top_k=RETRIEVE_TOP_K)
            )
            latencies[query.query_id] = (time.perf_counter() - started) * 1000
            retrieved[query.query_id] = response.results
            counts[query.query_id] = len(response.results)
            relevant = [
                item
                for item in response.warnings
                if "returned results from the available backend" not in item
            ]
            if relevant:
                warnings[query.query_id] = relevant
    print(f"{name}: {len(retrieved)} queries")
    return EvaluationRun(
        name=name,
        retrieved=retrieved,
        latencies_ms=latencies,
        candidate_counts=counts,
        configuration={**options, "warnings": warnings},
    )


def per_query_ranks(queries, runs: list[EvaluationRun]) -> dict:
    """First relevant rank (1-based) per query and configuration, or None when not in the top 10."""
    table = {}
    for query in queries:
        ids = {str(item.chunk_id) for item in query.relevant}
        table[query.query_id] = {
            run.name: next(
                (rank for rank, chunk in enumerate(run.retrieved[query.query_id], 1) if str(chunk.chunk_id) in ids),
                None,
            )
            for run in runs
        }
    return table


def markdown(results: dict, queries, dataset_meta: dict, findings: str | None) -> str:
    names = [run["configuration"]["name"] for run in results["by_k"]["10"]["runs"]]
    lines = [
        "# Retrieval evaluation report",
        "",
        (f"Generated {results['generated_at']} by `backend/scripts/retrieval_eval.py` against the live "
        "PostgreSQL and Qdrant data. Full numbers: `results.json`. Question set and labels: `questions.json`."),
        "",
        "## Setup",
        "",
        (f"- **Questions:** {len(queries)} ({sum(1 for q in queries if q.bid_identifier == 'Bid1')} for Bid1, "
        f"{sum(1 for q in queries if q.bid_identifier == 'Bid2')} for Bid2), each scoped to its own bid."),
        "- **Bids:** " + "; ".join(f"{name} = {info['title']}" for name, info in dataset_meta["bids"].items()) + ".",
        (f"- **Ground truth:** {dataset_meta['labelled_by']}. Every relevant chunk carries the quoted evidence "
        "that answers the question (grade 2 = states the answer, grade 1 = related context)."),
        f"- **Retrieval:** top {RETRIEVE_TOP_K} results per query; metrics at k = {', '.join(map(str, K_VALUES))}.",
        (f"- **Index:** `{settings.embedding_model}` embeddings ({settings.embedding_dimension} dimensions) in Qdrant; "
        f"PostgreSQL full-text keyword search; RRF constant {settings.search_rrf_constant}; chunk size "
        f"{settings.chunk_size} characters with {settings.chunk_overlap} overlap."),
        ("- **Recall@k** counts every labelled chunk, including the text and table copies of the same page, "
        "so it is stricter than Hit@k (at least one relevant chunk in the top k)."),
        "",
        "## Configurations",
        "",
        "| Configuration | Semantic | Keyword | LLM rewriting | Synonym expansion |",
        "|---|---|---|---|---|",
    ]
    for run in results["by_k"]["10"]["runs"]:
        c = run["configuration"]
        lines.append(
            f"| {c['name']} | {'yes' if c['semantic'] else 'no'} | {'yes' if c['keyword'] else 'no'} | "
            f"{'yes' if c['rewriting'] else 'no'} | {'yes' if c['expansion'] else 'no'} |"
        )
    for name, reason in results["not_run"].items():
        lines.append(f"| {name} | not run: {reason} | | | |")
    for k in K_VALUES:
        lines += ["", f"## Results at k = {k}", "", "| Configuration | Recall@k | MRR | Hit@k | nDCG@k | Precision@k | Mean latency (ms) |", "|---|---|---|---|---|---|---|"]
        for run in results["by_k"][str(k)]["runs"]:
            m = run["metrics"]
            lines.append(
                f"| {run['configuration']['name']} | {m['recall_at_k']:.3f} | {m['mrr']:.3f} | "
                f"{m['hit_rate_at_k']:.3f} | {m['ndcg_at_k']:.3f} | {m['precision_at_k']:.3f} | "
                f"{run['measurements']['mean_latency_ms']:.0f} |"
            )
    lines += ["", "## First relevant rank per question (top 10; - means not found)", "", "| Question | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for query in queries:
        ranks = results["first_relevant_rank"][query.query_id]
        cells = " | ".join("-" if ranks[name] is None else str(ranks[name]) for name in names)
        lines.append(f"| {query.query_id}: {query.query} | {cells} |")
    lines.append("")
    if findings:
        lines += [findings.strip(), ""]
    return "\n".join(lines)


def write_report(output_dir: Path, results: dict, queries, body: dict) -> None:
    findings_path = output_dir / "findings.md"
    findings = findings_path.read_text() if findings_path.exists() else None
    (output_dir / "report.md").write_text(markdown(results, queries, body, findings))


async def main_async(dataset: Path, output_dir: Path, report_only: bool) -> None:
    body = json.loads(dataset.read_text())
    queries = load_dataset(dataset)
    if report_only:
        write_report(output_dir, json.loads((output_dir / "results.json").read_text()), queries, body)
        return
    bid_ids = {row["query_id"]: UUID(row["bid_id"]) for row in body["queries"]}
    runs = [await run_configuration(name, options, queries, bid_ids) for name, options in CONFIGURATIONS.items()]
    not_run = dict(NOT_RUN)
    if importlib.util.find_spec("sentence_transformers") is not None:
        not_run = {name: "runtime installed but not part of this run" for name in NOT_RUN}
    results = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "dataset": str(dataset),
        "query_count": len(queries),
        "k_values": list(K_VALUES),
        "by_k": {str(k): compare_runs(queries, runs, k=k) for k in K_VALUES},
        "first_relevant_rank": per_query_ranks(queries, runs),
        "retrieved": {
            run.name: {
                query_id: [
                    {
                        "rank": rank,
                        "chunk_id": str(chunk.chunk_id),
                        "file_name": chunk.file_name,
                        "page_number": chunk.page_number,
                        "chunk_type": chunk.chunk_type,
                    }
                    for rank, chunk in enumerate(chunks, 1)
                ]
                for query_id, chunks in run.retrieved.items()
            }
            for run in runs
        },
        "not_run": not_run,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(json.dumps(results, indent=1, default=str) + "\n")
    (output_dir / "questions.json").write_text(dataset.read_text())
    write_report(output_dir, results, queries, body)
    for run in results["by_k"]["5"]["runs"]:
        m = run["metrics"]
        print(f"{run['configuration']['name']:<24} R@5={m['recall_at_k']:.3f} MRR={m['mrr']:.3f} Hit@5={m['hit_rate_at_k']:.3f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path, default=Path("evaluation/retrieval_queries.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("../deliverables/retrieval_evaluation"))
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="rebuild report.md from an existing results.json without running retrieval",
    )
    args = parser.parse_args()
    asyncio.run(main_async(args.dataset, args.output_dir, args.report_only))


if __name__ == "__main__":
    main()
