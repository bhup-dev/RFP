"""Read and create bids and documents for the browsing and upload APIs."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.bid import Bid
from app.db.models.document import Document
from app.db.models.job import ProcessingJob
from app.db.models.orchestration import BidWorkflowRun
from app.schemas.bids import BidCreate, BidSummary, JobRead

INDEXED_STATUSES = ("indexed", "partial", "completed")


class BidIdentifierTaken(Exception):
    def __init__(self, existing: Bid) -> None:
        super().__init__("a bid with this identifier already exists")
        self.existing = existing


@dataclass(frozen=True)
class BidPage:
    items: list[BidSummary]
    total: int


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SqlBidCatalog:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_bid(self, data: BidCreate) -> Bid:
        existing = await self._by_identifier(data.bid_identifier)
        if existing is not None:
            raise BidIdentifierTaken(existing)
        bid = Bid(
            id=uuid4(),
            bid_identifier=data.bid_identifier,
            title=data.title,
            solicitation_number=data.solicitation_number,
            issuing_organization=data.issuing_organization,
        )
        self._session.add(bid)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            found = await self._by_identifier(data.bid_identifier)
            if found is not None:
                raise BidIdentifierTaken(found) from exc
            raise
        return bid

    async def get_bid(self, bid_id: UUID) -> Bid | None:
        return await self._session.get(Bid, bid_id)

    async def list_bids(self, *, limit: int, offset: int, search: str | None) -> BidPage:
        condition = None
        if search and search.strip():
            pattern = f"%{_escape_like(search.strip())}%"
            condition = or_(
                Bid.bid_identifier.ilike(pattern, escape="\\"),
                Bid.title.ilike(pattern, escape="\\"),
                Bid.solicitation_number.ilike(pattern, escape="\\"),
                Bid.issuing_organization.ilike(pattern, escape="\\"),
            )
        total_query = select(func.count(Bid.id))
        bids_query = select(Bid).order_by(Bid.created_at.desc(), Bid.id).limit(limit).offset(offset)
        if condition is not None:
            total_query = total_query.where(condition)
            bids_query = bids_query.where(condition)
        total = int(await self._session.scalar(total_query) or 0)
        bids = list((await self._session.scalars(bids_query)).all())
        return BidPage(items=await self.summaries(bids), total=total)

    async def summaries(self, bids: list[Bid]) -> list[BidSummary]:
        if not bids:
            return []
        ids = [bid.id for bid in bids]
        counts = {
            row.bid_id: (row.total, row.indexed)
            for row in (
                await self._session.execute(
                    select(
                        Document.bid_id,
                        func.count(Document.id).label("total"),
                        func.count(Document.id)
                        .filter(Document.ingestion_status.in_(INDEXED_STATUSES))
                        .label("indexed"),
                    )
                    .where(Document.bid_id.in_(ids))
                    .group_by(Document.bid_id)
                )
            ).all()
        }
        latest_jobs = {
            job.bid_id: job
            for job in (
                await self._session.scalars(
                    select(ProcessingJob)
                    .where(ProcessingJob.bid_id.in_(ids))
                    .order_by(ProcessingJob.bid_id, ProcessingJob.created_at.desc())
                    .distinct(ProcessingJob.bid_id)
                )
            ).all()
        }
        latest_runs = {
            row.bid_id: row.status
            for row in (
                await self._session.execute(
                    select(BidWorkflowRun.bid_id, BidWorkflowRun.status)
                    .where(BidWorkflowRun.bid_id.in_(ids))
                    .order_by(BidWorkflowRun.bid_id, BidWorkflowRun.started_at.desc())
                    .distinct(BidWorkflowRun.bid_id)
                )
            ).all()
        }
        summaries = []
        for bid in bids:
            total, indexed = counts.get(bid.id, (0, 0))
            job = latest_jobs.get(bid.id)
            summaries.append(
                BidSummary(
                    id=bid.id,
                    bid_identifier=bid.bid_identifier,
                    title=bid.title,
                    solicitation_number=bid.solicitation_number,
                    issuing_organization=bid.issuing_organization,
                    created_at=bid.created_at,
                    document_count=total,
                    indexed_document_count=indexed,
                    latest_job=JobRead.model_validate(job) if job is not None else None,
                    latest_workflow_status=latest_runs.get(bid.id),
                )
            )
        return summaries

    async def list_documents(self, bid_id: UUID) -> list[Document]:
        rows = await self._session.scalars(
            select(Document)
            .where(Document.bid_id == bid_id)
            .order_by(Document.created_at, Document.id)
        )
        return list(rows.all())

    async def get_document(self, document_id: UUID) -> Document | None:
        return await self._session.get(Document, document_id)

    async def _by_identifier(self, identifier: str) -> Bid | None:
        return await self._session.scalar(select(Bid).where(Bid.bid_identifier == identifier))
