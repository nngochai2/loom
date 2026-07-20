"""One-off generator for the golden-fixture parity snapshot (ADR-0007).

Runs NAA's *real* current parsers — `NAA/pipeline/src/parser.py` for
Obsidian, `NAA/webapp/src/docx_generic_parser.py` for docx — against
Loom's checked-in fixture set, and writes their normalized output to
`backend/tests/fixtures/golden/*.json`.

This script is NOT part of the pytest suite and is NEVER imported by one.
NAA lives at an absolute, machine-local sibling path
(`D:\\Cloned Projects\\NAA`) that only exists on this dev machine — it is
not a submodule, not vendored, and not reachable from a GitHub Actions
runner. `backend/tests/test_golden_fixture_parity.py` is the CI-safe half
of this gate: it only ever reads the committed JSON this script produces
and never touches the NAA path.

Run this manually (and re-commit the regenerated JSON) whenever:
  - the fixture vault or fixture docx set changes, or
  - NAA's real parser behavior changes and Loom's port is intentionally
    re-synced to match.

Usage (from `backend/`, with an interpreter that has both Loom's deps
(pyyaml, python-docx) *and* NAA's own (notably python-dotenv, which
NAA/pipeline/src/config.py imports at module load time and Loom's backend
venv doesn't carry) -- Loom's backend venv is missing the latter, so this
was run with the system interpreter that already had NAA's requirements
installed):

    python scripts/generate_golden_fixture_snapshot.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[1]
NAA_ROOT = Path(r"D:\Cloned Projects\NAA")

for _p in (BACKEND_ROOT, BACKEND_ROOT / "tests", NAA_ROOT / "pipeline" / "src", NAA_ROOT / "webapp" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from golden_fixture_normalize import (  # noqa: E402 (sys.path set up above)
    naa_note_id,
    normalize_naa_docx_item,
    normalize_naa_obsidian_edge,
    normalize_naa_obsidian_note,
)

FIXTURES = BACKEND_ROOT / "tests" / "fixtures"
VAULT = FIXTURES / "vault"
OBSIDIAN_CONFIG_PATH = FIXTURES / "obsidian_config.yml"
DOCS_DIR = FIXTURES / "docs"
NAA_DOCX_RULE_FILE = NAA_ROOT / "parsing-rules" / "br_requirements.yml"
GOLDEN_DIR = FIXTURES / "golden"


def generate_obsidian_snapshot() -> dict[str, Any]:
    if not NAA_ROOT.exists():
        raise SystemExit(f"NAA source tree not found at {NAA_ROOT} -- this script only runs on the dev machine")

    import parser as naa_parser  # NAA/pipeline/src/parser.py

    with OBSIDIAN_CONFIG_PATH.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # NAA's vault-classification config is hardcoded Python constants in
    # config.py, not per-vault YAML (ADR-0004 moved this to YAML in Loom
    # only -- the *mechanism* differs by design). Point NAA's
    # already-imported parser module at Loom's fixture-vault config
    # values, read from the same YAML ObsidianSourceAdapter uses, so both
    # sides classify the identical vault the same way. parser.py reads
    # these as bare module globals at call time, so reassigning the
    # attributes here (after import, before scan_vault()) takes effect.
    naa_parser.MAIN_FOLDER = cfg["main_folder"]
    naa_parser.TAGS_FOLDER = cfg["tags_folder"]
    naa_parser.INCLUDE_FOLDERS = set(cfg["include_folders"])
    naa_parser.SUBFOLDER_TYPE_MAP = dict(cfg["subfolder_type_map"])
    naa_parser.TYPE_SIGNALS = {k: list(v) for k, v in cfg["type_signals"].items()}
    naa_parser.REL_KEYWORDS = dict(cfg["rel_keywords"])

    notes, errors, skipped = naa_parser.scan_vault(VAULT)
    if errors:
        raise RuntimeError(f"NAA parser reported errors on the fixture vault: {errors}")
    print(f"[obsidian] scanned {len(notes)} notes, skipped {skipped} non-included files, 0 errors")

    naa_parser.resolve_backlinks(notes)

    entities = [normalize_naa_obsidian_note(n) for n in notes]

    # Mirrors GraphBuilder.upsert_relationships (NAA/pipeline/src/graph.py)
    # -- the real downstream step that turns resolved WikiLinks into graph
    # edges: a title->id lookup over every note, keeping only links whose
    # target resolved to a real note (dangling links are silently dropped,
    # same as Loom's adapter). Ids are computed via naa_note_id(), not
    # NAA's own node_id property -- see that function's docstring.
    title_to_id = {n.title: naa_note_id(n) for n in notes}
    edges = []
    for note in notes:
        for link in note.links:
            tgt_id = title_to_id.get(link.target)
            if tgt_id is None:
                continue
            edges.append(normalize_naa_obsidian_edge(naa_note_id(note), tgt_id, link))

    entities.sort(key=lambda e: e["id"])
    edges.sort(key=lambda e: (e["from_id"], e["to_id"], e["type"]))
    return {"entities": entities, "edges": edges}


def generate_docx_snapshot() -> dict[str, Any]:
    if not NAA_ROOT.exists():
        raise SystemExit(f"NAA source tree not found at {NAA_ROOT} -- this script only runs on the dev machine")

    from docx_generic_parser import DocxRuleParser  # NAA/webapp/src/docx_generic_parser.py

    # NAA's *actual* current rule file (its real production config) --
    # not Loom's fixture-derived one. The two differ deliberately
    # (node_label BR vs REQUIREMENT, plus a couple of category
    # signals/keywords NAA's original has that Loom's generic
    # re-derivation dropped, per ADR-0006) -- see
    # golden_fixture_normalize.DOCX_NODE_LABEL_ALLOWLIST for how the one
    # observable divergence on this fixture set is allowlisted.
    parser = DocxRuleParser(str(NAA_DOCX_RULE_FILE))

    per_file: dict[str, Any] = {}
    for docx_path in sorted(DOCS_DIR.glob("*.docx")):
        items, _context = parser.parse(docx_path)
        entities = sorted((normalize_naa_docx_item(item) for item in items), key=lambda e: e["req_id"])
        per_file[docx_path.name] = {"entities": entities}
        print(f"[docx] {docx_path.name}: {len(entities)} entities")

    return per_file


def main() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)

    obsidian_snapshot = generate_obsidian_snapshot()
    (GOLDEN_DIR / "obsidian_vault.json").write_text(
        json.dumps(obsidian_snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"\n[obsidian] wrote {len(obsidian_snapshot['entities'])} entities, "
        f"{len(obsidian_snapshot['edges'])} edges to tests/fixtures/golden/obsidian_vault.json"
    )
    for e in obsidian_snapshot["entities"]:
        print(" ", e)
    for e in obsidian_snapshot["edges"]:
        print(" ", e)

    docx_snapshot = generate_docx_snapshot()
    (GOLDEN_DIR / "docx_fixtures.json").write_text(
        json.dumps(docx_snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print("\n[docx] wrote tests/fixtures/golden/docx_fixtures.json")
    for filename, data in docx_snapshot.items():
        print(f" {filename}: {len(data['entities'])} entities")
        for e in data["entities"]:
            print("   ", e)


if __name__ == "__main__":
    main()
