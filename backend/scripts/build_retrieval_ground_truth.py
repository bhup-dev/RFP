"""Resolve evaluation/retrieval_label_spec.json into chunk-level ground truth.

Usage, from the backend directory:
    python -m scripts.build_retrieval_ground_truth BID1_ID BID2_ID \
        [--spec evaluation/retrieval_label_spec.json] [--output evaluation/retrieval_queries.json]

For each question, every stored chunk of that bid whose whitespace-normalized text matches an
answer pattern becomes a relevant source with the matched text kept as evidence. A question
whose patterns match no chunk is an error; nothing is guessed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from app.db.models.chunk import DocumentChunk
from app.db.models.document import Document
from app.db.session import get_session_factory

EVIDENCE_CONTEXT = 80


async def load_chunks(bid_id: UUID) -> list[dict]:
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                select(DocumentChunk, Document.file_name)
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(Document.bid_id == bid_id, Document.ingestion_status == "indexed")
                .order_by(Document.file_name, DocumentChunk.chunk_index)
            )
        ).all()
    return [
        {
            "chunk_id": str(chunk.id),
            "file_name": file_name,
            "page_number": chunk.page_number,
            "chunk_type": chunk.chunk_type,
            "text": " ".join(chunk.text.split()),
        }
        for chunk, file_name in rows
    ]


def label(question: dict, chunks: list[dict]) -> list[dict]:
    relevant: dict[str, dict] = {}
    for answer in question["answers"]:
        pattern = re.compile(answer["pattern"], re.IGNORECASE)
        for chunk in chunks:
            match = pattern.search(chunk["text"])
            if match is None:
                continue
            current = relevant.get(chunk["chunk_id"])
            if current is not None and current["grade"] >= answer["grade"]:
                continue
            start = max(0, match.start() - EVIDENCE_CONTEXT)
            relevant[chunk["chunk_id"]] = {
                "chunk_id": chunk["chunk_id"],
                "file_name": chunk["file_name"],
                "page_number": chunk["page_number"],
                "chunk_type": chunk["chunk_type"],
                "grade": answer["grade"],
                "evidence": chunk["text"][start : match.end() + EVIDENCE_CONTEXT],
            }
    if not relevant:
        raise SystemExit(f"{question['query_id']}: no chunk matches its answer patterns")
    return sorted(relevant.values(), key=lambda item: (-item["grade"], item["file_name"]))


async def build(bids: dict[str, UUID], spec_path: Path, output: Path) -> None:
    spec = json.loads(spec_path.read_text())
    chunks = {name: await load_chunks(bid_id) for name, bid_id in bids.items()}
    queries = []
    for question in spec["questions"]:
        relevant = label(question, chunks[question["bid"]])
        queries.append(
            {
                "query_id": question["query_id"],
                "query": question["query"],
                "bid_identifier": question["bid"],
                "bid_id": str(bids[question["bid"]]),
                "relevant": relevant,
                "expect_no_results": False,
            }
        )
        print(f"{question['query_id']}: {len(relevant)} relevant chunks")
    body = {
        "version": 2,
        "status": "labelled",
        "labelled_by": spec["labelled_by"],
        "method": spec["method"],
        "bids": {name: {"bid_id": str(bid_id), "title": spec["bids"][name]} for name, bid_id in bids.items()},
        "matching": {"preferred": "chunk_id"},
        "queries": queries,
    }
    output.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {len(queries)} labelled queries to {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bid1", type=UUID, help="bid id for the Bid1 questions (Dallas ISD JA-207652)")
    parser.add_argument("bid2", type=UUID, help="bid id for the Bid2 questions (Maryland Dell laptops)")
    parser.add_argument("--spec", type=Path, default=Path("evaluation/retrieval_label_spec.json"))
    parser.add_argument("--output", type=Path, default=Path("evaluation/retrieval_queries.json"))
    args = parser.parse_args()
    asyncio.run(build({"Bid1": args.bid1, "Bid2": args.bid2}, args.spec, args.output))


if __name__ == "__main__":
    main()
