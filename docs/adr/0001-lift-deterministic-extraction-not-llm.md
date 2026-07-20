# 0001 — Lift NAA's deterministic extraction; no LLM-based entity/relationship extraction

## Status
Accepted

## Context
The original loom-spec (§1) assumed NAA's extraction logic was LLM-based: "Obsidian ingestion — uses Obsidian CLI," "docx extraction — LlamaIndex-based entity/relationship extraction," validated against "Entity F1 > 75%, Relationship F1 > 65%, Coverage > 60%, per Stanford KGGen/MINE-1 methodology."

Direct inspection of `NAA/pipeline/src/parser.py` and `NAA/webapp/src/docx_generic_parser.py` found none of this. There is no Obsidian CLI invocation anywhere in NAA — Obsidian notes are parsed with hand-rolled regex (wikilinks, a custom non-YAML header format). Docx extraction (`DocxRuleParser`) reads a YAML rule file and applies regex patterns to table rows — no LlamaIndex, no LLM SDK calls anywhere in `pipeline/`, `webapp/`, or `mcp/`. No benchmark/eval harness matching the F1/KGGen/MINE-1 claim exists in the repo.

## Decision
Loom lifts NAA's real, deterministic, regex/rule-based extraction as-is: the wikilink-context parser for Obsidian, and `DocxRuleParser` (YAML-rule-driven regex over docx tables) for docx. Loom does not build LLM-based entity/relationship extraction. The F1/benchmark language in the spec is dropped as inapplicable to deterministic pattern matching.

## Consequences
- Extraction quality is verified by exact-match/golden-fixture tests (see ADR-0007), not statistical F1 scoring.
- The `pipeline/rules/schema.py` JSON Schema must model the real rule-file shape (`id_pattern`, `category_signals`, `named_extractions`, etc. — see [[rule-file]] in CONTEXT.md), not "chunking parameters" or "prompts."
- If genuine LLM-based extraction is wanted later, it is new scope requiring its own spec change, not an extension of "lift, don't rewrite."
