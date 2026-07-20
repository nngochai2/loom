"""Tests for the pure parsing functions ported from NAA/pipeline/src/parser.py
(ADR-0001) into pipeline/sources/obsidian.py, adapted to read config from YAML
(ADR-0004) instead of hardcoded constants.
"""

from app.pipeline.sources.obsidian import (
    ObsidianSourceConfig,
    classify_note,
    extract_wikilinks_from_text,
    infer_relationship,
    parse_header,
)

REL_KEYWORDS = {
    "depends on": "DEPENDS_ON",
    "extends": "EXTENDS",
    "uses": "USES",
    "connects to": "CONNECTS_TO",
    "implements": "IMPLEMENTS",
    "see also": "RELATES_TO",
    "related to": "RELATES_TO",
    "fixes": "FIXES",
    "resolves": "RESOLVES",
    "caused by": "CAUSED_BY",
    "follows": "FOLLOWS",
    "violates": "VIOLATES",
}


def _config(**overrides: object) -> ObsidianSourceConfig:
    defaults: dict[str, object] = dict(
        include_folders=("Project",),
        tags_folder="Tags",
        main_folder="Project",
        subfolder_type_map={
            "architecture": "ARCHITECTURE",
            "tasks": "TASK",
            "conventions": "CONVENTION",
            "business": "BUSINESS_TERM",
        },
        type_signals={
            "TASK": ("bug", "fix", "ticket"),
            "ARCHITECTURE": ("service", "api", "database"),
            "CONVENTION": ("convention", "pattern", "standard"),
            "BUSINESS_TERM": ("definition", "glossary", "term"),
        },
        rel_keywords=REL_KEYWORDS,
    )
    defaults.update(overrides)
    return ObsidianSourceConfig(**defaults)  # type: ignore[arg-type]


# ── infer_relationship ────────────────────────────────────────────────────


def test_infer_relationship_matches_keyword_case_insensitively():
    assert infer_relationship("This service DEPENDS ON the auth module", REL_KEYWORDS) == "DEPENDS_ON"


def test_infer_relationship_defaults_to_links_to_when_no_keyword_matches():
    assert infer_relationship("just a passing mention", REL_KEYWORDS) == "LINKS_TO"


# ── extract_wikilinks_from_text ──────────────────────────────────────────


def test_extracts_simple_wikilink_with_default_relationship():
    links = extract_wikilinks_from_text("See [[Some Note]] for details.", REL_KEYWORDS)
    assert len(links) == 1
    assert links[0].target == "Some Note"
    assert links[0].alias == "Some Note"
    assert links[0].relationship == "LINKS_TO"
    assert links[0].is_tag_link is False


def test_extracts_aliased_wikilink():
    links = extract_wikilinks_from_text("See [[Some Note|a friendlier name]].", REL_KEYWORDS)
    assert len(links) == 1
    assert links[0].target == "Some Note"
    assert links[0].alias == "a friendlier name"


def test_extracts_wikilink_with_inferred_relationship_from_context():
    links = extract_wikilinks_from_text("This module depends on [[Auth Service]] heavily.", REL_KEYWORDS)
    assert len(links) == 1
    assert links[0].relationship == "DEPENDS_ON"


def test_image_embeds_are_not_treated_as_wikilinks():
    links = extract_wikilinks_from_text("Here is a diagram: ![[diagram.png]]", REL_KEYWORDS)
    assert links == []


def test_tag_section_links_get_tagged_with_relationship():
    links = extract_wikilinks_from_text("[[backend]] [[urgent]]", REL_KEYWORDS, is_tag_section=True)
    assert [link.relationship for link in links] == ["TAGGED_WITH", "TAGGED_WITH"]
    assert all(link.is_tag_link for link in links)


def test_wikilink_with_heading_anchor_captures_full_target():
    links = extract_wikilinks_from_text("See [[Some Note#Section Heading]].", REL_KEYWORDS)
    assert len(links) == 1
    assert links[0].target == "Some Note"


# ── parse_header ──────────────────────────────────────────────────────────


def test_parse_header_extracts_iso_date_status_and_tags():
    lines = [
        "2026-07-01 10:30",
        "Status: #done",
        "Tags: [[backend]]",
        "  [[urgent]]",
        "Body starts here.",
    ]
    created_at, status, tag_links, body_start = parse_header(lines)

    assert created_at == "2026-07-01T10:30"
    assert status == "done"
    assert [link.target for link in tag_links] == ["backend", "urgent"]
    assert all(link.is_tag_link for link in tag_links)
    # Ported as-is from NAA (ADR-0001): once inside a Tags: block, header
    # parsing only ends on a new Status: line, not on plain content — so a
    # body line immediately after tags is absorbed into the header too.
    # This is a faithful NAA quirk, preserved for golden-fixture parity.
    assert "\n".join(lines[body_start:]) == ""


def test_parse_header_normalizes_eu_date_format():
    lines = ["01-07-2026 10:30", "Body content."]
    created_at, _status, _tags, _body_start = parse_header(lines)
    assert created_at == "2026-07-01T10:30"


def test_parse_header_with_no_recognized_fields_starts_body_at_zero():
    lines = ["Just a plain first line.", "More content."]
    created_at, status, tag_links, body_start = parse_header(lines)
    assert created_at == ""
    assert status == ""
    assert tag_links == []
    assert body_start == 0


# ── classify_note ─────────────────────────────────────────────────────────


def test_classify_note_prefers_subfolder_signal():
    config = _config()
    note_type, rule_id = classify_note("Random Title", "Architecture", "no signal words here", config)
    assert note_type == "ARCHITECTURE"
    assert rule_id == "subfolder:architecture"


def test_classify_note_falls_back_to_keyword_scoring_when_no_subfolder_hit():
    config = _config()
    note_type, rule_id = classify_note("A ticket about a bug", "", "we need to fix this bug soon", config)
    assert note_type == "TASK"
    assert rule_id == "keyword-signal:TASK"


def test_classify_note_defaults_to_note_when_nothing_matches():
    config = _config()
    note_type, rule_id = classify_note("Untitled", "", "nothing recognizable at all", config)
    assert note_type == "NOTE"
    assert rule_id == "default"
