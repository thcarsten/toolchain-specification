"""`ConfigTranslator` derives a compiler-facing config for the steps that
need one — and only those.

A component that declares no `tcs:userFacingConfigShape` has one config
contract, not two, so its authored `p-plan:hasInputVar` config already is
the compiler-facing config and nothing is derived. `lookup_step_config`
is what makes that invisible to consumers. See
docs/config-shape-split-plan.md.
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

# A user-facing shape that omits the channel key, plus the query that
# puts it back. `?config tcs:embedded ?source` is how the query reaches
# the authored body: ?config is a named IRI and can be substituted,
# whereas the body root is a blank node and a blank-node label in a
# WHERE clause matches anything rather than referring to that node.
TRANSLATED = """
ldio:HttpInPoller dcat:qualifiedRelation [
    a dcat:Relationship ;
    dcat:hadRole tcs:userFacingConfigShape ;
    dct:relation demo:PollerAuthoringShape
] ;
    tcs:configTranslation \"\"\"
        CONSTRUCT { ?target ?p ?o . ?target rdfc:writer ?channel . }
        WHERE {
            ?config tcs:embedded ?source .
            ?source ?p ?o .
            OPTIONAL { ?step tcs:writesTo ?channel }
        } \"\"\" .
"""


def test_a_component_with_one_contract_aliases_its_authored_config(catalog_graph):
    """No `tcs:userFacingConfigShape` means the authored config already
    is the compiler-facing one, so `tcs:compilerConfig` points at that
    very node — one triple, no copy. The predicate still has to be there:
    a compiler-facing shape targets `tcs:compilerConfig/tcs:embedded`,
    and without the alias it would select nothing and validate nothing
    while still reporting `conforms: true`."""
    parse_extra(catalog_graph, PIPELINE)
    _, build = compile_pipeline(catalog_graph, "demo:Test")

    for step in ("demo:In", "demo:Out"):
        authored = build.filter(sub=step, pred="p-plan:hasInputVar").df["obj"].to_list()
        derived = build.filter(sub=step, pred="tcs:compilerConfig").df["obj"].to_list()
        assert derived == authored, f"{step}: {derived!r} is not the authored config"


def test_every_configured_step_has_a_compiler_config(catalog_graph):
    """Both shape roles target their own path unconditionally, so the
    predicate must exist for every step that has a config at all."""
    parse_extra(catalog_graph, PIPELINE)
    _, build = compile_pipeline(catalog_graph, "demo:Test")

    missing = build.select(
        "?step",
        """
        ?step a tcs:InstancePipelineComponent ; p-plan:hasInputVar ?config .
        FILTER NOT EXISTS { ?step tcs:compilerConfig ?compiler_config }
        """,
    )
    assert missing.empty, f"no compiler-facing config: {list(missing['step'])}"


def test_lookup_step_config_falls_back_to_the_authored_config(catalog_graph):
    parse_extra(catalog_graph, PIPELINE)
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    from compilers.utils import extract_config, lookup_step_config

    config_id = lookup_step_config(build, "demo:Out")
    assert config_id is not None
    authored = build.filter(sub="demo:Out", pred="p-plan:hasInputVar").df["obj"].iloc[0]
    # Resolves to the authored node either way here, since the alias
    # points at it — the helper's job is that a consumer running before
    # ConfigTranslator gets the same answer.
    assert config_id == authored
    # And it is readable as a config, not just present.
    assert extract_config(build, config_id) != {}


def test_a_user_facing_shape_without_a_translation_raises(catalog_graph):
    """Two declared contracts and nothing to bridge them is an authoring
    error, not a licence to compile the authored config unchanged: it may
    not satisfy the compiler-facing shape."""
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


def test_a_translation_query_supplies_what_the_author_omitted(catalog_graph):
    """The mechanism slice 2 relocates RDF-Connect's channel injection
    into: the author omits the channel key and the query supplies it."""
    parse_extra(catalog_graph, PIPELINE + TRANSLATED)
    _, build = compile_pipeline(catalog_graph, "demo:Test")

    injected = build.select(
        "?channel",
        """
        demo:In tcs:compilerConfig ?config .
        ?config tcs:embedded ?embedded .
        ?embedded rdfc:writer ?channel .
        """,
    )
    assert not injected.empty, "the query's constructed triple never landed"

    # The authored body came across too — a translation replaces the
    # config, it does not start from an empty one.
    copied = build.select(
        "?url",
        """
        demo:In tcs:compilerConfig ?config .
        ?config tcs:embedded ?embedded .
        ?embedded ldio:url ?url .
        """,
    )
    assert not copied.empty, "the authored body was dropped"


def test_a_translation_does_not_touch_the_authored_config(catalog_graph):
    """The point of the split: what the compiler reads must not be what
    the author wrote, or injecting into one mutates the other."""
    parse_extra(catalog_graph, PIPELINE + TRANSLATED)
    _, build = compile_pipeline(catalog_graph, "demo:Test")

    leaked = build.select(
        "?channel",
        """
        demo:In p-plan:hasInputVar ?config .
        ?config tcs:embedded ?embedded .
        ?embedded rdfc:writer ?channel .
        """,
    )
    assert leaked.empty, "translation leaked into the authored config"

    shared = build.select(
        "?node",
        """
        demo:In p-plan:hasInputVar ?authored ; tcs:compilerConfig ?compiler .
        ?authored tcs:embedded ?node .
        ?compiler tcs:embedded ?node .
        """,
    )
    assert shared.empty, "the two configs share a body node"


def test_a_translated_step_compiles_through_lookup_step_config(catalog_graph):
    """Consumers must pick the derived config over the authored one."""
    parse_extra(catalog_graph, PIPELINE + TRANSLATED)
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    from compilers.utils import lookup_step_config

    derived = build.filter(sub="demo:In", pred="tcs:compilerConfig").df["obj"].iloc[0]
    assert lookup_step_config(build, "demo:In") == derived
