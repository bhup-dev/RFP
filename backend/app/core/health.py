"""Dependency checks for readiness. Liveness does not call this module."""

import asyncio
import logging
from collections.abc import Callable
from typing import TypeVar

from sqlalchemy import text

from app.config import settings
from app.core.redaction import redact_text
from app.db.session import get_engine
from app.services.qdrant_service import get_qdrant_service

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def run_blocking(func: Callable[[], T], timeout: float) -> T:
    """Run a sync call and return when ``timeout`` elapses.

    ``asyncio.wait_for`` on Python 3.11 waits for a cancelled worker thread
    to finish, so a hung client would hold the health response. This wait
    returns on time and leaves that thread to finish in the background.
    """
    task = asyncio.create_task(asyncio.to_thread(func))
    done, _pending = await asyncio.wait({task}, timeout=timeout)
    if task not in done:
        task.add_done_callback(_consume_late_result)
        raise TimeoutError
    return task.result()


def _consume_late_result(task: asyncio.Task[object]) -> None:
    if task.cancelled():
        return
    task.exception()


async def postgresql_check() -> dict[str, str]:
    """Run ``SELECT 1`` and close the connection. No migrations are applied."""
    timeout = settings.health_check_timeout_seconds

    async def _ping() -> None:
        connection = await get_engine().connect()
        try:
            await connection.execute(text("SELECT 1"))
        finally:
            await connection.close()

    try:
        await asyncio.wait_for(_ping(), timeout=timeout)
    except TimeoutError:
        logger.error("PostgreSQL readiness check timed out")
        return {"status": "unavailable", "detail": "PostgreSQL readiness check timed out"}
    except Exception as exc:
        logger.error("PostgreSQL readiness check failed (%s)", type(exc).__name__)
        return {"status": "unavailable", "detail": "PostgreSQL is unavailable"}
    return {"status": "ok"}


async def qdrant_check() -> dict[str, str]:
    """Use the shared Qdrant service. This does not create or resize a collection."""
    timeout = settings.health_check_timeout_seconds
    try:
        readiness = await run_blocking(get_qdrant_service().readiness, timeout)
    except TimeoutError:
        logger.error("Qdrant readiness check timed out")
        return {"status": "unavailable", "detail": "Qdrant readiness check timed out"}

    if readiness.ready:
        return {"status": "ok"}
    detail = redact_text(readiness.detail or "Qdrant is unavailable")
    return {"status": "unavailable", "detail": detail}


async def readiness_report() -> dict[str, object]:
    """Check PostgreSQL and Qdrant. LLM and embedding providers are not called."""
    postgresql, qdrant = await asyncio.gather(postgresql_check(), qdrant_check())
    ready = postgresql["status"] == "ok" and qdrant["status"] == "ok"
    return {
        "status": "ok" if ready else "unavailable",
        "postgresql": postgresql,
        "qdrant": qdrant,
    }
