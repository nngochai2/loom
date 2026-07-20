# 0006 — Docx ingestion lifts the generic parent-link path only, not Flow/UseCase/Document

## Status
Accepted

## Context
NAA's live docx ingestion (`webapp/src/main.py`, `parse_doc` endpoint) has two branches:
1. A generic, source-agnostic one: `GraphBuilder.upsert_requirements(items, parent_node_id=...)` — a requirement node with an optional single parent link.
2. A project-specific one: when `flow_name`/`uc_id`/`doc_type` are supplied, it builds a hardcoded `Flow → UseCase → Document → BR` hierarchy — modeling the user's eInvoice project's own structure (a "flow" made of "use cases," each with FDD/SDD documents).

Separately, `GraphBuilder` also defines `upsert_sql_view`/`upsert_oracle_package`/`upsert_package_functions`/etc. — these are never called anywhere in the live code; they are dead code from an earlier or planned eInvoice-specific SQL/Oracle ingestion path.

The loom-spec never mentions Flow/UseCase/Document/SqlView/OraclePackage concepts anywhere — Loom is meant to be usable for arbitrary docx sources, not just FDD/SDD-shaped specs for one project.

## Decision
Loom lifts only the generic parent-link path. Requirement nodes get an optional single `parent_node_id`; the caller decides what "parent" means for their document source. The `Flow`/`UseCase`/`Document` hierarchy and the unused `SqlView`/`SqlSegment`/`FieldMapping`/`OraclePackage`/`PackageFunction` machinery are **not** part of Loom's core and are not lifted.

## Consequences
- Someone who wants NAA's Flow/UseCase/Document structure specifically can model it as a document-hierarchy convention on top of the generic parent-link mechanism, entirely at the rule-config/schema level (per ADR-0002/0003) — no special-cased entity types in Loom's core.
- The `POST /jobs` / `POST /configs/{id}/preview` API surface (spec §8) doesn't need flow/use-case/doc-type-specific fields.
