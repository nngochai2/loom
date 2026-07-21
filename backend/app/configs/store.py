"""Configs store (spec §7, §8): parsing-rule config YAML files on disk are
the source of truth; this module is the CRUD surface the Configs API
(`app/api/configs.py`) sits on top of. It never invents a second format —
every read returns exactly what `yaml.safe_load` produced, every write goes
through `yaml.safe_dump`.

Two config shapes are supported — docx rule files (`pipeline/rules/schema.py`)
and Obsidian source configs (`pipeline/sources/obsidian.py`, ADR-0004) — and
each config's file is one YAML document directly under `configs_dir`, keyed
by filename stem as its `id`. Which shape a config is gets *detected* by
which JSON Schema it validates against, rather than stored separately,
since the schemas are disjoint by construction (each requires fields the
other's `additionalProperties: False` forbids) — the same source of truth
the write path validates against on every create/update.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml
from jsonschema import Draft202012Validator, ValidationError

from app.pipeline.rules.schema import RULE_FILE_SCHEMA, validate_rule_file
from app.pipeline.sources.obsidian_schema import OBSIDIAN_CONFIG_SCHEMA, validate_obsidian_config

_SCHEMAS_BY_SOURCE_TYPE: dict[str, dict[str, Any]] = {
    "docx": RULE_FILE_SCHEMA,
    "obsidian": OBSIDIAN_CONFIG_SCHEMA,
}
_VALIDATORS_BY_SOURCE_TYPE: dict[str, Callable[[dict[str, Any]], None]] = {
    "docx": validate_rule_file,
    "obsidian": validate_obsidian_config,
}

# No path separators, no ".." anywhere, and must not be entirely dots -- a
# config id becomes a filename directly under `configs_dir` (see
# `ConfigsStore._path_for`), so this is the only thing standing between a
# client-supplied id and a path-traversal write outside that directory.
_VALID_ID_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$")


class ConfigNotFoundError(Exception):
    """No config with this id exists on disk."""


class ConfigAlreadyExistsError(Exception):
    """A config with this id already exists (POST is create-only)."""


class InvalidConfigIdError(Exception):
    """The id isn't safe to use as a filename under `configs_dir`."""


@dataclass(frozen=True)
class SchemaError:
    """One JSON Schema violation, shaped for the API to hand back to a
    client building a form from the same schema (spec §7)."""

    path: str
    message: str


class ConfigSchemaValidationError(Exception):
    """Raised instead of writing a config that fails its source type's
    JSON Schema -- carries structured `errors` so the API returns those
    instead of a bare 500 or an unvalidated write."""

    def __init__(self, errors: list[SchemaError]) -> None:
        super().__init__(f"{len(errors)} schema violation(s): {errors}")
        self.errors = errors


@dataclass(frozen=True)
class ConfigSummary:
    id: str
    source_type: str
    title: str


@dataclass(frozen=True)
class ConfigDetail:
    id: str
    source_type: str
    title: str
    data: dict[str, Any]
    json_schema: dict[str, Any]


def detect_source_type(raw: dict[str, Any]) -> str | None:
    """Which source type's schema `raw` validates against, or `None` if
    neither does."""
    for source_type, schema in _SCHEMAS_BY_SOURCE_TYPE.items():
        if Draft202012Validator(schema).is_valid(raw):
            return source_type
    return None


def _validate(source_type: str, raw: dict[str, Any]) -> None:
    validate = _VALIDATORS_BY_SOURCE_TYPE.get(source_type)
    if validate is None:
        raise ConfigSchemaValidationError(
            [
                SchemaError(
                    path="source_type",
                    message=(
                        f"unknown source_type {source_type!r}; "
                        f"expected one of {sorted(_VALIDATORS_BY_SOURCE_TYPE)}"
                    ),
                )
            ]
        )
    try:
        validate(raw)
    except ValidationError as exc:
        raise ConfigSchemaValidationError(
            [SchemaError(path="/".join(str(p) for p in exc.path), message=exc.message)]
        ) from exc
    except ValueError as exc:
        raise ConfigSchemaValidationError([SchemaError(path="", message=str(exc))]) from exc


