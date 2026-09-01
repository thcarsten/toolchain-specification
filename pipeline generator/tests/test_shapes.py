"""The four shape rewrites, each against a minimal upstream fixture.

Every rule here exists because a literal copy of upstream Turtle would
produce a constraint the toolchain can never satisfy. The tests assert
both halves of each rewrite: the constraint that replaces it, and the
annotation that preserves what upstream said.
"""

from rdflib import Graph, URIRef

from rdfc_catalog_harvest import shapes

RDFC = "https://w3id.org/rdf-connect#"

PREAMBLE = """
@prefix rdfc: <https://w3id.org/rdf-connect#> .
@prefix rdfl: <https://w3id.org/rdf-lens/ontology#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
"""


def _translate(body: str, component: str = "Demo"):
    graph = Graph()
    graph.parse(data=PREAMBLE + body, format="turtle")
    return shapes.translate(graph, URIRef(f"{RDFC}{component}"))


def _flat(shape) -> list[tuple[str, str]]:
    """All predicate/object pairs across a shape's property shapes."""
    return [pair for prop in shape.properties for pair in prop]


def test_reader_and_writer_collapse_to_channel():
    # Upstream's Reader/Writer distinction cannot match: the toolchain
    # only ever asserts tcs:Channel, inferred from readsFrom/writesTo.
    shape = _translate("""
    rdfc:Demo rdfc:jsImplementationOf rdfc:Processor .
    [] a sh:NodeShape ; sh:targetClass rdfc:Demo ;
       sh:property [ sh:path rdfc:reader ; sh:name "reader" ; sh:class rdfc:Reader ] ,
                   [ sh:path rdfc:writer ; sh:name "writer" ; sh:class rdfc:Writer ] .
    """)
    pairs = _flat(shape)
    assert ("sh:class", "tcs:Channel") in pairs
    assert pairs.count(("sh:class", "tcs:Channel")) == 2
    # Direction preserved, non-constraining.
    assert ("tcs:upstreamClass", "rdfc:Reader") in pairs
    assert ("tcs:upstreamClass", "rdfc:Writer") in pairs
    assert not any(p == "sh:class" and o != "tcs:Channel" for p, o in pairs)


def test_required_channel_parameter_loses_its_min_count():
    # A channel parameter is required of the *pipeline*, not of the
    # author: RdfcConfigCompiler fills rdfc:reader in from tcs:readsFrom,
    # so the authored config deliberately omits it and upstream's
    # sh:minCount 1 would fail every pipeline in the repo. The class
    # constraint still applies to whatever the author does write; only
    # the obligation moves, and the original is kept as provenance.
    shape = _translate("""
    rdfc:Demo rdfc:jsImplementationOf rdfc:Processor .
    [] a sh:NodeShape ; sh:targetClass rdfc:Demo ;
       sh:property [ sh:path rdfc:reader ; sh:name "reader" ;
                     sh:class rdfc:Reader ; sh:minCount 1 ; sh:maxCount 1 ] ,
                   [ sh:path rdfc:level ; sh:name "level" ;
                     sh:datatype xsd:string ; sh:minCount 1 ] .
    """)
    pairs = _flat(shape)
    assert ("tcs:upstreamMinCount", "1") in pairs
    assert ("sh:class", "tcs:Channel") in pairs
    # maxCount is untouched — it still holds for whatever is written.
    assert ("sh:maxCount", "1") in pairs
    # Non-channel parameters keep their obligation: nothing fills those in.
    assert pairs.count(("sh:minCount", "1")) == 1


def test_nested_config_class_becomes_node_reference():
    # The pipeline embeds config as an untyped blank node, so sh:class
    # cannot hold; sh:node against a named shape can.
    shape = _translate("""
    rdfc:Demo rdfc:jsImplementationOf rdfc:Processor .
    [] a sh:NodeShape ; sh:targetClass rdfc:Demo ;
       sh:property [ sh:path rdfc:cfg ; sh:name "cfg" ; sh:class rdfc:DemoConfig ] .
    [] a sh:NodeShape ; sh:targetClass rdfc:DemoConfig ;
       sh:property [ sh:path rdfc:mode ; sh:name "mode" ; sh:datatype xsd:string ] .
    """)
    assert ("sh:node", ":DemoConfigShape") in _flat(shape)
    assert not any(p == "sh:class" for p, _ in _flat(shape))

    # The nested shape is emitted, and targeted by the property path
    # rather than by class.
    assert len(shape.nested) == 1
    nested = shape.nested[0]
    assert nested.iri == ":DemoConfigShape"
    assert nested.target == [("sh:targetObjectsOf", "rdfc:cfg")]
    assert ("sh:path", "rdfc:mode") in _flat(nested)


def test_known_foreign_class_gets_a_node_reference():
    """rdfl:PathLens is modelled by hand, so it is enforced, not just noted.

    The path is consumed at execution time to extract values from each
    record, so an invalid one fails at runtime — worth checking rather
    than documenting.
    """
    shape = _translate("""
    rdfc:Demo rdfc:jsImplementationOf rdfc:Processor .
    [] a sh:NodeShape ; sh:targetClass rdfc:Demo ;
       sh:property [ sh:path rdfc:lens ; sh:name "lens" ; sh:class rdfl:PathLens ] .
    """)
    pairs = _flat(shape)
    assert ("sh:node", ":ShaclPathShape") in pairs
    # Original class kept as provenance; never as an unsatisfiable constraint.
    assert (
        "tcs:upstreamClass",
        "<https://w3id.org/rdf-lens/ontology#PathLens>",
    ) in pairs
    assert not any(p == "sh:class" for p, _ in pairs)


