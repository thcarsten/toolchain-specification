"""Pytest fixtures for the pipeline generator's test suite.

Plain helper functions and the shared file lists (`parse_extra`,
`compile_pipeline`, `CATALOG_FILES`, `INFERENCE_RULES`, ...) live in
testing_helpers.py, not here — see that module's docstring for why.
Import them from there rather than from this module: `from conftest
import ...` is the pattern that breaks once a second conftest.py exists
under src/rdfine/tests/.

Two groups of fixtures below. The first builds graphs for the edge-case
suite, which supplies its own synthetic pipeline. The second serves the
catalog generator's tests, which work from the committed harvest
snapshot and the real demonstrator pipeline.

`src/` is importable via `pythonpath = src` in pytest.ini; run `pytest`
from the `pipeline generator` directory.
"""

from pathlib import Path

import pytest
from rdflib import Graph

from testing_helpers import CATALOG_FILES, DATA_DIR, NOTEBOOK_FILES, SHAPES_FILE

ROOT = DATA_DIR.parent


@pytest.fixture
def catalog_graph() -> Graph:
    """The framework catalog files only — no shapes, no pipeline
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


@pytest.fixture
def demonstrator_graph(catalog_with_shapes: Graph) -> Graph:
    """Catalog + shapes + the real ``demo:DishacledPipeline`` definition —
    for tests that exercise the actual demonstrator pipeline rather than
    a synthetic snippet."""
    catalog_with_shapes.parse(
        str(DATA_DIR / "pipeline_definition.ttl"),
        publicID="file:///workspace/pipeline/",
    )
    return catalog_with_shapes


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """The ``pipeline generator`` directory."""
    return ROOT


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return DATA_DIR


@pytest.fixture(scope="session")
def notebook_files() -> list[str]:
    return list(NOTEBOOK_FILES)


@pytest.fixture(scope="session")
def snapshot_dir(data_dir: Path) -> Path:
    return data_dir / "rdfc_harvest"


@pytest.fixture(scope="session")
def requests(data_dir: Path):
    from rdfc_catalog_harvest.requests import load_requests

    return load_requests(data_dir / "catalog-rdfc-requests.ttl")


@pytest.fixture(scope="session")
def generated(requests, snapshot_dir: Path) -> str:
    """The catalog text the emitter produces from the committed snapshot."""
    from rdfc_catalog_harvest.emitter import generate

    return generate(requests, snapshot_dir)
