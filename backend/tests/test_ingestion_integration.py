"""PostgreSQL registration check.

The test rolls back its transaction. If a row is left behind, only a bid whose
identifier starts with ``test-ingest-`` and matches this run is deleted.
"""

import asyncio
import hashlib
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.models.bid import Bid
from app.db.models.document import Document
from app.ingestion.registry import CHANGED, DUPLICATE, REGISTERED, SqlDocumentRegistry, grouping_identifier

pytestmark = pytest.mark.integration


class DatabaseUnreachable(Exception):
    """The configured PostgreSQL database could not be contacted."""


class _Rollback(Exception):
    """Abort the test transaction after the assertions."""


def test_postgres_registration_is_idempotent_and_rolls_back() -> None:
    identifier = f"test-ingest-{uuid4().hex}"
    try:
        asyncio.run(_register(identifier))
    except DatabaseUnreachable as exc:
        pytest.skip(_safe_message(exc.__cause__ or exc))


async def _register(identifier: str) -> None:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    original = hashlib.sha256(b"notes").hexdigest()
    changed = hashlib.sha256(b"notes-changed").hexdigest()
    relative = f"{identifier}/notes.pdf"
    connected = False
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                connected = True
                async with session.begin():
                    await _exercise(session, identifier, relative, original, changed)
                    raise _Rollback()
        except _Rollback:
            pass
        except (OSError, OperationalError, InterfaceError, DBAPIError, TimeoutError) as exc:
            raise DatabaseUnreachable from exc
    finally:
        try:
            if connected:
                await _remove_if_committed(engine, identifier)
        finally:
            await engine.dispose()


async def _exercise(
    session: AsyncSession,
    identifier: str,
    relative: str,
    original: str,
    changed: str,
) -> None:
    assert grouping_identifier(relative, override=None, input_root_name="bids") == identifier
    registry = SqlDocumentRegistry(session)
    first = await registry.register(
        bid_identifier=identifier,
        relative_path=relative,
        file_name="notes.pdf",
        document_type="pdf",
        file_hash=original,
    )
    repeated = await registry.register(
        bid_identifier=identifier,
        relative_path=relative,
        file_name="notes.pdf",
        document_type="pdf",
        file_hash=original,
    )
    duplicate = await registry.register(
        bid_identifier=identifier,
        relative_path=f"{identifier}/copy.pdf",
        file_name="copy.pdf",
        document_type="html",
        file_hash=original,
    )
    conflict = await registry.register(
        bid_identifier=identifier,
        relative_path=relative,
        file_name="notes.pdf",
        document_type="pdf",
        file_hash=changed,
    )

    assert first.outcome == REGISTERED
    assert repeated.outcome == DUPLICATE
    assert duplicate.outcome == DUPLICATE
    assert duplicate.document_id == first.document_id
    assert conflict.outcome == CHANGED
    assert conflict.document_id == first.document_id

    bid = await session.get(Bid, first.bid_id)
    document = await session.get(Document, first.document_id)
    assert bid is not None
    assert document is not None
    assert bid.bid_identifier == identifier
    assert bid.solicitation_number is None
    assert bid.title is None
    assert bid.issuing_organization is None
    assert document.storage_path == relative
    assert document.file_hash == original
    assert document.ingestion_status == "pending"
    assert document.document_date is None
    assert document.addendum_number is None
    rows = (
        await session.execute(text("SELECT count(*) FROM documents WHERE bid_id = :bid_id"), {"bid_id": first.bid_id})
    ).scalar_one()
    assert rows == 1


async def _remove_if_committed(engine: object, identifier: str) -> None:
    if not identifier.startswith("test-ingest-"):
        return
    async with engine.connect() as connection:  # type: ignore[attr-defined]
        transaction = await connection.begin()
        count = (
            await connection.execute(
                text("SELECT count(*) FROM bids WHERE bid_identifier = :identifier"),
                {"identifier": identifier},
            )
        ).scalar_one()
        if count:
            await connection.execute(
                text("DELETE FROM bids WHERE bid_identifier = :identifier"),
                {"identifier": identifier},
            )
            await transaction.commit()
            raise AssertionError("ingestion test rows were committed and then removed")
        await transaction.rollback()


def _safe_message(exc: BaseException) -> str:
    """Describe a connection failure without including the database URL."""
    detail = str(exc).lower()
    if "does not exist" in detail:
        reason = "the configured database does not exist"
    elif "password" in detail or "authentication" in detail:
        reason = "authentication failed"
    elif "refused" in detail or "could not connect" in detail:
        reason = "the server refused the connection"
    else:
        reason = type(exc).__name__
    return f"PostgreSQL is not available ({reason})"
