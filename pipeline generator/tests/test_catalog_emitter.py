"""Emitter behaviour: determinism, validity, and path construction."""

from pathlib import Path

import pytest
from rdflib import Graph

from catalog import emitter
from catalog.model import CatalogRequest, HarvestRecord
from catalog.requests import load_requests


def test_output_is_valid_turtle(generated: str):
    Graph().parse(data=generated, format="turtle", publicID="file:///workspace/pipeline/")


def test_generation_is_byte_stable(requests, snapshot_dir: Path):
    """Same inputs must give identical bytes, or the file cannot be committed."""
    runs = {emitter.generate(requests, snapshot_dir) for _ in range(3)}
    assert len(runs) == 1


def test_committed_catalog_is_current(generated: str, data_dir: Path):
    """`python -m catalog generate --check` in test form.

    Fails when data/catalog-rdfc.ttl was hand-edited or a harvest landed
    without regenerating.
    """
    on_disk = (data_dir / "catalog-rdfc.ttl").read_text(encoding="utf-8")
    assert on_disk == generated, (
        "data/catalog-rdfc.ttl is stale - run `python -m catalog generate`"
    )


def test_generated_file_is_marked_do_not_edit(generated: str):
    assert "GENERATED FILE - DO NOT EDIT" in generated.splitlines()[1]


def _record(**overrides) -> HarvestRecord:
    defaults = dict(
        component="rdfc:Demo",
        source="npm",
        package="@scope/demo",
        resolved_version="1.0.0",
        language="js",
        label="Demo",
        comment="A demo.",
        landing_page="https://example.invalid/demo",
        source_file="processor.ttl",
        module_path=None,
        turtle="",
    )
    return HarvestRecord(**{**defaults, **overrides})


def test_npm_import_path_is_node_modules_relative():
    record = _record(source_file="configs/sdsify.ttl")
    request = CatalogRequest(component="rdfc:Demo", package="@scope/demo")
    assert (
        emitter.owl_imports_path(record, request)
        == "./node_modules/@scope/demo/configs/sdsify.ttl"
    )


def test_python_import_path_climbs_out_of_the_workdir():
    """The ``../`` count is derived from CONTAINER_WORKDIR, not hardcoded.

    ``/workspace/pipeline/`` is two segments deep, so reaching ``/usr``
    takes three ``../`` — the same shape the hand-written catalog used.
    """
    record = _record(language="python", source_file="rdfc_http_out/processor.ttl")
    request = CatalogRequest(component="rdfc:HttpOut", package="rdfc_http_out")
    path = emitter.owl_imports_path(record, request)
    assert path == (
        "../../../usr/local/lib/python3.13/site-packages/rdfc_http_out/processor.ttl"
    )
    # Resolving against the container workdir must land at an absolute path.
    from urllib.parse import urljoin

    resolved = urljoin(f"file://{emitter.CONTAINER_WORKDIR}", path)
    assert resolved == (
        "file:///usr/local/lib/python3.13/site-packages/rdfc_http_out/processor.ttl"
    )


def test_python_version_matches_the_dockerfile(data_dir: Path):
    """PYTHON_VERSION is baked into generated IRIs; drift breaks imports silently.

    Nothing validates an owl:imports value (tcs:RdfcProcessorShape only
    checks sh:minCount 1), so a mismatch between this constant and the
    image's interpreter would produce a catalog that looks fine and a
    pipeline that cannot boot.
    """
    manual = (data_dir / "catalog-rdfc-manual.ttl").read_text(encoding="utf-8")
    assert f"FROM python:{emitter.PYTHON_VERSION}-slim" in manual


def test_container_workdir_matches_graph_reader_basepath():
    """The emitter's base and GraphReader's must agree.

    Relative import IRIs are resolved at parse time against
    GraphReader._basepath / the notebook publicID; the ``../`` count above
    is only correct if that base is this path.
    """
    from rdfine import GraphReader

    assert GraphReader._basepath == f"file://{emitter.CONTAINER_WORKDIR}"


def test_local_package_gets_download_location_not_version():
    """RdfcDockerFileCompiler branches on exactly this distinction."""
    record = _record(language="python", package="rdfc_http_out")
    request = CatalogRequest(
        component="rdfc:HttpOut",
        package="rdfc_http_out",
        download_location="file:///workspace/pipeline/local_processors/rdfc_http_out",
        supplied_by=":pip",
    )
    node = emitter._package_node(record, request)
    assert "spdx:downloadLocation" in node
    assert "spdx:versionInfo" not in node
    assert "spdx:suppliedBy :pip" in node


def test_missing_harvest_record_names_the_fix(tmp_path: Path):
    request = CatalogRequest(component="rdfc:Nope", package="nope")
    with pytest.raises(FileNotFoundError, match="catalog harvest"):
        emitter.generate([request], tmp_path)


def test_requests_are_order_independent(data_dir: Path, snapshot_dir: Path):
    """Shuffling the request file must not change the output."""
    requests = load_requests(data_dir / "catalog-rdfc-requests.ttl")
    forward = emitter.generate(requests, snapshot_dir)
    backward = emitter.generate(list(reversed(requests)), snapshot_dir)
    assert forward == backward
