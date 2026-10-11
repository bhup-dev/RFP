"""Request and operation identifiers stored in context variables.

A request id is set by middleware and cleared when the request finishes.
An operation id can be bound when a later task starts a long-running workflow.
"""

from __future__ import annotations

import re
from contextvars import ContextVar, Token
from uuid import uuid4

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_operation_id: ContextVar[str | None] = ContextVar("operation_id", default=None)


def get_request_id() -> str | None:
    return _request_id.get()


def get_operation_id() -> str | None:
    return _operation_id.get()


def normalize_request_id(value: str | None) -> str:
    """Keep a caller-supplied id when it is a short safe token. Otherwise mint one."""
    if value is not None:
        candidate = value.strip()
        if _REQUEST_ID.fullmatch(candidate):
            return candidate
    return str(uuid4())


def set_request_id(value: str | None) -> Token[str | None]:
    return _request_id.set(normalize_request_id(value) if value else str(uuid4()))


def reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)


def bind_operation_id(value: str | None = None) -> Token[str | None]:
    """Attach an operation id to the current context. The caller resets the token."""
    operation_id = normalize_request_id(value) if value else str(uuid4())
    return _operation_id.set(operation_id)


def reset_operation_id(token: Token[str | None]) -> None:
    _operation_id.reset(token)


def set_operation_id(value: str | None) -> Token[str | None]:
    return _operation_id.set(value)


def clear_operation_id(token: Token[str | None]) -> None:
    _operation_id.reset(token)
