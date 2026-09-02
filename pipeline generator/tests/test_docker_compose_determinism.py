"""`DockerComposeCompiler` must emit the same bytes for the same input.

It aggregates one compose fragment per `tcs:DockerComposeConfig`, and the
config list came from a SPARQL SELECT whose result order is not
guaranteed. Two things depended on that order — which fragment wins a
name collision, and (since the YAML dumper preserves insertion order)
the order services appear in the file — so the same build graph produced
a different, if equivalent, `docker-compose.yml` on each run.

That made the committed `out/` artifact unreviewable: every regeneration
showed dozens of moved lines with no change in meaning.
"""

from pathlib import Path

import pytest

from testing_helpers import PIPELINE_ID, NOTEBOOK_FILES, load_reader
import yaml


def _compose() -> str:
    from compilers import PipelineGenerator, FileMaterializer

    build = PipelineGenerator(PIPELINE_ID).compile()
    files = FileMaterializer(build).files
    return next(
        row["content"]
        for _, row in files.iterrows()
        if row["filename"] == "docker-compose.yml"
    )


def test_repeated_compilation_is_byte_identical():
    first = _compose()
    second = _compose()
    assert first == second


def test_committed_output_matches_a_fresh_run(repo_root: Path):
    """Guards the checked-in artifact against silent drift."""
    committed = (repo_root / "out/dishacled-full/docker-compose.yml").read_text(
        encoding="utf-8"
    )
    assert committed == _compose()


def test_services_are_emitted_in_a_canonical_order():
    """Sorted by name, so a renamed config cannot reshuffle the file."""
    services = list(yaml.safe_load(_compose())["services"])
    assert services == sorted(services)
    assert len(services) > 1, "fixture should aggregate several services"


@pytest.mark.parametrize(
    "section", ["services", "volumes", "networks", "configs", "secrets"]
)
def test_canonical_sorts_every_name_keyed_section(section: str):
    from compilers.core.docker_compose_compiler import _canonical

    result = _canonical({section: {"zebra": 1, "alpha": 2, "mango": 3}})
    assert list(result[section]) == ["alpha", "mango", "zebra"]


def test_canonical_leaves_other_keys_untouched():
    """Only name-to-body mappings are sorted; scalars and lists are not."""
    from compilers.core.docker_compose_compiler import _canonical

    original = {"version": "3.9", "x-custom": ["b", "a"], "services": {"b": 1, "a": 2}}
    result = _canonical(original)
    assert result["version"] == "3.9"
    assert result["x-custom"] == ["b", "a"]
    assert list(result["services"]) == ["a", "b"]


def test_canonical_does_not_reorder_inside_a_service_body():
    """Body key order comes from the catalog literal — the author's choice."""
    from compilers.core.docker_compose_compiler import _canonical

    body = {"image": "x", "container_name": "y", "build": "z"}
    result = _canonical({"services": {"svc": body}})
    assert list(result["services"]["svc"]) == ["image", "container_name", "build"]
