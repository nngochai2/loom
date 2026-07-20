"""The one door to Neo4j (spec §6.5). All Cypher goes through this module —
sinks and API modules call it; nothing else may import the `neo4j` driver
(enforced by tests/test_neo4j_client.py's repo-wide scan).

Credentials come from the environment (NEO4J_URI / NEO4J_USER /
NEO4J_PASSWORD) and are never hardcoded — see spec §3's security invariant.
"""

from __future__ import annotations

import os

from neo4j import Driver, GraphDatabase

_driver: Driver | None = None


def get_driver() -> Driver:
    """Return the process-wide bolt driver, creating it on first use."""
    global _driver
    if _driver is None:
        uri = os.environ["NEO4J_URI"]
        user = os.environ["NEO4J_USER"]
        password = os.environ["NEO4J_PASSWORD"]
        _driver = GraphDatabase.driver(uri, auth=(user, password))
    return _driver


def close_driver() -> None:
    """Close and discard the process-wide driver, if one was created."""
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None
