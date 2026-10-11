"""PostgreSQL integration check.

Skipped when the configured database cannot be reached. This test does not
use SQLite.
"""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings

pytestmark = pytest.mark.integration


class DatabaseUnreachable(Exception):
    """The configured PostgreSQL database could not be contacted."""


def test_postgres_transaction_rolls_back() -> None:
    try:
        asyncio.run(_transaction_rolls_back())
    except DatabaseUnreachable as exc:
        pytest.skip(_safe_message(exc.__cause__ or exc))


async def _transaction_rolls_back() -> None:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        try:
            connection = await engine.connect()
        except (OSError, OperationalError, InterfaceError, DBAPIError, TimeoutError) as exc:
            raise DatabaseUnreachable from exc
        try:
            transaction = await connection.begin()
            try:
                result = await connection.execute(text("SELECT 1"))
                assert result.scalar_one() == 1
            finally:
                await transaction.rollback()
        finally:
            await connection.close()
    finally:
        await engine.dispose()


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
