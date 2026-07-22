"""Manual smoke check for the local Ollama connection (ADR-0019).

Sends one prompt through `app.llm.ollama_client.generate` to a real,
running Ollama instance and prints the response. NOT part of the pytest
suite (`test_ollama_client.py` covers `ollama_client`'s behavior against a
mocked transport) — this script is for confirming an actual local Ollama
install/model is reachable and configured correctly.

Usage (from `backend/`, with `OLLAMA_BASE_URL`/`OLLAMA_MODEL` set — e.g.
via `.env`, or after `docker-compose up ollama` and `ollama pull <model>`):

    python scripts/check_ollama_connection.py
"""

from __future__ import annotations

import os
import sys

from app.llm import ollama_client


def main() -> None:
    base_url = os.environ.get("OLLAMA_BASE_URL", "<unset>")
    model = os.environ.get("OLLAMA_MODEL", "<unset>")
    print(f"Sending a test prompt to {base_url} (model={model})...")

    try:
        response = ollama_client.generate("Reply with the single word: pong")
    except Exception as exc:  # noqa: BLE001 - manual script, want the raw error
        print(f"FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"Response: {response!r}")


if __name__ == "__main__":
    main()
