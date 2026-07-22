"""LLM-based prose extraction (spec §4.2, Phase 6; ADR-0018). The only
module that turns a docx document's free-text prose (`LoadedDoc.content`)
into entities/relationships — table rows remain the regex engine's job
(`pipeline/rules/engine.py`). Reaches Ollama exclusively through
`app.llm.ollama_client` (ADR-0019); never imports `httpx` or any other
LLM API directly.

An unreachable/timed-out Ollama or an unusable response (`OllamaError`
from `ollama_client`) or an unparsable model response (`_parse_json_object`)
is re-raised as `ProseExtractionError` — the one exception type
`DocxSourceAdapter.extract()` catches to degrade this doc to partial
success (regex output still written, a warning surfaced) instead of
failing it outright (ADR-0022, issue #20).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.llm import ollama_client
from app.pipeline.rules.schema import ProseExtraction
from app.pipeline.types import Entity, Relationship

# Bump whenever _PROMPT_TEMPLATE's wording changes meaningfully enough that
# previously-stored LLM-derived extractions should be treated as stale and
# re-extracted (ADR-0020) — compared alongside content_hash and the
# configured model name via `ExtractionVersion` (`pipeline/types.py`).
PROMPT_VERSION = "1"


class ProseExtractionError(Exception):
    """The LLM call or its response couldn't be turned into entities/
    relationships — Ollama unreachable/timed out (wrapping `OllamaError`)
    or a response that isn't the expected JSON shape (ADR-0022)."""


_PROMPT_TEMPLATE = """\
You are extracting structured entities and relationships from a document's \
prose text for a knowledge graph.

Only extract entities of these types: {entity_types}
Only extract relationships of these types: {relationship_types}

For each entity, give its type (one of the allowed entity types above, \
exactly as written) and a short, specific name.
For each relationship, give its type (one of the allowed relationship types \
above, exactly as written) and the exact names of the two entities it \
connects — both names must also appear in your entities list.

Respond with ONLY a JSON object of this exact shape, no prose, no markdown \
fences:
{{"entities": [{{"type": "...", "name": "..."}}], "relationships": \
[{{"type": "...", "from": "...", "to": "..."}}]}}

If nothing matches, respond with {{"entities": [], "relationships": []}}.

Document text:
\"\"\"
{content}
\"\"\"
"""


def _build_prompt(content: str, prose_extraction: ProseExtraction) -> str:
    return _PROMPT_TEMPLATE.format(
        entity_types=", ".join(prose_extraction.target_entity_types),
        relationship_types=", ".join(prose_extraction.target_relationship_types),
        content=content,
    )


def _parse_json_object(raw: str) -> dict[str, Any]:
    """LLMs routinely wrap JSON in markdown fences or add stray words
    around it even when explicitly told not to — take the outermost
    `{...}` span rather than requiring the whole response to be clean
    JSON."""
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON object found in LLM response: {raw!r}")

    parsed: dict[str, Any] = json.loads(raw[start : end + 1])
    return parsed


def _node_id(entity_type: str, doc_id: str, name: str) -> str:
    return hashlib.sha1(f"{entity_type}::{doc_id}::{name}".encode()).hexdigest()[:16]


def extract_prose_entities(
    content: str,
    prose_extraction: ProseExtraction,
    *,
    doc_id: str,
    source_file: str,
) -> tuple[tuple[Entity, ...], tuple[Relationship, ...]]:
    """Run the local LLM over `content` and return the entities/relationships
    it found, scoped to `prose_extraction.target_entity_types`/
    `target_relationship_types` and tagged `rule_id=prose_extraction.id`
    (ADR-0018) — the shape `DocxSourceAdapter.extract()` merges alongside
    the regex engine's own output.

    Skips the LLM call entirely for blank content (nothing to extract).

    Raises `ProseExtractionError` if Ollama is unreachable/times out or its
    response can't be parsed as the expected JSON shape (ADR-0022) — the
    caller decides how to degrade, this function just fails loudly rather
    than returning a silently-empty result that looks like "nothing found".
    """
    if not content.strip():
        return (), ()

    allowed_entity_types = set(prose_extraction.target_entity_types)
    allowed_relationship_types = set(prose_extraction.target_relationship_types)

    try:
        raw_response = ollama_client.generate(_build_prompt(content, prose_extraction))
        parsed = _parse_json_object(raw_response)
    except (ollama_client.OllamaError, ValueError) as exc:
        raise ProseExtractionError(str(exc)) from exc

    entities: list[Entity] = []
    node_id_by_name: dict[str, str] = {}

    for raw_entity in parsed.get("entities", []):
        entity_type = raw_entity.get("type")
        name = str(raw_entity.get("name", "")).strip()
        if not name or entity_type not in allowed_entity_types:
            continue

        node_id = _node_id(entity_type, doc_id, name)
        node_id_by_name[name] = node_id
        entities.append(
            Entity(
                id=node_id,
                type=entity_type,
                name=name,
                origin="extracted",
                rule_id=prose_extraction.id,
                properties={"source_file": source_file},
            )
        )

    relationships: list[Relationship] = []
    for raw_rel in parsed.get("relationships", []):
        rel_type = raw_rel.get("type")
        from_name = str(raw_rel.get("from", "")).strip()
        to_name = str(raw_rel.get("to", "")).strip()

        if rel_type not in allowed_relationship_types:
            continue
        if from_name not in node_id_by_name or to_name not in node_id_by_name:
            continue

        relationships.append(
            Relationship(
                from_id=node_id_by_name[from_name],
                to_id=node_id_by_name[to_name],
                type=rel_type,
                origin="extracted",
                rule_id=prose_extraction.id,
            )
        )

    return tuple(entities), tuple(relationships)
