"""Provider settings and startup behavior. No API key or network call is required."""

import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app


def test_provider_environment_variables_load(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-config-secret")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-large")
    monkeypatch.setenv("EMBEDDING_DIMENSION", "3072")

    loaded = Settings(_env_file=None)

    assert loaded.llm_provider == "openai"
    assert loaded.llm_model == "gpt-4o-mini"
    assert loaded.embedding_provider == "openai"
    assert loaded.embedding_model == "text-embedding-3-large"
    assert loaded.embedding_dimension == 3072
    assert loaded.llm_model != loaded.embedding_model
    assert "sk-config-secret" not in repr(loaded)


def test_blank_openai_api_key_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    loaded = Settings(_env_file=None)
    assert loaded.openai_api_key is None
    assert "OPENAI_API_KEY" not in repr(loaded)


def test_health_does_not_construct_an_openai_client(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.embedding_service as embedding
    import app.services.llm_service as llm

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("OpenAI client was constructed")

    monkeypatch.setattr(llm, "AsyncOpenAI", fail_if_called)
    monkeypatch.setattr(embedding, "AsyncOpenAI", fail_if_called)
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_application_imports_without_an_api_key_or_network() -> None:
    env = os.environ.copy()
    env["OPENAI_API_KEY"] = ""
    env.pop("OPENAI_BASE_URL", None)
    script = """
import socket

def fail(*_args, **_kwargs):
    raise AssertionError("network connection attempted during import")

socket.create_connection = fail
from app.main import app
from app.services.embedding_service import EmbeddingService
from app.services.llm_service import LLMService
LLMService()
EmbeddingService()
print(app.title)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=os.path.join(os.path.dirname(__file__), ".."),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "RFP Intelligence Platform" in result.stdout
