"""CLI argument parsing and dispatch, with a fake session and fake command handlers."""

import asyncio
import json
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from pydantic import BaseModel

from app import cli
from app.services.run_trace import NO_MODEL_NODES, PROMPT_LOCATIONS

BID = uuid4()


class FakeSession:
    def __init__(self, in_transaction=True):
        self._in_transaction = in_transaction
        self.rolled_back = False

    def in_transaction(self):
        return self._in_transaction

    async def rollback(self):
        self.rolled_back = True


def _factory(session):
    @asynccontextmanager
    async def factory():
        yield session

    return factory


class Result(BaseModel):
    value: str


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["ingest", "--folder", "bid6", "--bid", "bid6"], {"folder": "bid6", "bid": "bid6"}),
        (["process", str(BID)], {"bid_id": BID}),
        (["profile", str(BID), "--output", "p.json"], {"bid_id": BID}),
        (["search", str(BID), "due date", "--top-k", "3"], {"query": "due date", "top_k": 3}),
        (["ask", str(BID), "Due?", "--compare", str(BID)], {"question": "Due?", "compare": [BID]}),
        (["trace", str(BID)], {"bid_id": BID, "workflow_id": None}),
    ],
)
def test_parser_accepts_every_command(argv, expected):
    args = cli.build_parser().parse_args(argv)
    assert args.command == argv[0]
    for key, value in expected.items():
        assert getattr(args, key) == value


def test_parser_rejects_bad_bid_id():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["profile", "not-a-uuid"])


def test_run_dispatches_and_serializes_models(monkeypatch):
    seen = {}

    async def fake_profile(session, args):
        seen["session"] = session
        return Result(value=str(args.bid_id))

    monkeypatch.setitem(cli.COMMANDS, "profile", fake_profile)
    session = FakeSession()
    args = cli.build_parser().parse_args(["profile", str(BID)])

    text = asyncio.run(cli.run(args, _factory(session)))

    assert json.loads(text) == {"value": str(BID)}
    assert seen["session"] is session
    assert session.rolled_back is True


def test_main_writes_output_file(monkeypatch, tmp_path):
    async def fake_trace(session, args):
        return {"workflow": {"bid_id": str(args.bid_id)}}

    monkeypatch.setitem(cli.COMMANDS, "trace", fake_trace)
    monkeypatch.setattr(cli, "get_session_factory", lambda: _factory(FakeSession(False)))
    output = tmp_path / "out" / "trace.json"

    assert cli.main(["trace", str(BID), "--output", str(output)]) == 0
    assert json.loads(output.read_text())["workflow"]["bid_id"] == str(BID)


def test_main_reports_service_errors(monkeypatch, capsys):
    async def failing(session, args):
        raise RuntimeError("bid not found")

    monkeypatch.setitem(cli.COMMANDS, "process", failing)
    monkeypatch.setattr(cli, "get_session_factory", lambda: _factory(FakeSession()))

    assert cli.main(["process", str(BID)]) == 1
    assert "RuntimeError: bid not found" in capsys.readouterr().err


def test_trace_prompt_locations_cover_only_model_nodes():
    assert not set(PROMPT_LOCATIONS) & NO_MODEL_NODES
    assert "extract_rfp_fields" in PROMPT_LOCATIONS
