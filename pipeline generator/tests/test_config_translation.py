"""`ConfigTranslator` derives a step's compiler-facing config from its
authored one.

The split exists so an author can omit what the generator infers and a
component can hard-code what the author must not set. Slice 1 of
docs/config-shape-split-plan.md puts the mechanism in place without
changing behaviour, so most of what is worth pinning here is that the
identity path is *exactly* an identity — same content, but not the same
blank nodes, because sharing them would let a later injection into the
compiler-facing config mutate the author's.
"""

import pytest

from testing_helpers import compile_pipeline, parse_extra

PREFIXES = """
@prefix dcat: <http://www.w3.org/ns/dcat#> .
@prefix dct: <http://purl.org/dc/terms/> .
@prefix demo: <http://example.org/example/demonstrator/> .
@prefix ldio: <http://example.org/example/ldio/> .
@prefix p-plan: <http://purl.org/net/p-plan#> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix rdfc: <https://w3id.org/rdf-connect#> .
@prefix tcs: <https://w3id.org/toolchain#> .
"""

PIPELINE = PREFIXES + """
demo:Test a tcs:PipelineDefinition .
demo:In a tcs:InstancePipelineComponent ; prov:specializationOf ldio:HttpInPoller ;
    p-plan:isStepOfPlan demo:Test ;
    p-plan:hasInputVar [
        a tcs:PipelineConfig ;
        tcs:embedded [ ldio:url "http://a" ; ldio:cron "0 0 * * * *" ]
    ] ;
    tcs:writesTo demo:ch1 .
demo:Out a tcs:InstancePipelineComponent ; prov:specializationOf ldio:ConsoleOut ;
    p-plan:isStepOfPlan demo:Test ;
    p-plan:hasInputVar [
        a tcs:PipelineConfig ;
        tcs:embedded [ ldio:rdf-writer [ ldio:content-type "text/turtle" ] ]
    ] ;
    tcs:readsFrom demo:ch1 .
"""


def _translated(build):
    """step -> (authored config, compiler-facing config)."""
    rows = build.select(
        "?step ?authored ?compiler",
        """
        ?step a tcs:InstancePipelineComponent ;
              p-plan:hasInputVar ?authored ;
              tcs:compilerConfig ?compiler .
        """,
    )
    return {
        row["step"]: (row["authored"], row["compiler"]) for _, row in rows.iterrows()
    }


def test_every_configured_step_gets_a_compiler_facing_config(catalog_graph):
    parse_extra(catalog_graph, PIPELINE)
    _, build = compile_pipeline(catalog_graph, "demo:Test")

    unconfigured = build.select(
        "?step",
        """
        ?step a tcs:InstancePipelineComponent ; p-plan:hasInputVar ?config .
        FILTER NOT EXISTS { ?step tcs:compilerConfig ?compiler_config }
        """,
    )
    assert unconfigured.empty, f"untranslated steps: {list(unconfigured['step'])}"
    assert set(_translated(build)) >= {"demo:In", "demo:Out"}


def test_identity_copy_preserves_the_authored_body(catalog_graph):
    parse_extra(catalog_graph, PIPELINE)
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    from compilers.utils import extract_config

    # ldio:ConsoleOut declares no tcs:userFacingConfigShape, so its
    # compiler-facing config is an identity copy. Read both through
    # extract_config: a translated config's tcs:embedded root is named
    # rather than blank, and reading it back is exactly where that
    # difference could bite.
    authored, compiler = _translated(build)["demo:Out"]
    authored_body = extract_config(build, authored)
    compiler_body = extract_config(build, compiler)
    for body in (authored_body, compiler_body):
        body.pop("@id", None)
    assert compiler_body == authored_body
    assert compiler_body != {}, "a config that reads as empty is the failure mode"


def test_the_two_configs_share_no_blank_nodes(catalog_graph):
    """The point of the split: injecting into one must not touch the other."""
    parse_extra(catalog_graph, PIPELINE)
    _, build = compile_pipeline(catalog_graph, "demo:Test")

    shared = build.select(
        "?authored ?compiler ?node",
        """
        ?step p-plan:hasInputVar ?authored ; tcs:compilerConfig ?compiler .
        ?authored tcs:embedded ?node .
        ?compiler tcs:embedded ?node .
        """,
    )
    assert shared.empty, "compiler-facing config reuses the authored body"


def test_a_user_facing_shape_without_a_translation_raises(catalog_graph):
    """Two declared contracts and nothing to bridge them is an authoring
    error, not a licence to identity-copy: the copy would not satisfy the
    compiler-facing shape."""
    parse_extra(
        catalog_graph,
        PIPELINE + """
    ldio:ConsoleOut dcat:qualifiedRelation [
        a dcat:Relationship ;
        dcat:hadRole tcs:userFacingConfigShape ;
        dct:relation demo:SomeAuthoringShape
    ] .
    """,
    )
    with pytest.raises(ValueError, match="tcs:configTranslation"):
        compile_pipeline(catalog_graph, "demo:Test")


def test_a_translation_query_replaces_the_identity_copy(catalog_graph):
    """The mechanism slice 2 relocates RDF-Connect's channel injection
    into: the author omits the channel key and the query supplies it."""
    parse_extra(
        catalog_graph,
        PIPELINE + """
    ldio:HttpInPoller dcat:qualifiedRelation [
        a dcat:Relationship ;
        dcat:hadRole tcs:userFacingConfigShape ;
        dct:relation demo:PollerAuthoringShape
    ] ;
        tcs:configTranslation \"\"\"
            CONSTRUCT { ?target ?p ?o . ?target rdfc:writer ?channel . }
            WHERE {
                ?source ?p ?o .
                OPTIONAL { ?step tcs:writesTo ?channel }
            } \"\"\" .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")

    _, compiler = _translated(build)["demo:In"]
    injected = build.select(
        "?channel",
        f"{compiler} tcs:embedded ?embedded . ?embedded rdfc:writer ?channel .",
    )
    assert not injected.empty, "the query's constructed triple never landed"

    # The authored config keeps its own shape — the query writes to the
    # compiler-facing node only.
    authored, _ = _translated(build)["demo:In"]
    leaked = build.select(
        "?channel",
        f"{authored} tcs:embedded ?embedded . ?embedded rdfc:writer ?channel .",
    )
    assert leaked.empty, "translation leaked into the authored config"
