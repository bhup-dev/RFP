"""FastAPI application entry point."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.addendum_analysis import router as addendum_analysis_router
from app.api.bids import router as bids_router
from app.api.comparison import router as comparison_router
from app.api.extraction import router as extraction_router
from app.api.extraction_validation import router as extraction_validation_router
from app.api.indexing import router as indexing_router
from app.api.intelligence_profile import router as intelligence_profile_router
from app.api.orchestration import router as orchestration_router
from app.api.qa import router as qa_router
from app.api.reviews import router as reviews_router
from app.api.ingestion import router as ingestion_router
from app.api.search import router as search_router
from app.api.workflow import router as workflow_router
from app.config import settings
from app.core.health import readiness_report, run_blocking
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.core.redaction import redact_text
from app.core.storage import ensure_storage_directory
from app.core.validation import validate_configuration
from app.db.session import dispose_engine, get_session_factory
from app.jobs.worker import Worker, default_worker_id
from app.services.embedding_service import close_embedding_client
from app.services.llm_service import close_llm_client
from app.services.qdrant_service import QdrantReadiness, close_qdrant_client, get_qdrant_service

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Configure logs, then release clients on shutdown.

    Startup does not connect to PostgreSQL, Qdrant, or a model provider.
    """
    configure_logging(settings)
    validate_configuration(settings)
    ensure_storage_directory(settings.storage_path)
    logger.info(
        "Application starting environment=%s log_format=%s",
        settings.environment,
        settings.log_format,
    )
    worker_stop = asyncio.Event()
    worker_task: asyncio.Task[None] | None = None
    if settings.job_worker_embedded:
        worker = Worker(session_factory=get_session_factory(), worker_id=default_worker_id())
        worker_task = asyncio.create_task(worker.run_forever(worker_stop))
    yield
    if worker_task is not None:
        worker_stop.set()
        try:
            await asyncio.wait_for(worker_task, timeout=30)
        except TimeoutError:
            worker_task.cancel()
    await dispose_engine()
    close_qdrant_client()
    await close_llm_client()
    await close_embedding_client()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=False,
    lifespan=lifespan,
)
app.add_middleware(RequestContextMiddleware)
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Reviewer-ID", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Content-Disposition"],
    )
app.include_router(bids_router)
app.include_router(ingestion_router)
app.include_router(indexing_router)
app.include_router(search_router)
app.include_router(qa_router)
app.include_router(workflow_router)
app.include_router(extraction_router)
app.include_router(addendum_analysis_router)
app.include_router(extraction_validation_router)
app.include_router(intelligence_profile_router)
app.include_router(orchestration_router)
app.include_router(comparison_router)
app.include_router(reviews_router)


@app.get("/health")
def health() -> dict[str, str]:
    """Report that the API process is running."""
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready() -> JSONResponse:
    """Report PostgreSQL and Qdrant readiness. Model providers are not called."""
    body = await readiness_report()
    status_code = 200 if body["status"] == "ok" else 503
    return JSONResponse(status_code=status_code, content=body)


@app.get("/health/qdrant")
async def qdrant_health() -> JSONResponse:
    """Report Qdrant connectivity and whether the configured collection exists."""
    try:
        readiness = await run_blocking(
            get_qdrant_service().readiness,
            settings.health_check_timeout_seconds,
        )
    except TimeoutError:
        readiness = QdrantReadiness(
            connected=False,
            collection_ready=False,
            detail="Qdrant readiness check timed out",
        )
    body: dict[str, object] = {
        "status": "ok" if readiness.ready else "unavailable",
        "connected": readiness.connected,
        "collection_ready": readiness.collection_ready,
        "collection": settings.qdrant_collection_name,
    }
    if readiness.detail:
        body["detail"] = redact_text(readiness.detail)
    status_code = 200 if readiness.ready else 503
    return JSONResponse(status_code=status_code, content=body)
