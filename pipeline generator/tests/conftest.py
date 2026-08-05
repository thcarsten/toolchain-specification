"""Pytest fixtures for the pipeline generator's edge-case test suite.

Plain helper functions (parse_extra, assert_shacl_violation, etc.) live
in testing_helpers.py, not here — see that module's docstring for why.
"""

import pytest
from rdflib import Graph

from testing_helpers import CATALOG_FILES, DATA_DIR, SHAPES_FILE


@pytest.fixture
def catalog_graph() -> Graph:
    """The four framework catalog files only — no shapes, no pipeline
    definition. Edge-case tests parse their own tiny synthetic pipeline
    into this via `parse_extra`."""
    g = Graph()
    for filename in CATALOG_FILES:
        g.parse(str(DATA_DIR / filename), publicID="file:///workspace/pipeline/")
    return g


@pytest.fixture
def catalog_with_shapes(catalog_graph: Graph) -> Graph:
    """Catalog + the application-profile SHACL shapes, for violation tests."""
    catalog_graph.parse(
        str(DATA_DIR / SHAPES_FILE), publicID="file:///workspace/pipeline/"
    )
    return catalog_graph
