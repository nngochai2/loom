# 0005 — Rule IDs are a generated field, independent of the editable name

## Status
Accepted

## Context
Spec §6.4/§7 requires rule IDs to be stable across edits: "Renaming a rule keeps its id; deleting and recreating produces a new id" — because `rule_id` is the join key for per-rule correction-rate analytics.

NAA's real rule YAML (`parsing-rules/br_requirements.yml`) has no such field. Each `category_signal`/`named_extraction` only has a human-editable `name` (e.g. `"SQLView"`, `"views"`). Using `name` as the stable id (as NAA does implicitly) would silently orphan correction history the moment someone renames a rule for clarity.

## Decision
Every category-signal/named-extraction entry gets an explicit, generated `id` field (short slug or UUID), assigned once when the rule is created and never changed by the Rules page form — independent of its editable `name`/display label. `corrections.originating_rule_id` (§6.4) references this `id`, never the `name`.

## Consequences
- The rule-file JSON Schema (`pipeline/rules/schema.py`) requires an `id` alongside `name` for every rule entry.
- The Rules page form must generate an `id` on create and treat it as read-only thereafter (renaming the rule edits `name` only).
- Migrating NAA's existing `br_requirements.yml`-shaped rule content into Loom requires backfilling `id`s that don't currently exist.