def _validate_id(config_id: str) -> None:
    if ".." in config_id or not _VALID_ID_RE.match(config_id):
        raise InvalidConfigIdError(
            f"invalid config id {config_id!r}: must match {_VALID_ID_RE.pattern} "
            "with no path separators or '..'"
        )


def _title_of(config_id: str, raw: dict[str, Any]) -> str:
    name = raw.get("name")
    return str(name) if name else config_id


def _dump_yaml(data: dict[str, Any]) -> str:
    # `sort_keys=False` so re-saving preserves the order `yaml.safe_load`
    # already read off disk (Python dicts preserve insertion order) --
    # the ticket's round-trip requirement, plus real rule files (e.g.
    # `br_requirements.yml`) group related fields for readability.
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)


class ConfigsStore:
    """File-backed CRUD over parsing-rule config YAML (spec §7). Each
    `*.yml`/`*.yaml` file directly under `configs_dir` is one config; its
    `id` is the filename stem. Validation runs before every write, so an
    invalid config never reaches disk (the ticket's core guarantee)."""

    def __init__(self, configs_dir: str) -> None:
        self._dir = Path(configs_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _config_paths(self) -> list[Path]:
        return sorted(
            p for p in self._dir.iterdir() if p.is_file() and p.suffix in (".yml", ".yaml")
        )

    def _existing_path_for(self, config_id: str) -> Path | None:
        _validate_id(config_id)
        for ext in (".yml", ".yaml"):
            candidate = self._dir / f"{config_id}{ext}"
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _read_yaml(path: Path) -> dict[str, Any]:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}

    def list_configs(self) -> list[ConfigSummary]:
        summaries: list[ConfigSummary] = []
        for path in self._config_paths():
            raw = self._read_yaml(path)
            source_type = detect_source_type(raw)
            if source_type is None:
                continue  # not a config this API recognizes; skip rather than fail listing
            config_id = path.stem
            summaries.append(
                ConfigSummary(id=config_id, source_type=source_type, title=_title_of(config_id, raw))
            )
        return summaries

    def get_config(self, config_id: str) -> ConfigDetail:
        path = self._existing_path_for(config_id)
        if path is None:
            raise ConfigNotFoundError(config_id)
        raw = self._read_yaml(path)
        source_type = detect_source_type(raw)
        if source_type is None:
            raise ConfigNotFoundError(config_id)
        return ConfigDetail(
            id=config_id,
            source_type=source_type,
            title=_title_of(config_id, raw),
            data=raw,
            json_schema=_SCHEMAS_BY_SOURCE_TYPE[source_type],
        )

    def create_config(self, config_id: str, source_type: str, data: dict[str, Any]) -> ConfigDetail:
        if self._existing_path_for(config_id) is not None:
            raise ConfigAlreadyExistsError(config_id)
        _validate(source_type, data)

        path = self._dir / f"{config_id}.yml"
        path.write_text(_dump_yaml(data), encoding="utf-8")
        return ConfigDetail(
            id=config_id,
            source_type=source_type,
            title=_title_of(config_id, data),
            data=data,
            json_schema=_SCHEMAS_BY_SOURCE_TYPE[source_type],
        )

    def update_config(self, config_id: str, data: dict[str, Any]) -> ConfigDetail:
        path = self._existing_path_for(config_id)
        if path is None:
            raise ConfigNotFoundError(config_id)
        existing_source_type = detect_source_type(self._read_yaml(path))
        if existing_source_type is None:
            raise ConfigNotFoundError(config_id)
        _validate(existing_source_type, data)

        path.write_text(_dump_yaml(data), encoding="utf-8")
        return ConfigDetail(
            id=config_id,
            source_type=existing_source_type,
            title=_title_of(config_id, data),
            data=data,
            json_schema=_SCHEMAS_BY_SOURCE_TYPE[existing_source_type],
        )
