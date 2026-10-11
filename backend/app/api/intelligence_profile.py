"""Read-only final bid intelligence profile endpoint."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db_session
from app.schemas.intelligence import BidIntelligenceProfile
from app.services.bid_intelligence_service import (
    BidIntelligenceService,
    IntelligenceBidNotFound,
    IntelligenceExtractionBidMismatch,
    IntelligenceExtractionNotFound,
    SqlIntelligenceStore,
)

router = APIRouter(prefix=f"{settings.api_prefix}/v1/bids", tags=["intelligence-profile"])


def get_bid_intelligence_service(
    session: AsyncSession = Depends(get_db_session),
) -> BidIntelligenceService:
    return BidIntelligenceService(SqlIntelligenceStore(session))


@router.get("/{bid_id}/intelligence-profile", response_model=BidIntelligenceProfile)
async def intelligence_profile(
    bid_id: UUID,
    extraction_run_id: UUID | None = Query(default=None),
    service: BidIntelligenceService = Depends(get_bid_intelligence_service),
) -> BidIntelligenceProfile:
    """Consolidate persisted results; never launches extraction or model calls."""
    try:
        return await service.build(bid_id, extraction_run_id)
    except IntelligenceBidNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IntelligenceExtractionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IntelligenceExtractionBidMismatch as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
