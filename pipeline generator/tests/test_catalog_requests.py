"""Request-file parsing, including the errors it must not swallow."""

import pytest

from catalog.requests import load_requests

PREAMBLE = """
@prefix : <http://example.org/example/> .
@prefix rdfc: <https://w3id.org/rdf-connect#> .
@prefix spdx: <http://spdx.org/rdf/terms#> .
@prefix tcs: <https://w3id.org/toolchain#> .
"""


def _write(tmp_path, body: str):
    path = tmp_path / "requests.ttl"
    path.write_text(PREAMBLE + body, encoding="utf-8")
    return path


def test_minimal_request_needs_only_a_package(tmp_path):
    requests = load_requests(
        _write(tmp_path, 'rdfc:Demo a tcs:CatalogRequest ; tcs:package "demo" .')
    )
    assert len(requests) == 1
    assert requests[0].component == "rdfc:Demo"
    assert requests[0].package == "demo"
    assert requests[0].version is None


def test_all_optional_fields_round_trip(tmp_path):
    requests = load_requests(
        _write(
            tmp_path,
            """
    rdfc:Demo a tcs:CatalogRequest ;
        tcs:package "demo" ;
        spdx:versionInfo "^1.2.3" ;
        tcs:fromPath "some/dir" ;
        spdx:downloadLocation "file:///pkg" ;
        spdx:suppliedBy ":pip" ;
        tcs:sourceFile "configs/x.ttl" .
    """,
        )
    )
    request = requests[0]
    assert request.version == "^1.2.3"
    assert request.from_path == "some/dir"
    assert request.download_location == "file:///pkg"
    assert request.supplied_by == ":pip"
    assert request.source_file == "configs/x.ttl"


def test_requests_come_back_sorted(tmp_path):
    requests = load_requests(
        _write(
            tmp_path,
            """
    rdfc:Zulu a tcs:CatalogRequest ; tcs:package "z" .
    rdfc:Alpha a tcs:CatalogRequest ; tcs:package "a" .
    rdfc:Mike a tcs:CatalogRequest ; tcs:package "m" .
    """,
        )
    )
    assert [r.component for r in requests] == [
        "rdfc:Alpha",
        "rdfc:Mike",
        "rdfc:Zulu",
    ]


def test_typo_in_a_predicate_is_an_error(tmp_path):
    """A misspelled predicate must not silently drop the field."""
    with pytest.raises(ValueError, match="unrecognised predicate"):
        load_requests(
            _write(
                tmp_path,
                'rdfc:Demo a tcs:CatalogRequest ; tcs:packge "demo" .',
            )
        )


def test_request_with_no_source_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="tcs:package"):
        load_requests(
            _write(tmp_path, 'rdfc:Demo a tcs:CatalogRequest ; spdx:versionInfo "^1" .')
        )


def test_blank_node_request_is_an_error(tmp_path):
    """The subject names the component, so it has to be an IRI."""
    with pytest.raises(ValueError, match="must be an IRI"):
        load_requests(
            _write(tmp_path, '[] a tcs:CatalogRequest ; tcs:package "demo" .')
        )


def test_non_request_subjects_are_ignored(tmp_path):
    requests = load_requests(
        _write(
            tmp_path,
            """
    rdfc:Demo a tcs:CatalogRequest ; tcs:package "demo" .
    rdfc:NotARequest a tcs:PipelineComponent ; tcs:package "other" .
    """,
        )
    )
    assert [r.component for r in requests] == ["rdfc:Demo"]


def test_real_request_file_covers_the_expected_components(requests):
    components = {r.component for r in requests}
    assert components == {
        "rdfc:HttpFetch",
        "rdfc:HttpOut",
        "rdfc:HttpServer",
        "rdfc:LogProcessorJs",
        "rdfc:LogProcessorPy",
        "rdfc:SPARQLIngest",
        "rdfc:Sdsify",
        "rdfc:SkolemizationProcessor",
        "tm:ThresholdMonitorJs",
    }


def test_every_published_request_pins_a_version(requests):
    """Version is the one non-derivable field; an unpinned entry would
    let a harvest silently jump majors."""
    for request in requests:
        if request.download_location is None:
            assert request.version, f"{request.component} has no spdx:versionInfo"