def test_unknown_foreign_class_is_demoted_to_annotation():
    """The fallback: no shape for it, so record rather than constrain."""
    shape = _translate("""
    @prefix other: <https://example.invalid/vocab#> .
    rdfc:Demo rdfc:jsImplementationOf rdfc:Processor .
    [] a sh:NodeShape ; sh:targetClass rdfc:Demo ;
       sh:property [ sh:path rdfc:x ; sh:name "x" ; sh:class other:Mystery ] .
    """)
    pairs = _flat(shape)
    assert not any(p == "sh:class" for p, _ in pairs)
    assert not any(p == "sh:node" for p, _ in pairs)
    assert ("tcs:upstreamClass", "<https://example.invalid/vocab#Mystery>") in pairs


def test_external_shape_registry_targets_a_real_shape(catalog_data_dir):
    """Every EXTERNAL_SHAPES value must actually exist in the manual file.

    A typo here would emit sh:node at a shape that does not exist, which
    SHACL treats as vacuously satisfied — a constraint that silently
    checks nothing.
    """
    from rdflib import Graph
    from rdflib.namespace import RDF

    from rdfc_catalog_harvest.shapes import EXTERNAL_SHAPES

    graph = Graph()
    graph.parse(catalog_data_dir / "catalog-rdfc-manual.ttl")
    declared = {
        str(s)
        for s in graph.subjects(
            RDF.type, URIRef("http://www.w3.org/ns/shacl#NodeShape")
        )
    }
    for klass, shape_iri in EXTERNAL_SHAPES.items():
        expanded = shape_iri.replace(":", "http://example.org/example/", 1)
        assert expanded in declared, f"{klass} maps to missing shape {shape_iri}"


def test_xsd_iri_becomes_nodekind_iri():
    # xsd:iri is not a datatype; as written the constraint demands a
    # literal that no IRI can be.
    shape = _translate("""
    rdfc:Demo rdfc:jsImplementationOf rdfc:Processor .
    [] a sh:NodeShape ; sh:targetClass rdfc:Demo ;
       sh:property [ sh:path rdfc:stream ; sh:name "s" ; sh:datatype xsd:iri ] .
    """)
    pairs = _flat(shape)
    assert ("sh:nodeKind", "sh:IRI") in pairs
    assert ("tcs:upstreamDatatype", "xsd:iri") in pairs
    assert not any(p == "sh:datatype" for p, _ in pairs)


def test_real_datatypes_pass_through_untouched():
    shape = _translate("""
    rdfc:Demo rdfc:jsImplementationOf rdfc:Processor .
    [] a sh:NodeShape ; sh:targetClass rdfc:Demo ;
       sh:property [ sh:path rdfc:n ; sh:name "n" ; sh:datatype xsd:integer ;
                     sh:minCount 1 ; sh:maxCount 1 ] .
    """)
    pairs = _flat(shape)
    assert ("sh:datatype", "xsd:integer") in pairs
    assert ("sh:minCount", "1") in pairs
    assert ("sh:maxCount", "1") in pairs


def test_component_shape_carries_sparql_target():
    # sh:targetObjectsOf is not expressive enough: the focus node is two
    # hops from the step and must be scoped to this component.
    shape = _translate("""
    rdfc:Demo rdfc:jsImplementationOf rdfc:Processor .
    [] a sh:NodeShape ; sh:targetClass rdfc:Demo ;
       sh:property [ sh:path rdfc:x ; sh:name "x" ; sh:datatype xsd:string ] .
    """)
    assert len(shape.target) == 1
    predicate, rendered = shape.target[0]
    assert predicate == "sh:target"
    assert "sh:SPARQLTarget" in rendered
    assert "prov:specializationOf rdfc:Demo" in rendered
    assert "p-plan:hasInputVar/tcs:embedded ?this" in rendered


def test_processor_without_a_shape_returns_none():
    # A parameterless processor is legitimate, not an error.
    assert _translate("rdfc:Demo rdfc:jsImplementationOf rdfc:Processor .") is None


def test_sibling_processors_in_the_same_file_are_not_pulled_in():
    # Several packages declare more than one processor per file. Only the
    # requested component's shape may be emitted.
    shape = _translate("""
    rdfc:Demo rdfc:jsImplementationOf rdfc:Processor .
    rdfc:Other rdfc:jsImplementationOf rdfc:Processor .
    [] a sh:NodeShape ; sh:targetClass rdfc:Demo ;
       sh:property [ sh:path rdfc:mine ; sh:name "mine" ; sh:datatype xsd:string ] .
    [] a sh:NodeShape ; sh:targetClass rdfc:Other ;
       sh:property [ sh:path rdfc:theirs ; sh:name "theirs" ; sh:datatype xsd:string ] .
    """)
    paths = [o for p, o in _flat(shape) if p == "sh:path"]
    assert paths == ["rdfc:mine"]
    assert shape.nested == []


def test_property_order_is_stable_across_runs():
    body = """
    rdfc:Demo rdfc:jsImplementationOf rdfc:Processor .
    [] a sh:NodeShape ; sh:targetClass rdfc:Demo ;
       sh:property [ sh:path rdfc:zebra ; sh:name "z" ; sh:datatype xsd:string ] ,
                   [ sh:path rdfc:alpha ; sh:name "a" ; sh:datatype xsd:string ] ,
                   [ sh:path rdfc:mango ; sh:name "m" ; sh:datatype xsd:string ] .
    """
    runs = [[o for p, o in _flat(_translate(body)) if p == "sh:path"] for _ in range(5)]
    assert runs[0] == ["rdfc:alpha", "rdfc:mango", "rdfc:zebra"]
    assert all(run == runs[0] for run in runs)
