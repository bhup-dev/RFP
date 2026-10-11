"""Parse registered documents. Results stay in memory and are not written to PostgreSQL."""

from __future__ import annotations

import logging
from pathlib import Path

from app.ingestion.exceptions import IngestionError, UnsupportedDocumentTypeError
from app.ingestion.file_utils import file_sha256, resolve_registered_file
from app.ingestion.parsers.base import RegisteredSource, failed_document
from app.ingestion.parsers.factory import parser_for
from app.schemas.parsing import ParseMessage, ParsedDocument

logger = logging.getLogger(__name__)


class DocumentParseStage:
    """Resolve a registered file and parse it. The source file is only read."""

    def __init__(self, input_root: Path) -> None:
        self._input_root = input_root

    def parse(self, source: RegisteredSource) -> ParsedDocument:
        try:
            result = self._parse(source)
        except Exception as exc:
            logger.error(
                "Parse failed for document_id=%s path=%s (%s)",
                source.document_id,
                source.relative_path,
                type(exc).__name__,
            )
            result = failed_document(source, code="parse_error", message="document could not be parsed")
        logger.info(
            "Parsed document_id=%s path=%s status=%s warnings=%s errors=%s",
            source.document_id,
            source.relative_path,
            result.status.value,
            len(result.warnings),
            len(result.errors),
        )
        return result

    def parse_many(self, sources: list[RegisteredSource]) -> list[ParsedDocument]:
        """Parse each document. One failure does not stop the rest."""
        return [self.parse(source) for source in sources]

    def _parse(self, source: RegisteredSource) -> ParsedDocument:
        if not source.document_type or not source.document_type.strip():
            return failed_document(source, code="unsupported", message="document type is not supported")
        try:
            parser = parser_for(source.document_type)
        except UnsupportedDocumentTypeError as exc:
            return failed_document(source, code="unsupported", message=str(exc))
        try:
            path = resolve_registered_file(self._input_root, source.relative_path)
        except IngestionError as exc:
            return failed_document(
                source,
                code="unavailable",
                message=str(exc),
                parser_name=parser.name,
                parser_version=parser.version,
            )
        parsed = parser.parse(path, source)
        if source.file_hash:
            current = file_sha256(path)
            if current != source.file_hash:
                parsed.warnings.insert(
                    0,
                    ParseMessage(
                        code="content_changed",
                        message="file content differs from the registered hash",
                    ),
                )
        return parsed
