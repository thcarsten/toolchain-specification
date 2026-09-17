"""Tests for ValidationReportCompiler.normalize_config_shapes().

Slice 1e of docs/config-shape-split-plan.md: the catalogs used to
hand-write the `sh:target` that says where a step config lives — 9
blocks in catalog-nifi.ttl, 9 generated into catalog-rdfc.ttl, 1 in
catalog-rdfc-manual.ttl — every one of them pointing at
`p-plan:hasInputVar/tcs:embedded`. With the config contract split in two
that answer depends on the role the shape is attached under, so the
catalogs stopped answering it and this method became the only place that
knows.
"""

from testing_helpers import CATALOG_DIR, CATALOG_FILES, compile_pipeline, parse_extra

PREFIXES = """
@prefix dcat: <http://www.w3.org/ns/dcat#> .
@prefix dct: <http://purl.org/dc/terms/> .
@prefix demo: <http://example.org/example/demonstrator/> .
@prefix ldio: <http://example.org/example/ldio/> .
@prefix p-plan: <http://purl.org/net/p-plan#> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix tcs: <https://w3id.org/toolchain#> .
"""

PIPELINE = PREFIXES + """
demo:Test a tcs:PipelineDefinition .
demo:Out a tcs:InstancePipelineComponent ; prov:specializationOf ldio:ConsoleOut ;
    p-plan:isStepOfPlan demo:Test ;
    p-plan:hasInputVar [
        a tcs:PipelineConfig ;
        tcs:embedded [ ldio:rdf-writer [ ldio:content-type "text/turtle" ] ]
    ] .
"""


def _targets(build) -> dict[str, str]:
    """shape -> the sh:select of the target minted for it."""
    rows = build.select(
        "?shape ?select",
        "?shape sh:target ?target . ?target sh:select ?select .",
    )
    return dict(zip(rows["shape"], rows["select"]))


def test_no_shipped_catalog_hand_writes_a_config_shape_target(catalog_graph):
    """The whole point of 1e. A hand-written target wins over the minted
    one (the guard below leaves an author-supplied target alone), so a
    stray block would silently pin that shape back to the authoring
    contract — validating a body no compiler ever reads."""
    for name in CATALOG_FILES:
        text = (CATALOG_DIR / name).read_text(encoding="utf-8")
        assert "sh:SPARQLTarget" not in text, (
            f"{name} hand-writes a config-shape target; "
            "normalize_config_shapes mints it"
        )


def test_a_compiler_facing_shape_targets_the_compiler_config(catalog_graph):
    parse_extra(catalog_graph, PIPELINE)
    _, build = compile_pipeline(catalog_graph, "demo:Test")

    select = _targets(build)["ldio:ConsoleOutConfigShape"]
    assert "prov:specializationOf ldio:ConsoleOut" in select
    assert "tcs:compilerConfig/tcs:embedded ?this" in select
    assert "p-plan:hasInputVar" not in select


def test_a_user_facing_shape_targets_the_authored_config(catalog_graph):
    """Each role is judged against its own config node. Targeting both at
    the authored one would let a compiler-facing shape pass on a body the
    translation never produced."""
    parse_extra(
        catalog_graph,
        PIPELINE + """
    demo:ConsoleOutAuthoringShape a sh:NodeShape ;
        sh:property [ sh:path ldio:content-type ; sh:maxCount 1 ] .
    ldio:ConsoleOut dcat:qualifiedRelation [
        a dcat:Relationship ;
        dcat:hadRole tcs:userFacingConfigShape ;
        dct:relation demo:ConsoleOutAuthoringShape
    ] ;
        tcs:configTranslation \"\"\"
            CONSTRUCT { ?target ?p ?o . }
            WHERE { ?config tcs:embedded ?source . ?source ?p ?o . }
        \"\"\" .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")

    targets = _targets(build)
    authoring = targets["demo:ConsoleOutAuthoringShape"]
    assert "p-plan:hasInputVar/tcs:embedded ?this" in authoring
    assert "tcs:compilerConfig" not in authoring
    # And the component's other shape still gets its own, distinct one.
    assert (
        "tcs:compilerConfig/tcs:embedded ?this"
        in targets["ldio:ConsoleOutConfigShape"]
    )


def test_an_author_supplied_target_still_wins(catalog_graph):
    """Nothing in the shipped catalogs uses this any more, but the escape
    hatch stays: a shape that knows better keeps its own target."""
    parse_extra(
        catalog_graph,
        PIPELINE + """
    ldio:ConsoleOutConfigShape sh:target [
        a sh:SPARQLTarget ;
        sh:prefixes tcs:prefixes ;
        sh:select "SELECT ?this WHERE { ?this a tcs:Nothing . }"
    ] .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")

    assert _targets(build)["ldio:ConsoleOutConfigShape"] == (
        "SELECT ?this WHERE { ?this a tcs:Nothing . }"
    )
