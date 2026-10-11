"""Application logging. Handlers are attached from the process lifespan."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.config import Settings, settings
from app.core.context import get_operation_id, get_request_id
from app.core.redaction import redact_text

HANDLER_NAME = "rfp.intelligence"


class ContextFilter(logging.Filter):
    """Add correlation ids and strip credentials from the formatted message."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        record.operation_id = get_operation_id() or "-"
        message = record.getMessage()
        redacted = redact_text(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
            record.message = redacted
        return True


class TextFormatter(logging.Formatter):
    """Single-line logs for local development."""

    def __init__(self, *, debug: bool) -> None:
        fmt = (
            "%(asctime)s %(levelname)s %(name)s "
            "request_id=%(request_id)s operation_id=%(operation_id)s %(message)s"
        )
        if debug:
            fmt += " %(pathname)s:%(lineno)d"
        super().__init__(fmt)

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return datetime.fromtimestamp(record.created, UTC).isoformat()


class JsonFormatter(logging.Formatter):
    """One JSON object per line for production log collectors."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        request_id = getattr(record, "request_id", None)
        operation_id = getattr(record, "operation_id", None)
        if request_id and request_id != "-":
            payload["request_id"] = request_id
        if operation_id and operation_id != "-":
            payload["operation_id"] = operation_id
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(settings_obj: Settings | None = None) -> None:
    """Attach one application handler. Repeated calls update it in place."""
    current = settings_obj or settings
    level = getattr(logging, current.log_level)
    handler = _application_handler()
    handler.setLevel(level)
    handler.setFormatter(_formatter(current))
    if not any(isinstance(item, ContextFilter) for item in handler.filters):
        handler.addFilter(ContextFilter())

    root = logging.getLogger()
    root.setLevel(level)
    if handler not in root.handlers:
        root.addHandler(handler)


def _application_handler() -> logging.Handler:
    for handler in logging.getLogger().handlers:
        if getattr(handler, "name", None) == HANDLER_NAME:
            return handler
    handler = logging.StreamHandler(sys.stderr)
    handler.name = HANDLER_NAME
    return handler


def _formatter(current: Settings) -> logging.Formatter:
    if current.log_format == "json":
        return JsonFormatter()
    return TextFormatter(debug=current.debug)
