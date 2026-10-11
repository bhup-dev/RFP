"""ASGI middleware that assigns a request id for the life of one HTTP request."""

import logging
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from app.core.context import (
    REQUEST_ID_HEADER,
    clear_operation_id,
    normalize_request_id,
    reset_request_id,
    set_operation_id,
    set_request_id,
)

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

logger = logging.getLogger(__name__)
_ERROR_BODY = b'{"detail":"Internal server error"}'


class RequestContextMiddleware:
    """Copy a valid X-Request-ID or generate one, then clear the context afterward."""

    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        incoming = _header(scope, REQUEST_ID_HEADER)
        request_id = normalize_request_id(incoming)
        request_token = set_request_id(request_id)
        operation_token = set_operation_id(None)
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                headers = list(message.get("headers") or [])
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as exc:
            if not response_started:
                logger.error(
                    "Unhandled request error (%s) request_id=%s",
                    type(exc).__name__,
                    request_id,
                )
                await send_with_request_id(
                    {
                        "type": "http.response.start",
                        "status": 500,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(_ERROR_BODY)).encode("ascii")),
                        ],
                    }
                )
                await send_with_request_id(
                    {"type": "http.response.body", "body": _ERROR_BODY}
                )
            raise
        finally:
            reset_request_id(request_token)
            clear_operation_id(operation_token)


def _header(scope: Scope, name: str) -> str | None:
    target = name.lower().encode("ascii")
    for key, value in scope.get("headers") or []:
        if key.lower() == target:
            return value.decode("latin-1")
    return None
