"""Errors raised by LLM and embedding providers.

Messages are safe to log. They omit API keys and request text.
"""

from openai import APITimeoutError, AuthenticationError, OpenAIError, RateLimitError


class ProviderConfigurationError(RuntimeError):
    """The provider name or credentials cannot be used for a request."""


class ProviderRequestError(RuntimeError):
    """The provider rejected or failed a request."""


def describe_provider_error(exc: OpenAIError, operation: str) -> ProviderRequestError:
    """Build a public error from an SDK exception without copying its message."""
    status = getattr(exc, "status_code", None)
    if isinstance(exc, APITimeoutError):
        detail = f"{operation} timed out"
    elif isinstance(exc, RateLimitError):
        detail = f"{operation} was rate limited"
    elif isinstance(exc, AuthenticationError):
        detail = f"{operation} was rejected. Check OPENAI_API_KEY."
    else:
        detail = f"{operation} failed ({type(exc).__name__})"
        if isinstance(status, int):
            detail = f"{detail}, HTTP {status}"
    return ProviderRequestError(detail)
