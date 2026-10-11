"""Independent validation endpoint for persisted extraction runs."""

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_session_factory
from app.schemas.validation import ExtractionValidationReport
from app.services.extraction_validation_service import (
    ExtractionRunBidMismatch,
    ExtractionRunNotFound,
    ExtractionValidationService,
    SqlValidationStore,
    ValidationBidNotFound,
    ValidationUnavailable,
)
from app.services.llm_service import get_llm_service

router = APIRouter(prefix=f"{settings.api_prefix}/v1/bids", tags=["extraction-validation"])


async def get_validation_session() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            if session.in_transaction():
                await session.rollback()


def get_extraction_validation_service(
    session: AsyncSession = Depends(get_validation_session),
) -> ExtractionValidationService:
    return ExtractionValidationService(
        SqlValidationStore(session),
        get_llm_service(),
        model_name=settings.llm_model,
        semantic_enabled=settings.validation_semantic_enabled,
        high_threshold=settings.validation_high_confidence_threshold,
        medium_threshold=settings.validation_medium_confidence_threshold,
        max_output_tokens=settings.validation_max_output_tokens,
    )


@router.post(
    "/{bid_id}/extractions/{run_id}/validate",
    response_model=ExtractionValidationReport,
)
async def validate_extraction(
    bid_id: UUID,
    run_id: UUID,
    service: ExtractionValidationService = Depends(get_extraction_validation_service),
) -> ExtractionValidationReport:
    try:
        return await service.validate(bid_id, run_id)
    except ValidationBidNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExtractionRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExtractionRunBidMismatch as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValidationUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "validation_unavailable", "message": str(exc)},
        ) from exc
