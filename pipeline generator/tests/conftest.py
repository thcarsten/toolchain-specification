"""Shared pytest fixtures.

``compilers`` and ``rdfc_catalog_harvest`` are not installed as packages (only
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

# Both rule files, in the order the demo notebook applies them. Same
# reasoning as CATALOG_FILES, with a sharper failure mode: a caller that
# loads only the neutral file still infers *most* things, so the
# topology cross-check goes quiet instead of failing.
# ``test_notebook_rule_list_matches_this_test`` keeps this in sync too.
INFERENCE_RULES = [
    "inference_rules.yaml",
    "rdfc_inference_rules.yaml",
]

PIPELINE_ID = "demo:DishacledPipeline"


def infer_all(graph, data_dir: Path):
    """Apply every rule file to ``graph``, the way the notebook does."""
    from rdfine import GraphReader

    reader = GraphReader(graph)
    for name in INFERENCE_RULES:
        reader = reader.infer(str(data_dir / name))
    return reader


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
