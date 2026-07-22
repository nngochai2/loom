# 0021 — Recall-oriented (subset-assertion) test gate for LLM-derived extraction

## Status
Accepted

## Context
ADR-0007's golden-fixture parity gate asserts exact-match equality between Loom's adapter output and NAA's original parser — valid only because regex extraction is deterministic. ADR-0018's LLM path is not: even at low temperature, wording, ordering, or the exact set of extracted items can vary between runs. An exact-match gate applied to this path would be flaky by construction, not a meaningful correctness signal.

## Decision
The LLM prose-extraction path gets its own test gate, different in kind from ADR-0007's: a small hand-curated fixture (a prose passage plus a "must contain" list of entities/relationships known to be extractable from it) is asserted as a **subset check** against real output — not exact match. The LLM may produce additional or differently-worded items beyond the must-find list without failing the test. The test fails only if a known-extractable item is missing, or extraction errors/times out/returns nothing.

## Consequences
- This gate is deliberately tolerant of LLM variance, so it does not certify precision — it never asserts the model *didn't* hallucinate something extra. Precision control leans on the existing Graph-page human correction workflow (§6), which was already designed to absorb imperfect `origin: extracted` items regardless of which mechanism produced them.
- The gate does catch hard regressions: a broken prompt template, an incompatible model swap, or a pipeline-wiring bug that silently returns nothing.
- Lives in `backend/tests/fixtures/` alongside the existing golden fixtures, but as its own fixture type and test file — not folded into `test_golden_fixture_normalize.py`'s exact-match machinery, since the assertion semantics are fundamentally different.
