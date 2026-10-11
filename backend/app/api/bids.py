"""Bid creation, browser uploads, browsing, and background processing jobs."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_session_factory
from app.ingestion.exceptions import IngestionError
from app.ingestion.file_utils import resolve_registered_file
from app.jobs.repository import ActiveJobExists, JobRepository
from app.schemas.bids import (
    BidCreate,
    BidDetail,
    BidListResponse,
    BidSummary,
    DocumentRead,
    JobRead,
    ProcessRequest,
    UploadLimits,
    UploadResponse,
    UploadResultItem,
)
from app.services.bid_catalog import BidIdentifierTaken, SqlBidCatalog
from app.services.upload_service import DOCUMENT_ROLES, UploadRequestItem, UploadService

router = APIRouter(prefix=f"{settings.api_prefix}/v1", tags=["bids"])

_MEDIA_TYPES = {"pdf": "application/pdf", "html": "text/html"}


async def get_bids_session() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            if session.in_transaction():
                await session.rollback()


def get_bid_catalog(session: AsyncSession = Depends(get_bids_session)) -> SqlBidCatalog:
    return SqlBidCatalog(session)


def get_job_repository(session: AsyncSession = Depends(get_bids_session)) -> JobRepository:
    return JobRepository(session)


def get_upload_service(session: AsyncSession = Depends(get_bids_session)) -> UploadService:
    return UploadService(
        session=session,
        input_root=settings.ingestion_input_root,
        max_file_bytes=settings.upload_max_file_bytes,
        allowed_extensions=settings.upload_extensions,
    )


async def _require_bid(catalog: SqlBidCatalog, bid_id: UUID):
    bid = await catalog.get_bid(bid_id)
    if bid is None:
        raise HTTPException(status_code=404, detail="bid not found")
    return bid


@router.get("/uploads/limits", response_model=UploadLimits)
def upload_limits() -> UploadLimits:
    return UploadLimits(
        max_file_bytes=settings.upload_max_file_bytes,
        max_files_per_request=settings.upload_max_files_per_request,
        allowed_extensions=sorted(settings.upload_extensions),
        roles=["auto", *DOCUMENT_ROLES],
    )


@router.post("/bids", response_model=BidSummary, status_code=status.HTTP_201_CREATED)
async def create_bid(
    body: BidCreate,
    catalog: SqlBidCatalog = Depends(get_bid_catalog),
) -> BidSummary:
    try:
        bid = await catalog.create_bid(body)
    except BidIdentifierTaken as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "bid_id": str(exc.existing.id)},
        ) from exc
    return (await catalog.summaries([bid]))[0]


@router.get("/bids", response_model=BidListResponse)
async def list_bids(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None, max_length=200),
    catalog: SqlBidCatalog = Depends(get_bid_catalog),
) -> BidListResponse:
    page = await catalog.list_bids(limit=limit, offset=offset, search=search)
    return BidListResponse(items=page.items, total=page.total, limit=limit, offset=offset)


@router.get("/bids/{bid_id}", response_model=BidDetail)
async def get_bid(
    bid_id: UUID,
    catalog: SqlBidCatalog = Depends(get_bid_catalog),
    jobs: JobRepository = Depends(get_job_repository),
) -> BidDetail:
    bid = await _require_bid(catalog, bid_id)
    summary = (await catalog.summaries([bid]))[0]
    documents = await catalog.list_documents(bid_id)
    return BidDetail(
        **summary.model_dump(),
        documents=[DocumentRead.model_validate(item) for item in documents],
        jobs=[JobRead.model_validate(item) for item in await jobs.list_for_bid(bid_id)],
    )


@router.get("/bids/{bid_id}/documents", response_model=list[DocumentRead])
async def list_bid_documents(
    bid_id: UUID,
    catalog: SqlBidCatalog = Depends(get_bid_catalog),
) -> list[DocumentRead]:
    await _require_bid(catalog, bid_id)
    return [DocumentRead.model_validate(item) for item in await catalog.list_documents(bid_id)]


@router.post("/bids/{bid_id}/documents", response_model=UploadResponse)
async def upload_documents(
    bid_id: UUID,
    files: list[UploadFile] = File(...),
    roles: list[str] = Form(default=[]),
    addendum_numbers: list[str] = Form(default=[]),
    catalog: SqlBidCatalog = Depends(get_bid_catalog),
    jobs: JobRepository = Depends(get_job_repository),
    uploads: UploadService = Depends(get_upload_service),
) -> UploadResponse:
    """Store files for later processing. ``roles``/``addendum_numbers`` align with ``files``."""
    bid = await _require_bid(catalog, bid_id)
    if not files:
        raise HTTPException(status_code=400, detail="select at least one file")
    if len(files) > settings.upload_max_files_per_request:
        raise HTTPException(
            status_code=413,
            detail=f"at most {settings.upload_max_files_per_request} files per upload",
        )
    if roles and len(roles) != len(files):
        raise HTTPException(status_code=422, detail="roles must align with files")
    if addendum_numbers and len(addendum_numbers) != len(files):
        raise HTTPException(status_code=422, detail="addendum_numbers must align with files")
    active = await jobs.active_for_bid(bid_id)
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "this bid is being processed; upload after the job finishes",
                "job_id": str(active.id),
            },
        )

    items = []
    for index, upload in enumerate(files):
        raw_number = addendum_numbers[index].strip() if addendum_numbers else ""
        try:
            number = int(raw_number) if raw_number else None
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="addendum_numbers must be whole numbers"
            ) from exc
        items.append(
            UploadRequestItem(
                source=upload,
                role=roles[index] if roles else None,
                addendum_number=number,
            )
        )

    try:
        outcomes = await uploads.upload(bid, items)
        await uploads.session.commit()
    except IngestionError as exc:
        uploads.discard_written_files()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except BaseException:
        uploads.discard_written_files()
        raise
    finally:
        for upload in files:
            await upload.close()

    results = [
        UploadResultItem(
            original_filename=item.original_filename,
            outcome=item.outcome,
            document_id=item.document_id,
            stored_name=item.stored_name,
            size_bytes=item.size_bytes,
            role=item.role,
            reason=item.reason,
        )
        for item in outcomes
    ]
    return UploadResponse(
        bid_id=bid_id,
        registered=sum(1 for item in results if item.outcome == "registered"),
        duplicates=sum(1 for item in results if item.outcome == "duplicate"),
        rejected=sum(1 for item in results if item.outcome == "rejected"),
        results=results,
    )


@router.post(
    "/bids/{bid_id}/process",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_processing(
    bid_id: UUID,
    body: ProcessRequest | None = None,
    catalog: SqlBidCatalog = Depends(get_bid_catalog),
    jobs: JobRepository = Depends(get_job_repository),
) -> JobRead:
    """Queue ingestion/indexing and the analysis workflow. Poll the returned job."""
    request = body or ProcessRequest()
    await _require_bid(catalog, bid_id)
    if not await catalog.list_documents(bid_id):
        raise HTTPException(status_code=409, detail="upload documents before processing")
    try:
        job = await jobs.enqueue(
            bid_id, request.job_type, max_attempts=settings.job_max_attempts
        )
    except ActiveJobExists as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "job_id": str(exc.job.id)},
        ) from exc
    return JobRead.model_validate(job)


@router.get("/bids/{bid_id}/jobs", response_model=list[JobRead])
async def list_bid_jobs(
    bid_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    catalog: SqlBidCatalog = Depends(get_bid_catalog),
    jobs: JobRepository = Depends(get_job_repository),
) -> list[JobRead]:
    await _require_bid(catalog, bid_id)
    return [JobRead.model_validate(item) for item in await jobs.list_for_bid(bid_id, limit=limit)]


@router.get("/jobs", response_model=list[JobRead])
async def list_recent_jobs(
    limit: int = Query(default=10, ge=1, le=50),
    jobs: JobRepository = Depends(get_job_repository),
) -> list[JobRead]:
    return [JobRead.model_validate(item) for item in await jobs.list_recent(limit=limit)]


@router.get("/jobs/{job_id}", response_model=JobRead)
async def get_job(
    job_id: UUID,
    jobs: JobRepository = Depends(get_job_repository),
) -> JobRead:
    job = await jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return JobRead.model_validate(job)


@router.get("/documents/{document_id}/file")
async def download_document(
    document_id: UUID,
    download: bool = Query(default=False),
    catalog: SqlBidCatalog = Depends(get_bid_catalog),
) -> FileResponse:
    """Stream the stored source file. HTML is always sent as a sandboxed attachment."""
    document = await catalog.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    try:
        path = resolve_registered_file(settings.ingestion_input_root, document.storage_path)
    except IngestionError as exc:
        raise HTTPException(status_code=404, detail="source file is not available") from exc
    media_type = _MEDIA_TYPES.get((document.document_type or "").lower(), "application/octet-stream")
    inline = media_type == "application/pdf" and not download
    return FileResponse(
        path,
        media_type=media_type,
        filename=document.original_filename or document.file_name,
        content_disposition_type="inline" if inline else "attachment",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "Cache-Control": "private, max-age=300",
        },
    )
