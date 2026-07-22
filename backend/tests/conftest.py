import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.llm import ollama_client  # noqa: E402


def mock_ollama_generate(monkeypatch, response_text: str) -> list[str]:
    """Stub `ollama_client.generate` to return `response_text`, recording
    every prompt it was called with. Shared by the prose-extraction tests
    (`test_prose_llm.py`, `test_docx_prose_extraction.py`) so the mocking
    shape doesn't drift between the unit and integration levels."""
    calls: list[str] = []

    def fake_generate(prompt: str, *, client=None) -> str:
        calls.append(prompt)
        return response_text

    monkeypatch.setattr(ollama_client, "generate", fake_generate)
    return calls
