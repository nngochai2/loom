"""The one door to the local LLM (Ollama, ADR-0019). All prose-extraction
calls (Phase 6) go through this module — no other module may call Ollama
or any other LLM API, and no document content ever leaves the machine.

Endpoint/model come from the environment (OLLAMA_BASE_URL / OLLAMA_MODEL)
and are never hardcoded, the same convention `db/neo4j_client.py` uses for
Neo4j credentials (spec §6.5).
"""

from __future__ import annotations

import os

import httpx

_client: httpx.Client | None = None

# httpx's default timeout (5s) is tuned for typical HTTP APIs, not local LLM
# generation, which routinely takes well beyond that for non-trivial prompts.
_TIMEOUT_SECONDS = 120.0


def get_model_name() -> str:
    """Return the configured model name without making a call — the
    re-extraction trigger (ADR-0020) needs it to build a run's
    `ExtractionVersion` before any document is processed."""
    return os.environ["OLLAMA_MODEL"]


def get_client() -> httpx.Client:
    """Return the process-wide HTTP client, creating it on first use."""
    global _client
    if _client is None:
        base_url = os.environ["OLLAMA_BASE_URL"]
        _client = httpx.Client(base_url=base_url, timeout=_TIMEOUT_SECONDS)
    return _client


def close_client() -> None:
    """Close and discard the process-wide client, if one was created."""
    global _client
    if _client is not None:
        _client.close()
        _client = None


def generate(prompt: str, *, client: httpx.Client | None = None) -> str:
    """Send a prompt to the configured local Ollama model and return its
    text response. The only entry point into Ollama in this codebase.

    `client` is an optional injection seam for tests; real callers omit it
    and get the process-wide singleton.
    """
    model = os.environ["OLLAMA_MODEL"]
    http_client = client if client is not None else get_client()

    response = http_client.post(
        "/api/generate",
        json={"model": model, "prompt": prompt, "stream": False},
    )
    response.raise_for_status()

    text: str = response.json()["response"]
    return text
