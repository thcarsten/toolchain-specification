"""Shared pytest fixtures.

``compilers`` is not installed as a package (only ``rdfine`` is, from
``src/rdfine``), so ``src`` goes on the path here the same way the demo
notebook does it.

Run from the ``pipeline generator`` directory:

    PYTHONPATH=src pytest tests/ -q
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """The ``pipeline generator`` directory."""
    return ROOT


@pytest.fixture(scope="session")
def data_dir(repo_root: Path) -> Path:
    return repo_root / "data"
