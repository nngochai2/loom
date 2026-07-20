import ast
from pathlib import Path

import pytest

from app.db import neo4j_client

APP_ROOT = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture(autouse=True)
def _reset_driver_singleton():
    neo4j_client.close_driver()
    yield
    neo4j_client.close_driver()


@pytest.fixture()
def neo4j_env(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")


class _FakeDriver:
    def __init__(self, uri: str, auth: tuple[str, str]):
        self.uri = uri
        self.auth = auth
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_get_driver_reads_credentials_from_env(monkeypatch, neo4j_env):
    created: dict[str, object] = {}

    def fake_driver(uri: str, auth: tuple[str, str]) -> _FakeDriver:
        created["uri"] = uri
        created["auth"] = auth
        return _FakeDriver(uri, auth)

    monkeypatch.setattr(neo4j_client.GraphDatabase, "driver", fake_driver)

    driver = neo4j_client.get_driver()

    assert created == {"uri": "bolt://localhost:7687", "auth": ("neo4j", "secret")}
    assert driver.uri == "bolt://localhost:7687"


def test_get_driver_is_a_singleton(monkeypatch, neo4j_env):
    monkeypatch.setattr(neo4j_client.GraphDatabase, "driver", _FakeDriver)

    first = neo4j_client.get_driver()
    second = neo4j_client.get_driver()

    assert first is second


def test_close_driver_closes_and_clears_singleton(monkeypatch, neo4j_env):
    monkeypatch.setattr(neo4j_client.GraphDatabase, "driver", _FakeDriver)

    driver = neo4j_client.get_driver()
    neo4j_client.close_driver()

    assert driver.closed is True


def test_get_driver_raises_if_env_vars_missing(monkeypatch):
    monkeypatch.delenv("NEO4J_URI", raising=False)
    monkeypatch.delenv("NEO4J_USER", raising=False)
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)

    with pytest.raises(KeyError):
        neo4j_client.get_driver()


def _modules_importing_neo4j() -> list[Path]:
    offenders = []
    for path in APP_ROOT.rglob("*.py"):
        if path == APP_ROOT / "db" / "neo4j_client.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                alias.name == "neo4j" or alias.name.startswith("neo4j.")
                for alias in node.names
            ):
                offenders.append(path)
            elif isinstance(node, ast.ImportFrom) and node.module and (
                node.module == "neo4j" or node.module.startswith("neo4j.")
            ):
                offenders.append(path)
    return offenders


def test_no_module_outside_neo4j_client_imports_the_bolt_driver():
    offenders = _modules_importing_neo4j()
    assert offenders == [], (
        f"Only db/neo4j_client.py may import `neo4j` (spec §6.5); "
        f"found imports in: {offenders}"
    )
