"""`:ShaclPathShape` — the value space of `rdfl:PathLens`.

This is the executable spec of which property paths a processor config
may use. It matters at runtime, not just at validation time: rdf-lens
walks the path to extract a value from every record, so a path the
library cannot interpret is a live extraction failure. These cases mirror
the grammar `ShaclPath` implements in rdf-lens `src/shacl.ts`.
"""

import warnings
from pathlib import Path

import pyshacl
import pytest
from rdflib import Graph

PREFIXES = """
@prefix : <http://example.org/example/> .
@prefix ex: <http://example.org/x#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix sosa: <http://www.w3.org/ns/sosa/> .
"""

# Targets :probe values at :ShaclPathShape, standing in for the generated
# `sh:path tm:path ; sh:node :ShaclPathShape`.
PROBE = """
[] a sh:NodeShape ;
   sh:targetSubjectsOf :probe ;
   sh:property [ sh:path :probe ; sh:minCount 1 ; sh:node :ShaclPathShape ] .
"""

ACCEPTED = {
    "predicate path (a bare IRI)": ":s :probe sosa:hasSimpleResult .",
    "sequence path (RDF list)": ":s :probe ( sosa:hasResult sosa:hasSimpleResult ) .",
    "inverse path": ":s :probe [ sh:inversePath sosa:hasSimpleResult ] .",
    "inverse of a sequence": ":s :probe [ sh:inversePath ( ex:a ex:b ) ] .",
    "alternative path": ":s :probe [ sh:alternativePath ( ex:a ex:b ) ] .",
    "zeroOrMorePath": ":s :probe [ sh:zeroOrMorePath ex:a ] .",
    "zeroOrOnePath": ":s :probe [ sh:zeroOrOnePath ex:a ] .",
    "nested multi over inverse": ":s :probe [ sh:zeroOrMorePath [ sh:inversePath ex:a ] ] .",
}

REJECTED = {
    # The mistake upstream's `sh:datatype xsd:iri` actively invites.
    "quoted string instead of an IRI": ':s :probe "sosa:hasSimpleResult" .',
    "numeric literal": ":s :probe 42 .",
    "misspelled path predicate": ":s :probe [ sh:inverse ex:a ] .",
    "empty blank node": ":s :probe [ ] .",
    # sh:alternativePath takes a list, not a single path.
    "alternativePath given a bare IRI": ":s :probe [ sh:alternativePath ex:a ] .",
    # Valid SHACL, but rdf-lens does not implement it: ShaclPath lists
    # MultiPath(SHACL.zeroOrMorePath, 1) where oneOrMorePath was meant, so
    # the term appears nowhere in the library.
    "oneOrMorePath (unimplemented upstream)": ":s :probe [ sh:oneOrMorePath ex:a ] .",
}


@pytest.fixture(scope="module")
def shapes(data_dir: Path) -> Graph:
    """`:ShaclPathShape` as actually declared in the catalog."""
    graph = Graph()
    graph.parse(data_dir / "catalog-rdfc-manual.ttl")
    graph.parse(data=PREFIXES + PROBE, format="turtle")
    return graph


def _conforms(shapes: Graph, body: str) -> bool:
    with warnings.catch_warnings():
        # Recursion support is optional in SHACL; pySHACL warns but
        # handles it. Nesting is what makes the grammar recursive.
        warnings.simplefilter("ignore")
        conforms, _, _ = pyshacl.validate(
            Graph().parse(data=PREFIXES + body, format="turtle"),
            shacl_graph=shapes,
            advanced=True,
        )
    return conforms


@pytest.mark.parametrize("body", ACCEPTED.values(), ids=list(ACCEPTED))
def test_valid_paths_are_accepted(shapes: Graph, body: str):
    assert _conforms(shapes, body)


@pytest.mark.parametrize("body", REJECTED.values(), ids=list(REJECTED))
def test_invalid_paths_are_rejected(shapes: Graph, body: str):
    assert not _conforms(shapes, body)


def test_demonstrator_path_needs_no_recursion(shapes: Graph):
    """The common case must not trip SHACL's optional recursion support.

    A bare IRI matches the first sh:or alternative, so the recursive
    branches are never entered — which is why validating the real catalog
    produces no ShapeRecursionWarning.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        pyshacl.validate(
            Graph().parse(
                data=PREFIXES + ":s :probe sosa:hasSimpleResult .", format="turtle"
            ),
            shacl_graph=shapes,
            advanced=True,
        )
    assert "ShapeRecursionWarning" not in {w.category.__name__ for w in caught}
