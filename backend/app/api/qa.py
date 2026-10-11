"""Grounded question answering over one bid's indexed documents."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.search import get_retrieval_service
from app.config import settings
from app.db.session import get_db_session
from app.retrieval.base import RetrievalUnavailable
from app.retrieval.retrieval_service import RetrievalService
from app.schemas.qa import AskRequest, AskResponse
from app.services.bid_qa_context import SqlBidRecordLoader
from app.services.bid_qa_service import (
    AnswerGenerationFailed,
    BidNotIndexed,
    BidQAService,
    InvalidCompareBids,
    QASettings,
    SqlQABidStore,
)
from app.services.llm_service import get_llm_service
from app.services.provider_errors import ProviderConfigurationError
from app.services.workflow_runner import BidNotFound

router = APIRouter(prefix=f"{settings.api_prefix}/v1", tags=["questions"])


def get_bid_qa_service(
    session: AsyncSession = Depends(get_db_session),
    retrieval: RetrievalService = Depends(get_retrieval_service),
) -> BidQAService:
    return BidQAService(
        store=SqlQABidStore(session),
        retrieval=retrieval,
        llm=get_llm_service(),
        settings=QASettings(
            top_k=settings.qa_top_k,
            max_history_turns=settings.qa_max_history_turns,
            max_output_tokens=settings.qa_max_output_tokens,
            max_passage_characters=settings.qa_max_passage_characters,
            model=settings.llm_model,
            max_compare_bids=settings.qa_max_compare_bids,
        ),
        records=SqlBidRecordLoader(
            session,
            max_addendum_records=settings.qa_max_addendum_records,
            max_review_records=settings.qa_max_review_records,
        ),
    )


@router.post("/bids/{bid_id}/ask", response_model=AskResponse)
async def ask_bid(
    bid_id: UUID,
    request: AskRequest,
    service: BidQAService = Depends(get_bid_qa_service),
) -> AskResponse:
    """Answer from this bid's passages and stored records, plus any added comparison bid."""
    try:
        return await service.ask(bid_id, request)
    except BidNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc).capitalize()) from exc
    except InvalidCompareBids as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_compare_bids", "message": str(exc)},
        ) from exc
    except BidNotIndexed as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "bid_not_indexed",
                "message": (
                    "Process the comparison bid before asking about it"
                    if "comparison" in str(exc)
                    else "Process the bid before asking questions"
                ),
            },
        ) from exc
    except RetrievalUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "retrieval_unavailable",
                "message": "Document search is unavailable",
                "errors": exc.errors,
            },
        ) from exc
    except ProviderConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "llm_not_configured", "message": str(exc)},
        ) from exc
    except AnswerGenerationFailed as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "answer_generation_failed",
                "message": f"Unable to generate an answer: {exc}",
            },
        ) from exc
