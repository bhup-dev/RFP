"""Coordinate discovery, hashing, and metadata registration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from app.ingestion.discovery import discover_files
from app.ingestion.exceptions import IngestionError
from app.ingestion.file_utils import file_sha256, file_size, public_input_label, resolve_within_root
from app.ingestion.parse_stage import DocumentParseStage
from app.ingestion.parsers.base import RegisteredSource
from app.ingestion.registry import (
    CHANGED,
    DUPLICATE,
    REGISTERED,
    RegistrationDecision,
    grouping_identifier,
)
from app.schemas.ingestion import IngestionFileResult, IngestionOutcome, IngestionSummary
from app.schemas.parsing import ParsedDocument

logger = logging.getLogger(__name__)


class DocumentRegistrar(Protocol):
    """Persistence used by the pipeline. Tests can supply an in-memory stand-in."""

    async def register(
        self,
        *,
        bid_identifier: str,
        relative_path: str,
        file_name: str,
        document_type: str,
        file_hash: str,
    ) -> RegistrationDecision:
        """Insert metadata or report that the file is already known."""


class IngestionPipeline:
    """Register source files, and parse registered files on request.

    Scanning stores metadata only. ``parse_registered`` reads a file and returns
    text in memory. It does not chunk, embed, or change the registered row.
    """

    def __init__(self, registrar: DocumentRegistrar, input_root: Path) -> None:
        self._registrar = registrar
        self._input_root = input_root

    def parse_registered(self, source: RegisteredSource) -> ParsedDocument:
        """Parse one registered document. The registered metadata is not updated."""
        return DocumentParseStage(self._input_root).parse(source)

    def parse_registered_many(self, sources: list[RegisteredSource]) -> list[ParsedDocument]:
        """Parse each registered document. One failure does not stop the others."""
        return DocumentParseStage(self._input_root).parse_many(sources)

    async def scan(
        self,
        relative_directory: str | None = None,
        *,
        bid_identifier: str | None = None,
    ) -> IngestionSummary:
        if bid_identifier is not None and bid_identifier.strip():
            grouping_identifier("file.pdf", override=bid_identifier, input_root_name="root")
        scan_root = resolve_within_root(self._input_root, relative_directory)
        root = self._input_root.expanduser().resolve()
        discovered = discover_files(root, scan_root)
        results: list[IngestionFileResult] = [
            _failure(item.relative_path, item.problem or "directory could not be read")
            for item in discovered.errors
        ]
        for item in discovered.files:
            if item.problem:
                results.append(_failure(item.relative_path, item.problem, item.document_type))
                continue
            if not item.supported or item.document_type is None:
                results.append(
                    IngestionFileResult(
                        relative_path=item.relative_path,
                        document_type=None,
                        outcome=IngestionOutcome.UNSUPPORTED,
                        message="file extension is not supported",
                    )
                )
                continue
            results.append(
                await self._register_file(
                    root,
                    item.relative_path,
                    item.path,
                    item.document_type,
                    bid_identifier,
                )
            )

        results.sort(key=lambda item: item.relative_path)
        summary = _summarize(public_input_label(root), results)
        logger.info(
            "Ingestion scan complete discovered=%s registered=%s duplicates=%s changed=%s unsupported=%s failed=%s",
            summary.files_discovered,
            summary.files_registered,
            summary.duplicates_skipped,
            summary.changed_files,
            summary.unsupported_files,
            summary.failed_files,
        )
        return summary

    async def _register_file(
        self,
        root: Path,
        relative_path: str,
        path: Path,
        document_type: str,
        bid_identifier: str | None,
    ) -> IngestionFileResult:
        digest: str | None = None
        size: int | None = None
        try:
            digest = file_sha256(path)
            size = file_size(path)
            grouping = grouping_identifier(
                relative_path,
                override=bid_identifier,
                input_root_name=root.name,
            )
            decision = await self._registrar.register(
                bid_identifier=grouping,
                relative_path=relative_path,
                file_name=Path(relative_path).name,
                document_type=document_type,
                file_hash=digest,
            )
        except IngestionError as exc:
            logger.error("Ingestion failed for %s (%s)", relative_path, type(exc).__name__)
            return _failure(relative_path, str(exc), document_type, digest, size)
        except Exception:
            logger.error("Ingestion failed for %s (%s)", relative_path, "database")
            return _failure(
                relative_path,
                "database rejected the registration",
                document_type,
                digest,
                size,
            )

        return IngestionFileResult(
            relative_path=relative_path,
            document_type=document_type,
            file_hash=digest,
            size_bytes=size,
            outcome=IngestionOutcome(decision.outcome),
            document_id=decision.document_id,
            bid_identifier=decision.bid_identifier,
            message=_message(decision.outcome),
        )


def _message(outcome: str) -> str:
    if outcome == REGISTERED:
        return "metadata registered; document is not parsed"
    if outcome == DUPLICATE:
        return "exact content is already registered"
    if outcome == CHANGED:
        return "file content changed and the existing registration was left unchanged"
    return outcome


def _failure(
    relative_path: str,
    message: str,
    document_type: str | None = None,
    file_hash: str | None = None,
    size_bytes: int | None = None,
) -> IngestionFileResult:
    return IngestionFileResult(
        relative_path=relative_path,
        document_type=document_type,
        file_hash=file_hash,
        size_bytes=size_bytes,
        outcome=IngestionOutcome.FAILED,
        message=message,
    )


def _summarize(input_root: str, results: list[IngestionFileResult]) -> IngestionSummary:
    def count(outcome: IngestionOutcome) -> int:
        return sum(1 for item in results if item.outcome == outcome)

    return IngestionSummary(
        input_root=input_root,
        files_discovered=len(results),
        files_registered=count(IngestionOutcome.REGISTERED),
        duplicates_skipped=count(IngestionOutcome.DUPLICATE),
        changed_files=count(IngestionOutcome.CHANGED),
        unsupported_files=count(IngestionOutcome.UNSUPPORTED),
        failed_files=count(IngestionOutcome.FAILED),
        results=results,
    )
