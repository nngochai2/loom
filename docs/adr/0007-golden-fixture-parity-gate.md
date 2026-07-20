# 0007 — Phase 1 gate uses a golden-fixture parity test, not an F1 benchmark

## Status
Accepted

## Context
The original Phase 1 gate (spec §10) required: "extraction quality on the NAA validation set is within tolerance of pre-lift scores (Entity F1 > 75%, Relationship F1 > 65%, Coverage > 60%)." Per ADR-0001, Loom lifts deterministic regex-based extraction, not probabilistic LLM extraction — there is no meaningful F1 to compute, and no such validation dataset or benchmark harness exists in NAA to begin with (§13 item 3 is moot).

## Decision
Replace the benchmark-check gate with a golden-fixture parity test: run NAA's current parser and Loom's ported adapter against the same fixture vault/docx inputs, and assert the extracted nodes/edges match (exactly, or against a recorded snapshot). This proves the port preserved NAA's behavior — the correct bar for a lift, not a rewrite.

## Consequences
- Building this fixture requires capturing NAA's actual current output for the chosen fixture vault/docx as the snapshot to diff against, before porting begins.
- Spec §13 item 3 ("Location of the extraction validation dataset") is resolved: no such dataset is needed; a golden-fixture snapshot is generated from NAA directly instead.
- If Loom later adds genuinely new extraction behavior beyond what NAA does, that behavior needs its own test — this gate only proves parity with the source being lifted.
