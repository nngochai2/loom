"""Obsidian source adapter (spec §4.1, §7; ADR-0001, ADR-0004).

Parsing logic (header format, wikilink regex, classification) is lifted
from NAA/pipeline/src/parser.py and NAA/pipeline/src/config.py, which are
hand-rolled regex over raw `.md` text — deterministic, not LLM-based
(ADR-0001). The one behavioral change from NAA: vault-specific
classification config (folder->type map, keyword signals,
relationship-inference keywords, included folders) is read from per-config
YAML instead of hardcoded Python constants (ADR-0004).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.pipeline.types import (
    Entity,
    ExplicitEdge,
    ExtractionResult,
    LoadedDoc,
    Relationship,
    SourceDoc,
)

_DATE_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})")  # YYYY-MM-DD
_DATE_LINE_RE_EU = re.compile(r"^(\d{2})-(\d{2})-(\d{4})\s+(\d{2}:\d{2})")  # DD-MM-YYYY
_STATUS_RE = re.compile(r"^Status:\s*#(\w+)", re.IGNORECASE)
_TAGS_START_RE = re.compile(r"^Tags:\s*(.*)", re.IGNORECASE)
# (?<!!) excludes Obsidian image embeds: ![[image.png]]
# Group 1 is greedy ([^\]|#]+) so the full target name is always captured
_WIKILINK_RE = re.compile(r"(?<!!)\[\[([^\]|#]+)(?:#[^\]|]*)?\|?([^\]]*?)\]\]")


@dataclass(frozen=True)
class ObsidianSourceConfig:
    """Per-vault classification config (ADR-0004) — moved out of NAA's
    hardcoded Python into YAML, loaded via `load_config()`."""

    include_folders: tuple[str, ...]
    tags_folder: str
    main_folder: str
    subfolder_type_map: dict[str, str]
    type_signals: dict[str, tuple[str, ...]]
    rel_keywords: dict[str, str]


@dataclass(frozen=True)
class _NoteMetadata:
    """The fields `load()` discovers and `extract()` classifies with —
    always travel together, so they get one typed home in
    `LoadedDoc.metadata["note"]` instead of five loose string keys."""

    title: str
    is_tag_note: bool
    subfolder: str = ""
    status: str = ""
    header_created_at: str = ""


@dataclass(frozen=True)
class _WikiLink:
    target: str
    alias: str
    context: str
    relationship: str
    is_tag_link: bool = False


def infer_relationship(context: str, rel_keywords: dict[str, str]) -> str:
    context = context.lower()
    for keyword, rel_type in rel_keywords.items():
        if keyword in context:
            return rel_type
    return "LINKS_TO"


def extract_wikilinks_from_text(
    text: str, rel_keywords: dict[str, str], is_tag_section: bool = False
) -> list[_WikiLink]:
    links = []
    for m in _WIKILINK_RE.finditer(text):
        target = m.group(1).strip()
        alias = m.group(2).strip() or target
        start = max(0, m.start() - 60)
        end = min(len(text), m.end() + 60)
        ctx = text[start:end].replace("\n", " ")
        rel = "TAGGED_WITH" if is_tag_section else infer_relationship(ctx, rel_keywords)
        links.append(
            _WikiLink(target=target, alias=alias, context=ctx, relationship=rel, is_tag_link=is_tag_section)
        )
    return links


def parse_header(lines: list[str]) -> tuple[str, str, list[_WikiLink], int]:
    """Parse custom Obsidian header (non-YAML).

    Returns (created_at, status, tag_links, body_start_line_index).
    """
    created_at = ""
    status = ""
    tag_links: list[_WikiLink] = []
    in_tags_block = False
    last_i = -1  # index of the last recognised header line; -1 = none found
    for i, line in enumerate(lines[:25]):
        stripped = line.strip()
        if not created_at:
            m = _DATE_LINE_RE.match(stripped)
            if m:
                created_at = f"{m.group(1)}T{m.group(2)}"
                last_i = i
                continue
            m = _DATE_LINE_RE_EU.match(stripped)
            if m:
                # Normalise DD-MM-YYYY -> YYYY-MM-DD
                created_at = f"{m.group(3)}-{m.group(2)}-{m.group(1)}T{m.group(4)}"
                last_i = i
                continue
        if not status:
            m = _STATUS_RE.match(stripped)
            if m:
                status = m.group(1)
                in_tags_block = False
                last_i = i
                continue
        m = _TAGS_START_RE.match(line)
        if m:
            in_tags_block = True
            last_i = i
            inline = m.group(1).strip()
            if inline:
                tag_links.extend(extract_wikilinks_from_text(inline, {}, is_tag_section=True))
            continue
        if in_tags_block:
            if stripped.startswith("[[") or (line.startswith((" ", "\t")) and stripped):
                tag_links.extend(extract_wikilinks_from_text(stripped, {}, is_tag_section=True))
            last_i = i
            continue
        else:
            in_tags_block = False
    return created_at, status, tag_links, last_i + 1


def classify_note(
    title: str, subfolder: str, body: str, config: ObsidianSourceConfig
) -> tuple[str, str]:
    """Classify a note into an entity type, per spec §5's enum.

    Returns (entity_type, rule_id) — rule_id names whichever classification
    path fired, since every `origin: extracted` node must carry one (§5).
    """
    note_type = config.subfolder_type_map.get(subfolder.lower(), "")
    if note_type:
        return note_type, f"subfolder:{subfolder.lower()}"

    combined = (title + " " + body[:400]).lower()
    scores: dict[str, int] = {t: 0 for t in config.type_signals}
    for ntype, keywords in config.type_signals.items():
        for kw in keywords:
            if kw in combined:
                scores[ntype] += 1
    best = max(scores, key=scores.__getitem__) if scores else ""
    if best and scores[best] > 0:
        return best, f"keyword-signal:{best}"

    return "NOTE", "default"


def load_config(path: str) -> ObsidianSourceConfig:
    """Load a per-vault classification config from YAML (ADR-0004)."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    return ObsidianSourceConfig(
        include_folders=tuple(raw["include_folders"]),
        tags_folder=raw["tags_folder"],
        main_folder=raw["main_folder"],
        subfolder_type_map=dict(raw["subfolder_type_map"]),
        type_signals={k: tuple(v) for k, v in raw["type_signals"].items()},
        rel_keywords=dict(raw["rel_keywords"]),
    )


