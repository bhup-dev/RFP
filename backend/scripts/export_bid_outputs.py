"""Write each bid's stored 20-field intelligence profile to <output-dir>/<name>.json.

Usage, from the backend directory:
    python -m scripts.export_bid_outputs BID_ID [BID_ID ...] --output-dir ../deliverables/bid_outputs \
        [--names Bid1 Bid2]

The file name is the bid's stored identifier unless --names gives one name per bid id, in order.
Nothing is extracted or recomputed: each file is the stored profile (values, validation status,
confidence scores, supporting and contradicting citations, addendum changes) exactly as the
`GET /api/v1/bids/{bid_id}/intelligence-profile` endpoint returns it.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from uuid import UUID

from app.db.models.bid import Bid
from app.db.session import get_session_factory
from app.services.bid_intelligence_service import (
    BidIntelligenceService,
    SqlIntelligenceStore,
)


async def export(bid_ids: list[UUID], output_dir: Path, names: list[str] | None) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    async with get_session_factory()() as session:
        for index, bid_id in enumerate(bid_ids):
            bid = await session.get(Bid, bid_id)
            if bid is None:
                raise SystemExit(f"bid {bid_id} not found")
            profile = await BidIntelligenceService(SqlIntelligenceStore(session)).build(bid_id)
            name = names[index] if names else bid.bid_identifier
            path = output_dir / f"{name}.json"
            path.write_text(profile.model_dump_json(indent=2) + "\n")
            print(f"{name} ({bid.bid_identifier}): {profile.metadata.status.value} -> {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bid_ids", type=UUID, nargs="+")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--names", nargs="+", help="output file names, one per bid id")
    args = parser.parse_args()
    if args.names and len(args.names) != len(args.bid_ids):
        parser.error("--names needs exactly one name per bid id")
    asyncio.run(export(args.bid_ids, args.output_dir, args.names))


if __name__ == "__main__":
    main()
