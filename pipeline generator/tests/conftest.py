"""Shared pytest fixtures.

``compilers`` and ``catalog`` are not installed as packages (only
``rdfine`` is, from ``src/rdfine``), so ``src`` goes on the path here the
same way the demo notebook does it.

Run from the ``pipeline generator`` directory:

    PYTHONPATH=src pytest tests/ -q
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# The catalog + pipeline graph, in the order the demo notebook loads it.
#
# Defined once here because several test modules need the same set, and a
# stale copy is a real failure mode: adding catalog-rdfc-manual.ttl broke
# a test that had its own list. ``test_notebook_file_list_matches_this_test``
# keeps this in sync with demo.ipynb.
CATALOG_FILES = [
    "catalog-core.ttl",
    "catalog-ldio.ttl",
    "catalog-rdfc.ttl",
    "catalog-rdfc-manual.ttl",
    "catalog-sw.ttl",
    "pipeline_definition.ttl",
    "catalog-application-profile-shapes.ttl",
]

PIPELINE_ID = "demo:DishacledPipeline"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """The ``pipeline generator`` directory."""
    return ROOT


@pytest.fixture(scope="session")
def catalog_files() -> list[str]:
    return list(CATALOG_FILES)


@pytest.fixture(scope="session")
def data_dir(repo_root: Path) -> Path:
    return repo_root / "data"


@pytest.fixture(scope="session")
def snapshot_dir(data_dir: Path) -> Path:
    return data_dir / "harvest"


@pytest.fixture(scope="session")
def requests(data_dir: Path):
    from catalog.requests import load_requests

    return load_requests(data_dir / "catalog-rdfc-requests.ttl")


@pytest.fixture(scope="session")
def generated(requests, snapshot_dir: Path) -> str:
    """The catalog text the emitter produces from the committed snapshot."""
    from catalog.emitter import generate

    return generate(requests, snapshot_dir)
