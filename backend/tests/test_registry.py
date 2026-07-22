"""Tests for `EXTRACTION_VERSION` (ADR-0020, issue #19): the registry entry
that turns an already-loaded source config into this run's LLM prompt/model
fingerprint, or `None` where that concept doesn't apply. `SOURCES`/`SINKS`
are pre-existing and already exercised indirectly via `test_cli.py`/
`test_jobs_runner.py`; this file only covers the new mapping.
"""

import dataclasses
from pathlib import Path

from app.pipeline.extraction.prose_llm import PROMPT_VERSION
from app.pipeline.registry import EXTRACTION_VERSION
from app.pipeline.rules.schema import ProseExtraction, RuleContext, load_rule_file
from app.pipeline.types import ExtractionVersion

RULE_PATH = str(Path(__file__).parent / "fixtures" / "br_requirements.yml")


def test_obsidian_never_has_an_extraction_version():
    assert EXTRACTION_VERSION["obsidian"](object()) is None


def test_docx_with_prose_extraction_disabled_has_no_extraction_version():
    rule_file = load_rule_file(RULE_PATH)
    assert rule_file.context.prose_extraction.enabled is False

    assert EXTRACTION_VERSION["docx"](rule_file) is None


def test_docx_with_prose_extraction_enabled_returns_the_prompt_version_and_configured_model(
    monkeypatch,
):
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.1")
    rule_file = dataclasses.replace(
        load_rule_file(RULE_PATH),
        context=RuleContext(
            prose_extraction=ProseExtraction(enabled=True, id="pe-1", target_entity_types=("TASK",))
        ),
    )

    result = EXTRACTION_VERSION["docx"](rule_file)

    assert result == ExtractionVersion(prompt_version=PROMPT_VERSION, model="llama3.1")
