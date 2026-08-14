"""Read and write the checked-in harvest snapshot.

Each component gets two files in the snapshot directory:

- ``<slug>.ttl`` — the upstream definition, byte-for-byte as published.
- ``<slug>.json`` — the registry facts around it (resolved version,
  repository URL, which file inside the package it came from).

Both are meant to be committed. That is what makes the generated
catalog reviewable: a diff shows whether a change came from upstream
(the ``.ttl`` moved) or from a policy edit (the request file moved), and
regeneration needs no network.
"""

from __future__ import annotations

import json
from pathlib import Path

from .model import HarvestRecord

_METADATA_FIELDS = (
    "component",
    "source",
    "package",
    "resolved_version",
    "language",
    "label",
    "comment",
    "landing_page",
    "source_file",
    "module_path",
)


def _slug(component: str) -> str:
    return component.replace(":", "_").replace("/", "_")


def write_record(directory: Path, record: HarvestRecord) -> tuple[Path, Path]:
    """Write ``record`` into ``directory``, returning the two paths."""
    directory.mkdir(parents=True, exist_ok=True)
    slug = _slug(record.component)

    turtle_path = directory / f"{slug}.ttl"
    turtle_path.write_text(record.turtle, encoding="utf-8")

    metadata = {field: getattr(record, field) for field in _METADATA_FIELDS}
    metadata_path = directory / f"{slug}.json"
    metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return metadata_path, turtle_path


def read_record(directory: Path, component: str) -> HarvestRecord:
    """Load one component's record.

    Raises:
        FileNotFoundError: with a pointer at ``catalog harvest``, since a
            missing record almost always means the request file gained an
            entry that was never harvested.
    """
    slug = _slug(component)
    metadata_path = directory / f"{slug}.json"
    turtle_path = directory / f"{slug}.ttl"
    if not metadata_path.exists() or not turtle_path.exists():
        raise FileNotFoundError(
            f"no harvest record for {component} in {directory}. "
            "Run `python -m rdfc_catalog_harvest harvest` to fetch it."
        )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    return HarvestRecord(
        turtle=turtle_path.read_text(encoding="utf-8"),
        **{field: metadata.get(field) for field in _METADATA_FIELDS},
    )
