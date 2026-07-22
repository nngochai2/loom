import ast
import json
from pathlib import Path

import httpx
import pytest

from app.llm import ollama_client

APP_ROOT = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture(autouse=True)
def _reset_client_singleton():
    ollama_client.close_client()
    yield
    ollama_client.close_client()


@pytest.fixture()
def ollama_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.1")


def test_get_client_reads_base_url_from_env(ollama_env):
    client = ollama_client.get_client()

    assert str(client.base_url) == "http://localhost:11434"


def test_get_client_uses_a_generous_timeout_not_httpxs_default(ollama_env):
    client = ollama_client.get_client()

    # httpx's 5s default is tuned for typical HTTP APIs, not local LLM
    # generation — a real call would routinely blow past it.
    assert client.timeout.read == ollama_client._TIMEOUT_SECONDS
    assert client.timeout.read > 5.0


def test_get_client_is_a_singleton(ollama_env):
    first = ollama_client.get_client()
    second = ollama_client.get_client()

    assert first is second


def test_close_client_closes_and_clears_singleton(ollama_env):
    client = ollama_client.get_client()
    ollama_client.close_client()

    assert client.is_closed


def test_get_client_raises_if_base_url_missing(monkeypatch):
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    with pytest.raises(KeyError):
        ollama_client.get_client()


def _fake_client(handler) -> httpx.Client:
    return httpx.Client(base_url="http://localhost:11434", transport=httpx.MockTransport(handler))


def test_generate_posts_prompt_to_configured_model_and_returns_response_text(ollama_env):
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json={"response": "hello from ollama"})

    result = ollama_client.generate("say hi", client=_fake_client(handler))

    assert result == "hello from ollama"
    assert captured["url"] == "http://localhost:11434/api/generate"
    assert captured["json"] == {"model": "llama3.1", "prompt": "say hi", "stream": False}


def test_generate_raises_ollama_error_on_http_error_status(ollama_env):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(ollama_client.OllamaError):
        ollama_client.generate("say hi", client=_fake_client(handler))


def test_generate_raises_ollama_error_on_connection_failure(ollama_env):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(ollama_client.OllamaError):
        ollama_client.generate("say hi", client=_fake_client(handler))


def test_generate_raises_ollama_error_when_response_is_missing_the_expected_field(ollama_env):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    with pytest.raises(ollama_client.OllamaError):
        ollama_client.generate("say hi", client=_fake_client(handler))


def test_generate_uses_singleton_client_when_none_passed(monkeypatch, ollama_env):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": "ok"})

    monkeypatch.setattr(ollama_client, "get_client", lambda: _fake_client(handler))

    assert ollama_client.generate("say hi") == "ok"


def _modules_importing_httpx() -> list[Path]:
    offenders = []
    for path in APP_ROOT.rglob("*.py"):
        if path == APP_ROOT / "llm" / "ollama_client.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                alias.name == "httpx" or alias.name.startswith("httpx.") for alias in node.names
            ):
                offenders.append(path)
            elif isinstance(node, ast.ImportFrom) and node.module and (
                node.module == "httpx" or node.module.startswith("httpx.")
            ):
                offenders.append(path)
    return offenders


def test_no_module_outside_ollama_client_imports_httpx():
    offenders = _modules_importing_httpx()
    assert offenders == [], (
        f"Only llm/ollama_client.py may import httpx to reach an LLM (ADR-0019); "
        f"found imports in: {offenders}"
    )
