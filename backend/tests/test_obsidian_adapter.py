from app.pipeline.sources.obsidian import ObsidianSourceAdapter, ObsidianSourceConfig

CONFIG = ObsidianSourceConfig(
    include_folders=("Project",),
    tags_folder="Tags",
    main_folder="Project",
    subfolder_type_map={"architecture": "ARCHITECTURE", "tasks": "TASK"},
    type_signals={
        "TASK": ("bug", "fix"),
        "ARCHITECTURE": ("service", "api"),
    },
    rel_keywords={"depends on": "DEPENDS_ON"},
)


def _write(vault_root, rel_path: str, content: str) -> None:
    path = vault_root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_vault(tmp_path):
    vault = tmp_path / "vault"
    _write(
        vault,
        "Project/Architecture/Auth Service.md",
        "This service depends on [[API Gateway]] for routing.\n",
    )
    _write(vault, "Project/Architecture/API Gateway.md", "The gateway itself.\n")
    _write(
        vault,
        "Project/Tasks/Fix Login Bug.md",
        "See [[Auth Service]] for context. Also references [[Ghost Note]] which does not exist.\n",
    )
    _write(vault, "Tags/backend.md", "")
    # Excluded: outside include_folders and not the tags folder
    _write(vault, "Scratch/ignored.md", "should not be discovered\n")
    # Excluded: dotfile-prefixed folder
    _write(vault, "Project/.trash/deleted.md", "should not be discovered\n")
    return vault


def test_discover_finds_only_included_and_tag_folder_files(tmp_path):
    vault = _build_vault(tmp_path)
    adapter = ObsidianSourceAdapter(CONFIG)

    docs = adapter.discover(str(vault))

    discovered_paths = {doc.path for doc in docs}
    assert len(docs) == 4
    assert any(p.endswith("Auth Service.md") for p in discovered_paths)
    assert any(p.endswith("API Gateway.md") for p in discovered_paths)
    assert any(p.endswith("Fix Login Bug.md") for p in discovered_paths)
    assert any(p.endswith("backend.md") for p in discovered_paths)
    assert not any("ignored.md" in p for p in discovered_paths)
    assert not any("deleted.md" in p for p in discovered_paths)


def test_discover_computes_stable_content_hash(tmp_path):
    vault = _build_vault(tmp_path)
    adapter = ObsidianSourceAdapter(CONFIG)

    first = {doc.doc_id: doc.content_hash for doc in adapter.discover(str(vault))}
    second = {doc.doc_id: doc.content_hash for doc in adapter.discover(str(vault))}

    assert first == second
    assert all(h for h in first.values())


def _extract_for(adapter: ObsidianSourceAdapter, docs, filename_suffix: str):
    doc = next(d for d in docs if d.path.endswith(filename_suffix))
    loaded = adapter.load(doc)
    return adapter.extract(loaded, CONFIG)


def test_note_entity_is_classified_by_subfolder_with_extracted_origin(tmp_path):
    vault = _build_vault(tmp_path)
    adapter = ObsidianSourceAdapter(CONFIG)
    docs = adapter.discover(str(vault))

    result = _extract_for(adapter, docs, "Auth Service.md")

    assert len(result.entities) == 1
    entity = result.entities[0]
    assert entity.type == "ARCHITECTURE"
    assert entity.origin == "extracted"
    assert entity.rule_id == "subfolder:architecture"
    assert entity.name == "Auth Service"


def test_tag_folder_note_becomes_tag_entity(tmp_path):
    vault = _build_vault(tmp_path)
    adapter = ObsidianSourceAdapter(CONFIG)
    docs = adapter.discover(str(vault))

    result = _extract_for(adapter, docs, "backend.md")

    assert len(result.entities) == 1
    entity = result.entities[0]
    assert entity.type == "TAG"
    assert entity.origin == "extracted"
    assert result.relationships == ()


def test_wikilink_resolves_to_target_note_as_explicit_relationship_with_no_rule_id(tmp_path):
    vault = _build_vault(tmp_path)
    adapter = ObsidianSourceAdapter(CONFIG)
    docs = adapter.discover(str(vault))
    gateway_doc = next(d for d in docs if d.path.endswith("API Gateway.md"))

    result = _extract_for(adapter, docs, "Auth Service.md")

    assert len(result.relationships) == 1
    rel = result.relationships[0]
    assert rel.to_id == gateway_doc.doc_id
    assert rel.origin == "explicit"
    assert rel.rule_id is None
    assert rel.type == "DEPENDS_ON"


def test_dangling_wikilink_is_dropped_not_written(tmp_path):
    vault = _build_vault(tmp_path)
    adapter = ObsidianSourceAdapter(CONFIG)
    docs = adapter.discover(str(vault))

    result = _extract_for(adapter, docs, "Fix Login Bug.md")

    # Two links in the body: one resolves (Auth Service), one is dangling (Ghost Note)
    assert len(result.relationships) == 1
    assert result.relationships[0].type == "LINKS_TO"


def test_extraction_result_carries_doc_id_and_content_hash(tmp_path):
    vault = _build_vault(tmp_path)
    adapter = ObsidianSourceAdapter(CONFIG)
    docs = adapter.discover(str(vault))
    doc = next(d for d in docs if d.path.endswith("Auth Service.md"))

    result = _extract_for(adapter, docs, "Auth Service.md")

    assert result.doc_id == doc.doc_id
    assert result.content_hash == doc.content_hash
