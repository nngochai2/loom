"""Docx rule engine (spec §4.1, §7; ADR-0001, ADR-0002, ADR-0006).

Applies a loaded `RuleFile` (`pipeline/rules/schema.py`) against table rows
already pulled from a docx file by `pipeline/sources/docx.py` (which owns
all python-docx-specific table/paragraph walking), producing
`Entity`/`Relationship` instances tagged `origin: extracted` with the
producing `rule_id` (spec §5).

Row matching, id formatting, title extraction, category-signal inference,
and named extractions are ported from NAA's `DocxRuleParser`
(`NAA/webapp/src/docx_generic_parser.py`, ADR-0001). Only the generic
single `parent_node_id` link is kept — not NAA's project-specific
`Flow -> UseCase -> Document` hierarchy (ADR-0006).
"""

from __future__ import annotations

import hashlib
import importlib
import re
from dataclasses import dataclass

from app.pipeline.rules.schema import RuleFile
from app.pipeline.types import Entity, Relationship

_kg_schema = importlib.import_module("kg-schema")

# There is exactly one id-matching rule per rule file, so a fixed string
# identifies it as the producing rule_id (spec §5) — the same convention
# ObsidianSourceAdapter uses for its own single-path classifications
# ("tag-folder", "default").
ROW_MATCH_RULE_ID = "id-pattern-match"


def _parse_flags(flags: str) -> int:
    result = 0
    for token in flags.upper().split("|"):
        token = token.strip()
        if token == "IGNORECASE":
            result |= re.IGNORECASE
        elif token == "MULTILINE":
            result |= re.MULTILINE
        elif token == "DOTALL":
            result |= re.DOTALL
    return result


@dataclass(frozen=True)
class TableRow:
    """One table row's non-empty, merged-cell-deduplicated cell texts."""

    cells: tuple[str, ...]


class RuleEngine:
    """Compiles a `RuleFile`'s patterns once (construction), then applies
    them per table row (`apply`)."""

    def __init__(self, rule_file: RuleFile) -> None:
        if rule_file.node_label not in _kg_schema.ENTITY_TYPES:
            raise ValueError(
                f"rule file {rule_file.name!r} maps to node_label "
                f"{rule_file.node_label!r}, which is not a kg-schema entity "
                "type (ADR-0002) — add it via a schema-version bump first"
            )

        self._rule_file = rule_file
        self._id_re = re.compile(rule_file.id_pattern, _parse_flags(rule_file.id_flags))
        self._category_signals: list[tuple[re.Pattern[str], str]] = [
            (re.compile(s.pattern, _parse_flags(s.flags)), s.name) for s in rule_file.category_signals
        ]
        self._named_extractions = [
            (re.compile(e.pattern, _parse_flags(e.flags)), e) for e in rule_file.named_extractions
        ]

    def row_matches_id(self, first_cell: str) -> bool:
        return self._id_re.match(first_cell) is not None

    def table_contains_id_row(self, rows: list[TableRow]) -> bool:
        return any(row.cells and self.row_matches_id(row.cells[0]) for row in rows)

    def apply(
        self,
        rows: list[TableRow],
        doc_id: str,
        source_file: str,
        parent_node_id: str | None = None,
        parent_rel_type: str = _kg_schema.DEFAULT_RELATIONSHIP_TYPE,
    ) -> tuple[tuple[Entity, ...], tuple[Relationship, ...]]:
        """Extract one `Entity` per id-matching row, plus one optional
        `parent_node_id -> item` `Relationship` per row when a parent is
        supplied (the generic parent-link mechanism, ADR-0006)."""
        entities: list[Entity] = []
        relationships: list[Relationship] = []

        for row in rows:
            if len(row.cells) < 2:
                continue
            match = self._id_re.match(row.cells[0])
            if not match:
                continue

            item_id = self._format_id(match)
            body = row.cells[1].strip()
            title = self._extract_title(body, fallback=row.cells[0])
            node_id = hashlib.sha1(
                f"{self._rule_file.node_label}::{doc_id}::{item_id}".encode()
            ).hexdigest()[:16]

            entities.append(
                Entity(
                    id=node_id,
                    type=self._rule_file.node_label,
                    name=title,
                    origin="extracted",
                    rule_id=ROW_MATCH_RULE_ID,
                    properties={
                        "req_id": item_id,
                        "body": body,
                        "source_file": source_file,
                        "candidate_categories": self._infer_categories(body),
                        "named_extractions": self._run_extractions(body),
                    },
                )
            )

            if parent_node_id is not None:
                relationships.append(
                    Relationship(
                        from_id=parent_node_id,
                        to_id=node_id,
                        type=parent_rel_type,
                        origin="extracted",
                        rule_id=ROW_MATCH_RULE_ID,
                    )
                )

        return tuple(entities), tuple(relationships)

    def _format_id(self, match: re.Match[str]) -> str:
        try:
            return self._rule_file.id_format.format(int(match.group(1)))
        except (IndexError, ValueError):
            return self._rule_file.id_format.format(match.group(0))

    def _extract_title(self, body: str, fallback: str) -> str:
        if self._rule_file.title_from == "first_line":
            lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
            return lines[0] if lines else fallback
        return fallback

    def _infer_categories(self, body: str) -> list[str]:
        return [name for pattern, name in self._category_signals if pattern.search(body)]

    def _run_extractions(self, body: str) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for pattern, ext in self._named_extractions:
            matches = [m.group(ext.group) for m in pattern.finditer(body)]

            if ext.transform == "uppercase":
                matches = [v.upper() for v in matches]
            elif ext.transform == "lowercase":
                matches = [v.lower() for v in matches]

            if ext.filter == "no_spaces":
                matches = [v for v in matches if " " not in v]

            # sort implies deduplicate; deduplicate alone preserves insertion order
            if ext.sort:
                matches = sorted(set(matches))
            elif ext.deduplicate:
                matches = list(dict.fromkeys(matches))

            result[ext.name] = matches

        return result
