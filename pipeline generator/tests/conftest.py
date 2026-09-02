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

from testing_helpers import (
    CATALOG_DIR,
    CATALOG_FILES,
    DATA_DIR,
    NOTEBOOK_FILES,
    PIPELINES_DIR,
    SHAPES_FILE,
)

ROOT = DATA_DIR.parent


def _copy_graph(g: Graph) -> Graph:
    """A cheap in-memory duplicate of ``g``, including namespace bindings.

    ``Graph() + g`` alone only copies triples — namespace prefixes
    bound during Turtle parsing (which ``PrefixStore`` reads back off
    the graph) are lost otherwise.
    """
    copy = Graph() + g
    for prefix, namespace in g.namespaces():
        copy.bind(prefix, namespace)
    return copy


# Function-scoped fixtures below hand out a private copy of the
# corresponding session-scoped graph below, so each test can freely
# mutate its copy (e.g. via `parse_extra`) without paying the cost of
# re-parsing the on-disk Turtle (~5k lines across the catalog files)
# from scratch. Copying an already-parsed in-memory graph is far
# cheaper than reparsing it.


@pytest.fixture(scope="session")
def _session_catalog_graph() -> Graph:
    g = Graph()
    for filename in CATALOG_FILES:
        g.parse(str(CATALOG_DIR / filename), publicID="file:///workspace/pipeline/")
    return g


@pytest.fixture(scope="session")
def _session_catalog_with_shapes(_session_catalog_graph: Graph) -> Graph:
    g = _copy_graph(_session_catalog_graph)
    g.parse(str(CATALOG_DIR / SHAPES_FILE), publicID="file:///workspace/pipeline/")
    return g


@pytest.fixture(scope="session")
def _session_demonstrator_graph(_session_catalog_with_shapes: Graph) -> Graph:
    g = _copy_graph(_session_catalog_with_shapes)
    g.parse(
        str(PIPELINES_DIR / "pipeline_definition.ttl"),
        publicID="file:///workspace/pipeline/",
    )
    return g


@pytest.fixture
def catalog_graph(_session_catalog_graph: Graph) -> Graph:
    """The framework catalog files only — no shapes, no pipeline
    definition. Edge-case tests parse their own tiny synthetic pipeline
    into this via `parse_extra`."""
    return _copy_graph(_session_catalog_graph)


@pytest.fixture
def catalog_with_shapes(_session_catalog_with_shapes: Graph) -> Graph:
    """Catalog + the application-profile SHACL shapes, for violation tests."""
    return _copy_graph(_session_catalog_with_shapes)


@pytest.fixture
def demonstrator_graph(_session_demonstrator_graph: Graph) -> Graph:
    """Catalog + shapes + the real ``demo:DishacledPipeline`` definition —
    for tests that exercise the actual demonstrator pipeline rather than
    a synthetic snippet."""
    return _copy_graph(_session_demonstrator_graph)


@pytest.fixture(scope="module")
def compiled_demonstrator(_session_demonstrator_graph: Graph):
    """``(runner, GraphReader)`` from compiling the real demonstrator
    pipeline, computed once per test module instead of once per test.

    Only for tests that inspect the compiled result read-only, without
    mutating the input graph first (e.g. via `parse_extra`) or patching
    compiler behaviour (e.g. via `monkeypatch`) — those still need their
    own `compile_pipeline(demonstrator_graph, ...)` call. Safe to share
    the same input graph across the whole module unguarded because
    `compile_pipeline` never mutates it.
    """
    from testing_helpers import compile_pipeline

    return compile_pipeline(_session_demonstrator_graph, "demo:DishacledPipeline")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """The ``pipeline generator`` directory."""
    return ROOT


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return DATA_DIR


@pytest.fixture(scope="session")
def catalog_data_dir() -> Path:
    return CATALOG_DIR


@pytest.fixture(scope="session")
def notebook_files() -> list[str]:
    return list(NOTEBOOK_FILES)


@pytest.fixture(scope="session")
def snapshot_dir(catalog_data_dir: Path) -> Path:
    return catalog_data_dir / "rdfc_harvest"


@pytest.fixture(scope="session")
def requests(catalog_data_dir: Path):
    from rdfc_catalog_harvest.requests import load_requests

    return load_requests(catalog_data_dir / "catalog-rdfc-requests.ttl")


@pytest.fixture(scope="session")
def generated(requests, snapshot_dir: Path) -> str:
    """The catalog text the emitter produces from the committed snapshot."""
    from rdfc_catalog_harvest.emitter import generate

    return generate(requests, snapshot_dir)
