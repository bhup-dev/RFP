"""Async SQLAlchemy engine and session dependency.

Importing this module does not connect to PostgreSQL. The engine is created
on first use and can be disposed during application shutdown.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return the shared async engine, creating it on first use."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return a session factory bound to the shared engine."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            expire_on_commit=False,
        )
    return _session_factory


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Yield a transactional session for FastAPI routes.

    The transaction commits when the request finishes and rolls back if the
    request raises. The caller does not need to close the session.
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            yield session


async def dispose_engine() -> None:
    """Dispose the engine if a pool was created. Safe when it was not."""
    global _engine, _session_factory
    if _engine is None:
        return
    await _engine.dispose()
    _engine = None
    _session_factory = None
