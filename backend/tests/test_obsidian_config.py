import textwrap

from app.pipeline.sources.obsidian import load_config

CONFIG_YAML = textwrap.dedent(
    """\
    include_folders:
      - "Project"
    tags_folder: "Tags"
    main_folder: "Project"
    subfolder_type_map:
      architecture: ARCHITECTURE
      tasks: TASK
    type_signals:
      TASK:
        - bug
        - fix
      ARCHITECTURE:
        - service
        - api
    rel_keywords:
      "depends on": DEPENDS_ON
      "uses": USES
    """
)


def test_load_config_parses_all_fields(tmp_path):
    config_path = tmp_path / "vault_config.yml"
    config_path.write_text(CONFIG_YAML, encoding="utf-8")

    config = load_config(str(config_path))

    assert config.include_folders == ("Project",)
    assert config.tags_folder == "Tags"
    assert config.main_folder == "Project"
    assert config.subfolder_type_map == {"architecture": "ARCHITECTURE", "tasks": "TASK"}
    assert config.type_signals == {
        "TASK": ("bug", "fix"),
        "ARCHITECTURE": ("service", "api"),
    }
    assert config.rel_keywords == {"depends on": "DEPENDS_ON", "uses": "USES"}


def test_load_config_preserves_rel_keywords_order_for_first_match_wins(tmp_path):
    # REL_KEYWORDS order matters (infer_relationship stops at the first hit);
    # a plain dict load must preserve YAML's declared key order.
    config_path = tmp_path / "vault_config.yml"
    config_path.write_text(
        textwrap.dedent(
            """\
            include_folders: ["Project"]
            tags_folder: "Tags"
            main_folder: "Project"
            subfolder_type_map: {}
            type_signals: {}
            rel_keywords:
              "see also": RELATES_TO
              "related to": RELATES_TO
              "resolves": RESOLVES
            """
        ),
        encoding="utf-8",
    )

    config = load_config(str(config_path))

    assert list(config.rel_keywords.keys()) == ["see also", "related to", "resolves"]