class ObsidianSourceAdapter:
    """Lifted from NAA/pipeline/src/parser.py's `scan_vault`/`parse_note`
    (ADR-0001), split across `discover`/`load`/`extract` per the
    `SourceAdapter` protocol.

    Wikilink-target resolution needs to know every note's title up front
    (a note can link to one discovered later in vault-walk order, or to
    nothing at all — a dangling link, which NAA silently drops rather than
    writing a phantom node). `discover()` builds that title -> doc_id map
    once and caches it on the instance for `load()` to consult.
    """

    source_type = "obsidian"

    def __init__(self, config: ObsidianSourceConfig):
        self.config = config
        self._vault_root = Path()
        self._title_to_id: dict[str, str] = {}

    def discover(self, source_path: str) -> list[SourceDoc]:
        vault_root = Path(source_path)
        self._vault_root = vault_root
        include_parts = [tuple(folder.split("/")) for folder in self.config.include_folders]

        docs: list[SourceDoc] = []
        title_to_id: dict[str, str] = {}
        for f in sorted(vault_root.rglob("*.md")):
            rel_parts = f.relative_to(vault_root).parts
            top_folder = rel_parts[0] if rel_parts else ""
            included = top_folder == self.config.tags_folder or any(
                rel_parts[: len(fp)] == fp for fp in include_parts
            )
            if any(p.startswith(".") for p in rel_parts) or not included:
                continue

            relative_path = f.relative_to(vault_root)
            doc_id = hashlib.sha1(str(relative_path).encode()).hexdigest()[:16]
            if top_folder == self.config.tags_folder:
                content_hash = hashlib.md5(f.stem.encode()).hexdigest()
            else:
                raw = f.read_text(encoding="utf-8", errors="replace")
                content_hash = hashlib.md5(raw.encode()).hexdigest()

            docs.append(SourceDoc(doc_id=doc_id, path=str(f), content_hash=content_hash))
            title_to_id[f.stem.lower()] = doc_id

        self._title_to_id = title_to_id
        return docs

    def _folder_hint(self, path: Path) -> str:
        try:
            rel = path.relative_to(self._vault_root / self.config.main_folder)
            return rel.parts[-2] if len(rel.parts) > 1 else ""
        except ValueError:
            return ""

    def load(self, doc: SourceDoc) -> LoadedDoc:
        path = Path(doc.path)
        title = path.stem
        rel_parts = path.relative_to(self._vault_root).parts
        top_folder = rel_parts[0] if rel_parts else ""

        if top_folder == self.config.tags_folder:
            note_metadata: _NoteMetadata = _NoteMetadata(title=title, is_tag_note=True)
            return LoadedDoc(doc=doc, content="", metadata={"note": note_metadata})

        raw = path.read_text(encoding="utf-8", errors="replace")
        lines = raw.splitlines()
        header_created_at, status, tag_links, header_end = parse_header(lines)
        body = "\n".join(lines[header_end:])
        body_links = extract_wikilinks_from_text(body, self.config.rel_keywords, is_tag_section=False)

        explicit_edges = []
        for link in tag_links + body_links:
            to_id = self._title_to_id.get(link.target.lower())
            if to_id is None:
                continue  # dangling wikilink target: drop it, matching NAA exactly
            explicit_edges.append(
                ExplicitEdge(
                    from_id=doc.doc_id,
                    to_id=to_id,
                    type=link.relationship,
                    properties={"alias": link.alias, "context": link.context},
                )
            )

        return LoadedDoc(
            doc=doc,
            content=body,
            explicit_edges=tuple(explicit_edges),
            metadata={
                "note": _NoteMetadata(
                    title=title,
                    is_tag_note=False,
                    subfolder=self._folder_hint(path),
                    status=status,
                    header_created_at=header_created_at,
                )
            },
        )

    def extract(self, loaded: LoadedDoc, config: Any) -> ExtractionResult:
        # `config` is unused here: this adapter already has its config bound
        # at construction (discover()/load() need it too, and neither takes
        # a config parameter per the SourceAdapter protocol). Kept on the
        # signature for uniformity with sources whose rule engine genuinely
        # needs a per-call config (docx).
        note = loaded.metadata["note"]
        assert isinstance(note, _NoteMetadata)

        if note.is_tag_note:
            entity = Entity(
                id=loaded.doc.doc_id,
                type="TAG",
                name=note.title,
                origin="extracted",
                rule_id="tag-folder",
            )
        else:
            note_type, rule_id = classify_note(note.title, note.subfolder, loaded.content, self.config)
            entity = Entity(
                id=loaded.doc.doc_id,
                type=note_type,
                name=note.title,
                origin="extracted",
                rule_id=rule_id,
                properties={
                    "subfolder": note.subfolder,
                    "status": note.status,
                    "header_created_at": note.header_created_at,
                },
            )

        relationships = tuple(
            Relationship(
                from_id=edge.from_id,
                to_id=edge.to_id,
                type=edge.type,
                origin="explicit",
                properties=edge.properties,
            )
            for edge in loaded.explicit_edges
        )

        return ExtractionResult(
            doc_id=loaded.doc.doc_id,
            content_hash=loaded.doc.content_hash,
            entities=(entity,),
            relationships=relationships,
        )
