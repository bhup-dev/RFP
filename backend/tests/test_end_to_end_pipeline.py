"""End-to-end coordinator stage ordering, resumption, and isolation."""

import asyncio
from uuid import uuid4

from app.ingestion.chunking.base import ChunkDocument
from app.ingestion.end_to_end_pipeline import EndToEndPipeline, WorkflowDocument
from app.schemas.chunking import ChunkingSummary
from app.schemas.indexing import IndexMessage, IndexingSummary
from app.schemas.ingestion import (
    IngestionFileResult,
    IngestionOutcome,
    IngestionSummary,
)
from app.schemas.parsing import ParseMessage, ParsedDocument, ParsedSection


def _record(*, status="pending", chunks=0, name="source.pdf") -> WorkflowDocument:
    document_id, bid_id = uuid4(), uuid4()
    return WorkflowDocument(
        document_id=document_id,
        bid_id=bid_id,
        relative_path=f"Bid/{name}",
        file_name=name,
        file_type="pdf",
        file_hash="a" * 64,
        ingestion_status=status,
        chunk_count=chunks,
        chunk_document=ChunkDocument(
            document_id=document_id,
            bid_id=bid_id,
            file_name=name,
            file_type="pdf",
            relative_path=f"Bid/{name}",
        ),
    )


class Source:
    def __init__(self, records, current=True) -> None:
        self.records = {record.document_id: record for record in records}
        self.current = current
        self.checkpoints = 0

    async def get(self, document_id):
        return self.records.get(document_id)

    def source_state(self, document):
        return (
            (True, None)
            if self.current
            else (False, "source content differs from the registered hash")
        )

    async def checkpoint(self):
        self.checkpoints += 1


class Scanner:
    def __init__(self, items) -> None:
        self.items = items
        self.calls = []

    async def scan(self, relative_directory=None, *, bid_identifier=None):
        self.calls.append((relative_directory, bid_identifier))
        return IngestionSummary(
            input_root="bids",
            files_discovered=len(self.items),
            files_registered=sum(
                item.outcome == IngestionOutcome.REGISTERED for item in self.items
            ),
            duplicates_skipped=sum(
                item.outcome == IngestionOutcome.DUPLICATE for item in self.items
            ),
            changed_files=sum(
                item.outcome == IngestionOutcome.CHANGED for item in self.items
            ),
            unsupported_files=sum(
                item.outcome == IngestionOutcome.UNSUPPORTED for item in self.items
            ),
            failed_files=sum(
                item.outcome == IngestionOutcome.FAILED for item in self.items
            ),
            results=self.items,
        )


class Parser:
    def __init__(self, failed=()) -> None:
        self.failed = set(failed)
        self.calls = []

    def parse(self, source):
        self.calls.append(source.document_id)
        if source.document_id in self.failed:
            return ParsedDocument(
                document_id=source.document_id,
                relative_path=source.relative_path,
                file_name=source.file_name,
                document_type="pdf",
                parser_name="test",
                parser_version="1",
                status="failed",
                text="",
                sections=[],
                tables=[],
                warnings=[],
                errors=[ParseMessage(code="unreadable", message="document is unreadable")],
                metadata={},
            )
        text = f"Requirements for {source.file_name}"
        return ParsedDocument(
            document_id=source.document_id,
            relative_path=source.relative_path,
            file_name=source.file_name,
            document_type="pdf",
            parser_name="test",
            parser_version="1",
            status="parsed",
            text=text,
            sections=[ParsedSection(page_number=1, text=text)],
            tables=[],
            warnings=[],
            errors=[],
            metadata={},
        )


class Chunker:
    def __init__(self) -> None:
        self.calls = []

    async def persist(self, source, parsed):
        self.calls.append((source.document_id, parsed.document_id))
        return ChunkingSummary(
            document_id=source.document_id,
            chunks_created=1,
            text_chunks_created=1,
        )


class Indexer:
    def __init__(self, failed=()) -> None:
        self.failed = set(failed)
        self.calls = []

    async def index_document(self, document_id):
        self.calls.append(document_id)
        if document_id in self.failed:
            return IndexingSummary(
                document_id=document_id,
                chunks_requested=1,
                chunks_embedded=1,
                chunks_failed=1,
                errors=[
                    IndexMessage(
                        code="qdrant_upsert",
                        message="Qdrant upsert failed",
                        document_id=document_id,
                    )
                ],
                ingestion_status="failed",
            )
        return IndexingSummary(
            document_id=document_id,
            chunks_requested=1,
            chunks_embedded=1,
            points_upserted=1,
            ingestion_status="indexed",
        )


