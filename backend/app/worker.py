"""Standalone job worker: ``python -m app.worker``.

With the default in-memory LangGraph checkpointer, a workflow that pauses for
human review in this process cannot be resumed by the API process. Use the
embedded worker (``JOB_WORKER_EMBEDDED=true``) until a durable checkpointer exists.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from app.config import settings
from app.core.logging import configure_logging
from app.core.validation import validate_configuration
from app.db.session import dispose_engine, get_session_factory
from app.jobs.worker import Worker, default_worker_id
from app.services.embedding_service import close_embedding_client
from app.services.llm_service import close_llm_client
from app.services.qdrant_service import close_qdrant_client

logger = logging.getLogger("app.worker")


async def main() -> None:
    configure_logging(settings)
    validate_configuration(settings)
    if settings.langgraph_checkpointer_backend == "memory" and settings.human_review_enabled:
        logger.warning(
            "Standalone worker uses an in-memory checkpointer; workflows paused for "
            "human review cannot be resumed from the API process"
        )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    worker = Worker(session_factory=get_session_factory(), worker_id=default_worker_id())
    try:
        await worker.run_forever(stop)
    finally:
        await dispose_engine()
        close_qdrant_client()
        await close_llm_client()
        await close_embedding_client()


if __name__ == "__main__":
    asyncio.run(main())
