# 0004 — Vault classification rules live in per-config YAML, not hardcoded Python

## Status
Accepted

## Context
NAA hardcodes vault-specific classification logic in `pipeline/src/config.py`: folder→type mappings (`"java"` → `CONVENTION`, `"mulesoft"` → `ARCHITECTURE`, `"eavesdrop"` → `NOTE`, …), fallback keyword scoring (`TYPE_SIGNALS`), relationship-inference keywords (`REL_KEYWORDS`), and which vault folders are even scanned (`INCLUDE_FOLDERS = {"6 - Main Notes/Project"}`, `TAGS_FOLDER = "3 - Tags"`). These are specific to the user's personal vault's folder layout, not generic to any Obsidian vault.

Loom's whole premise (spec §7) is that parsing behavior is driven by an editable, previewable YAML config, not hardcoded Python.

## Decision
`SUBFOLDER_TYPE_MAP`, `TYPE_SIGNALS`, `REL_KEYWORDS`, and the include-folder list all move into the per-config YAML, editable via the Rules page form with live preview (spec §7, §9). Nothing vault-layout-specific ships hardcoded in the Obsidian source adapter.

## Consequences
- The docx rule-file JSON Schema (`pipeline/rules/schema.py`) gains an Obsidian-source-specific counterpart (or a shared schema with per-source-type sections) covering folder-to-type mapping, keyword signals, and relationship keywords — this is what spec §7's "per-source-type overrides" concretely means.
- A default config shipped with Loom would need its own folder/keyword values (not necessarily NAA's) — TBD when the default config is authored; not blocking for Phase 1.