def _item(record, outcome=IngestionOutcome.REGISTERED):
    return IngestionFileResult(
        relative_path=record.relative_path,
        document_type=record.file_type,
        file_hash=record.file_hash,
        outcome=outcome,
        document_id=record.document_id,
        bid_identifier="Bid",
    )


def test_complete_stage_order_and_identifier_propagation() -> None:
    record = _record()
    source = Source([record])
    scanner, parser, chunker, indexer = (
        Scanner([_item(record)]),
        Parser(),
        Chunker(),
        Indexer(),
    )
    pipeline = EndToEndPipeline(scanner, source, parser, chunker, indexer)
    summary = asyncio.run(pipeline.process_directory("Bid"))

    assert scanner.calls == [("Bid", None)]
    assert source.checkpoints == 1
    assert parser.calls == [record.document_id]
    assert chunker.calls == [(record.document_id, record.document_id)]
    assert indexer.calls == [record.document_id]
    result = summary.results[0]
    assert result.document_id == record.document_id
    assert result.bid_id == record.bid_id
    assert result.relative_path == "Bid/source.pdf"
    assert result.parsing == result.chunking == result.indexing == "completed"
    assert result.points_upserted == 1
    assert result.completed is True


def test_one_failed_document_does_not_stop_an_unrelated_document() -> None:
    bad, good = _record(name="bad.pdf"), _record(name="good.pdf")
    parser = Parser(failed={bad.document_id})
    indexer = Indexer()
    summary = asyncio.run(
        EndToEndPipeline(
            Scanner([_item(bad), _item(good)]),
            Source([bad, good]),
            parser,
            Chunker(),
            indexer,
        ).process_directory("Bid")
    )
    assert summary.documents_failed == 1
    assert summary.documents_completed == 1
    assert summary.results[0].parsing == "failed"
    assert summary.results[1].completed is True
    assert indexer.calls == [good.document_id]


def test_indexed_document_is_idempotently_skipped() -> None:
    record = _record(status="indexed", chunks=3)
    parser, chunker, indexer = Parser(), Chunker(), Indexer()
    result = asyncio.run(
        EndToEndPipeline(
            Scanner([]),
            Source([record]),
            parser,
            chunker,
            indexer,
        ).process_registered_document(record.document_id)
    )
    assert result.completed is True
    assert result.resumed_from == "indexed"
    assert result.chunks_skipped == 3
    assert parser.calls == chunker.calls == indexer.calls == []


def test_chunked_or_failed_indexing_resumes_at_index() -> None:
    record = _record(status="failed", chunks=2)
    parser, chunker, indexer = Parser(), Chunker(), Indexer()
    result = asyncio.run(
        EndToEndPipeline(
            Scanner([]),
            Source([record]),
            parser,
            chunker,
            indexer,
        ).process_registered_document(record.document_id)
    )
    assert result.resumed_from == "chunks"
    assert result.parsing == result.chunking == "skipped"
    assert result.indexing == "completed"
    assert indexer.calls == [record.document_id]
    assert parser.calls == chunker.calls == []


def test_changed_source_is_not_parsed_or_overwritten() -> None:
    record = _record(chunks=2)
    parser, chunker, indexer = Parser(), Chunker(), Indexer()
    result = asyncio.run(
        EndToEndPipeline(
            Scanner([]),
            Source([record], current=False),
            parser,
            chunker,
            indexer,
        ).process_registered_document(record.document_id)
    )
    assert "registered hash" in result.errors[0]
    assert parser.calls == chunker.calls == indexer.calls == []


def test_changed_and_unsupported_registration_outcomes_are_not_processed() -> None:
    record = _record()
    changed = _item(record, IngestionOutcome.CHANGED)
    unsupported = IngestionFileResult(
        relative_path="Bid/readme.txt",
        outcome=IngestionOutcome.UNSUPPORTED,
        message="file extension is not supported",
    )
    parser, indexer = Parser(), Indexer()
    summary = asyncio.run(
        EndToEndPipeline(
            Scanner([changed, unsupported]),
            Source([record]),
            parser,
            Chunker(),
            indexer,
        ).process_directory("Bid")
    )
    assert summary.documents_completed == 0
    assert summary.documents_failed == 2
    assert parser.calls == indexer.calls == []
