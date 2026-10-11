"""Command-line access to ingestion, the bid workflow, search, Q&A, profiles, and traces.

Run from the backend directory:
    python -m app.cli ingest --folder FOLDER_NAME [--bid ID]
    python -m app.cli process BID_ID
    python -m app.cli profile BID_ID --output profile.json
    python -m app.cli search BID_ID "When are proposals due?" --top-k 5
    python -m app.cli ask BID_ID "When are proposals due?" [--compare OTHER_BID_ID]
    python -m app.cli trace BID_ID --output trace.json

Every command uses the same services as the matching API endpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_factory

Handler = Callable[[AsyncSession, argparse.Namespace], Awaitable[Any]]


async def ingest(session: AsyncSession, args: argparse.Namespace) -> Any:
    from app.api.workflow import get_end_to_end_pipeline

    return await get_end_to_end_pipeline(session).process_directory(
        args.folder, bid_identifier=args.bid
    )


async def process(session: AsyncSession, args: argparse.Namespace) -> Any:
    from app.api.orchestration import get_workflow_dependencies
    from app.schemas.orchestration import BidWorkflowResponse
    from app.services.workflow_runner import execute_bid_workflow

    state = await execute_bid_workflow(args.bid_id, get_workflow_dependencies(session))
    return BidWorkflowResponse.from_state(state)


async def profile(session: AsyncSession, args: argparse.Namespace) -> Any:
    from app.services.bid_intelligence_service import (
        BidIntelligenceService,
        SqlIntelligenceStore,
    )

    return await BidIntelligenceService(SqlIntelligenceStore(session)).build(args.bid_id)


async def search(session: AsyncSession, args: argparse.Namespace) -> Any:
    from app.api.search import get_retrieval_service
    from app.schemas.retrieval import RetrievalRequest

    return await get_retrieval_service(session).search(
        RetrievalRequest(query=args.query, bid_id=args.bid_id, top_k=args.top_k)
    )


async def ask(session: AsyncSession, args: argparse.Namespace) -> Any:
    from app.api.qa import get_bid_qa_service
    from app.api.search import get_retrieval_service
    from app.schemas.qa import AskRequest

    service = get_bid_qa_service(session, get_retrieval_service(session))
    return await service.ask(
        args.bid_id,
        AskRequest(question=args.question, compare_bid_ids=args.compare or []),
    )


async def trace(session: AsyncSession, args: argparse.Namespace) -> Any:
    from app.services.run_trace import build_run_trace

    return await build_run_trace(session, args.bid_id, args.workflow_id)


COMMANDS: dict[str, Handler] = {
    "ingest": ingest,
    "process": process,
    "profile": profile,
    "search": search,
    "ask": ask,
    "trace": trace,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    ingest_parser = commands.add_parser(
        "ingest", help="Register, parse, chunk, and index a bid folder under INGESTION_INPUT_ROOT"
    )
    ingest_parser.add_argument("--folder", required=True, help="folder relative to INGESTION_INPUT_ROOT")
    ingest_parser.add_argument("--bid", help="bid identifier (defaults to the folder name)")

    for name, help_text in (
        ("process", "Run the LangGraph bid workflow (extraction, addenda, agents, validation)"),
        ("profile", "Print the stored 20-field intelligence profile"),
        ("trace", "Print a trace of the latest workflow run built from stored records"),
    ):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("bid_id", type=UUID)
        if name == "trace":
            sub.add_argument("--workflow-id", type=UUID, help="trace this run instead of the latest")

    search_parser = commands.add_parser("search", help="Hybrid search within one bid")
    search_parser.add_argument("bid_id", type=UUID)
    search_parser.add_argument("query")
    search_parser.add_argument("--top-k", type=int, default=5)

    ask_parser = commands.add_parser("ask", help="Ask the read-only bid chat a question")
    ask_parser.add_argument("bid_id", type=UUID)
    ask_parser.add_argument("question")
    ask_parser.add_argument("--compare", type=UUID, action="append", help="bid to compare with")

    for sub in commands.choices.values():
        sub.add_argument("--output", type=Path, help="write JSON here instead of stdout")
    return parser


def to_json(result: Any) -> str:
    payload = result.model_dump(mode="json") if isinstance(result, BaseModel) else result
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


async def run(args: argparse.Namespace, session_factory=None) -> str:
    factory = session_factory or get_session_factory()
    async with factory() as session:
        try:
            return to_json(await COMMANDS[args.command](session, args))
        finally:
            if session.in_transaction():
                await session.rollback()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        text = asyncio.run(run(args))
    except Exception as exc:  # noqa: BLE001 - report any service error as a CLI failure
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
