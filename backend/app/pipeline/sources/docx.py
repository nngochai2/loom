"""Docx source adapter (spec §4.1, §7; ADR-0001, ADR-0006).

Owns all python-docx-specific mechanics — opening a `.docx` file, walking
its body for paragraphs vs. tables, and deduplicating the duplicate
adjacent cell text python-docx reports for merged table cells. Row
matching and extraction (id/title/category/named-extraction logic) is
delegated to `pipeline/rules/engine.py`, ported from NAA's
`DocxRuleParser` (`NAA/webapp/src/docx_generic_parser.py`, ADR-0001).

Only the generic parent-link path is lifted: a requirement item may carry
one optional `parent_node_id` (see `RuleEngine.apply`). This adapter
doesn't supply one — Loom's core has no document-hierarchy concept
(NAA's project-specific `Flow -> UseCase -> Document` tree is deliberately
not part of it, ADR-0006); a caller wanting that structure builds it at
the rule-config level instead.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from docx import Document as DocxDocument
from docx.table import Table, _Row
from docx.text.paragraph import Paragraph

from app.pipeline.extraction.prose_llm import ProseExtractionError, extract_prose_entities
from app.pipeline.rules.engine import RuleEngine, TableRow
from app.pipeline.rules.schema import RuleFile, load_rule_file
from app.pipeline.types import ExtractionResult, LoadedDoc, SourceDoc

load_config = load_rule_file


def _dedup_merged_cells(row: _Row) -> tuple[str, ...]:
    """Merged table cells appear as duplicate adjacent text in
    python-docx. Keep only the first occurrence of each consecutive
    duplicate, then drop empty cells entirely."""
    deduped: list[str] = []
    for cell in row.cells:
        text = cell.text.strip()
        if not deduped or text != deduped[-1]:
            deduped.append(text)
    return tuple(c for c in deduped if c)


def _element_tag(child: Any) -> str:
    tag: str = child.tag
    return tag.split("}")[-1] if "}" in tag else tag


class DocxSourceAdapter:
    """Ported from NAA's `DocxRuleParser` (ADR-0001), split across
    `discover`/`load`/`extract` per the `SourceAdapter` protocol. `load()`
    does one pass over the document body (NAA does two — a context pass
    and an items pass) since both need the same per-table id-row check and
    per-row cell dedup."""

    source_type = "docx"

    def __init__(self, rule_file: RuleFile):
        self.rule_file = rule_file
        self._engine = RuleEngine(rule_file)

    def discover(self, source_path: str) -> list[SourceDoc]:
        root = Path(source_path)
        docs: list[SourceDoc] = []
        for f in sorted(root.rglob("*.docx")):
            if f.name.startswith("~$"):
                continue  # Word's transient lock file for an open document
            relative_path = f.relative_to(root)
            # .as_posix() (not str()) so doc_id is stable across OSes, same
            # reasoning as ObsidianSourceAdapter.discover().
            doc_id = hashlib.sha1(relative_path.as_posix().encode()).hexdigest()[:16]
            content_hash = hashlib.md5(f.read_bytes()).hexdigest()
            docs.append(SourceDoc(doc_id=doc_id, path=str(f), content_hash=content_hash))
        return docs

    def load(self, doc: SourceDoc) -> LoadedDoc:
        docx_document = DocxDocument(doc.path)

        context_parts: list[str] = []
        rows: list[TableRow] = []

        for child in docx_document.element.body:
            tag = _element_tag(child)

            if tag == "p" and self.rule_file.context.include_paragraphs:
                text = Paragraph(child, docx_document).text.strip()
                if text:
                    context_parts.append(text)

            elif tag == "tbl":
                table = Table(child, docx_document)
                table_rows = [TableRow(cells=_dedup_merged_cells(row)) for row in table.rows]

                if self._engine.table_contains_id_row(table_rows):
                    rows.extend(table_rows)
                elif self.rule_file.context.include_non_br_tables:
                    rows_text = ["  ".join(row.cells) for row in table_rows if row.cells]
                    if rows_text:
                        context_parts.append("\n".join(rows_text))

        return LoadedDoc(
            doc=doc,
            content="\n\n".join(context_parts),
            metadata={"rows": tuple(rows)},
        )

    def extract(self, loaded: LoadedDoc, config: Any) -> ExtractionResult:
        # `config` is unused: this adapter's rule file is bound at
        # construction, same as ObsidianSourceAdapter's `config` parameter.
        rows = loaded.metadata["rows"]
        assert isinstance(rows, tuple)

        source_file = Path(loaded.doc.path).name
        entities, relationships = self._engine.apply(
            list(rows),
            doc_id=loaded.doc.doc_id,
            source_file=source_file,
        )

        warning: str | None = None
        prose_extraction = self.rule_file.context.prose_extraction
        if prose_extraction.enabled:
            try:
                prose_entities, prose_relationships = extract_prose_entities(
                    loaded.content,
                    prose_extraction,
                    doc_id=loaded.doc.doc_id,
                    source_file=source_file,
                )
                entities = entities + prose_entities
                relationships = relationships + prose_relationships
            except ProseExtractionError as exc:
                # Partial success (ADR-0022, issue #20): the regex-derived
                # entities/relationships above still get returned and
                # written; this doc is neither `failed` nor missing its
                # table-row output over one LLM hiccup.
                warning = f"prose extraction failed (rule {prose_extraction.id!r}): {exc}"

        return ExtractionResult(
            doc_id=loaded.doc.doc_id,
            content_hash=loaded.doc.content_hash,
            entities=entities,
            relationships=relationships,
            warning=warning,
        )
