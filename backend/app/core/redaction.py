"""Remove credentials from messages that may be logged or returned to clients."""

import re

_URL_PASSWORD = re.compile(r"(://[^:/\s]+:)([^@\s]+)@")


def redact_database_url(url: str) -> str:
    """Hide the password in a SQLAlchemy URL and keep the driver and host."""
    return _URL_PASSWORD.sub(r"\1***@", url)


def redact_text(message: str) -> str:
    """Replace known secrets and URL passwords. The input is not written back."""
    from app.config import settings

    redacted = _URL_PASSWORD.sub(r"\1***@", message)
    secrets = (settings.openai_api_key, settings.qdrant_api_key, settings.database_url)
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "***")
    return redacted
