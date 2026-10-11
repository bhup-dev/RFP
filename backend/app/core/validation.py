"""Configuration checks that do not open network connections."""

from app.config import DEFAULT_DEVELOPMENT_DATABASE_URL, Settings, settings

SUPPORTED_PROVIDERS = ("openai",)


class ConfigurationError(ValueError):
    """Settings name an unsupported provider or an inconsistent value."""


def validate_configuration(settings_obj: Settings | None = None) -> None:
    """Reject unsupported providers before the server accepts traffic.

    Credential checks stay on the provider call. Qdrant collection size is
    compared when that service runs, including ``GET /health/ready``.
    """
    current = settings_obj or settings
    problems: list[str] = []
    _check_provider("LLM", current.llm_provider, problems)
    _check_provider("embedding", current.embedding_provider, problems)
    if current.embedding_dimension < 1:
        problems.append("EMBEDDING_DIMENSION must be a positive integer.")
    if (
        current.environment.strip().lower() == "production"
        and current.database_url == DEFAULT_DEVELOPMENT_DATABASE_URL
    ):
        problems.append(
            "DATABASE_URL must not use the development default in production."
        )
    if problems:
        raise ConfigurationError(" ".join(problems))


def _check_provider(kind: str, provider: str, problems: list[str]) -> None:
    if provider.strip().lower() not in SUPPORTED_PROVIDERS:
        supported = ", ".join(SUPPORTED_PROVIDERS)
        problems.append(
            f"Unsupported {kind} provider {provider!r}. Supported providers: {supported}."
        )
