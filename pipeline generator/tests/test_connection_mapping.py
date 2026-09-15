"""Tests for SemanticModelMapper — the tcs:Connection authoring layer.

Structured like test_channel_synthesis.py: module-level PREFIXES, the
catalog_graph fixture + parse_extra, then compile_pipeline /
assert_compile_raises. See the plan's two-pillar convention
(tests/EDGE_CASES.md): unsupported edge cases are caught either by a
SHACL shape or a compiler-level guard; supported edge cases compile for
real and get their resulting graph inspected.
"""

from __future__ import annotations

import re
from pathlib import Path

from rdflib import Graph

from testing_helpers import (
    assert_compile_raises,
    assert_shacl_violation,
    compile_pipeline,
    parse_extra,
    pipeline_ttl_content,
)

PREFIXES = """
@prefix demo: <http://example.org/example/demonstrator/> .
@prefix demo_lr: <http://example.org/example/ldio-rdfc/> .
@prefix ldio: <http://example.org/example/ldio/> .
@prefix p-plan: <http://purl.org/net/p-plan#> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix rdfc: <https://w3id.org/rdf-connect#> .
@prefix tcs: <https://w3id.org/toolchain#> .
"""

TWO_STEP = """
demo:Test a tcs:PipelineDefinition .
demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
    p-plan:isStepOfPlan demo:Test .
demo:B a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
    p-plan:isStepOfPlan demo:Test .
"""


# ---------------------------------------------------------------------
# Pillar 2 — compiles (the resolution table, rows 1-4)
# ---------------------------------------------------------------------


def test_named_connection_wires_both_ends(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + TWO_STEP + """
        demo:conn1 a tcs:Connection ; tcs:from demo:A ; tcs:to demo:B .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    channel = build.filter(sub="demo:B", pred="tcs:readsFrom").df["obj"].iloc[0]
    assert build.ask(f"demo:A tcs:writesTo {channel} .")


def test_blank_node_connection_wires_both_ends(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + TWO_STEP + """
        [ a tcs:Connection ; tcs:from demo:A ; tcs:to demo:B ] .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    channel = build.filter(sub="demo:B", pred="tcs:readsFrom").df["obj"].iloc[0]
    assert build.ask(f"demo:A tcs:writesTo {channel} .")


def test_minted_channel_is_typed(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + TWO_STEP + """
        demo:conn1 a tcs:Connection ; tcs:from demo:A ; tcs:to demo:B .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    channel = build.filter(sub="demo:B", pred="tcs:readsFrom").df["obj"].iloc[0]
    assert build.ask(f"{channel} a tcs:Channel .")


def test_reuses_producers_existing_writesto(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ; tcs:writesTo demo:ch1 .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test .
        demo:conn1 a tcs:Connection ; tcs:from demo:A ; tcs:to demo:B .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    assert build.ask("demo:B tcs:readsFrom demo:ch1 .")


def test_reuses_consumers_existing_readsfrom(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ; tcs:readsFrom demo:ch1 .
        demo:conn1 a tcs:Connection ; tcs:from demo:A ; tcs:to demo:B .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    assert build.ask("demo:A tcs:writesTo demo:ch1 .")


def test_already_wired_to_same_channel_is_a_noop(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ; tcs:writesTo demo:ch1 .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ; tcs:readsFrom demo:ch1 .
        demo:conn1 a tcs:Connection ; tcs:from demo:A ; tcs:to demo:B .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    assert build.filter(sub="demo:A", pred="tcs:writesTo").df["obj"].to_list() == [
        "demo:ch1"
    ]
    assert build.filter(sub="demo:B", pred="tcs:readsFrom").df["obj"].to_list() == [
        "demo:ch1"
    ]


# ---------------------------------------------------------------------
# Pillar 1b — compiler-level guards
# ---------------------------------------------------------------------


def test_conflicting_existing_wiring_raises(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ; tcs:writesTo demo:ch1 .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ; tcs:readsFrom demo:ch2 .
        demo:conn1 a tcs:Connection ; tcs:from demo:A ; tcs:to demo:B .
    """,
    )
    assert_compile_raises(
        catalog_graph, "demo:Test", match="contradicts existing wiring"
    )


def test_fan_out_from_one_step_raises(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test .
        demo:C a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test .
        demo:conn1 a tcs:Connection ; tcs:from demo:A ; tcs:to demo:B .
        demo:conn2 a tcs:Connection ; tcs:from demo:A ; tcs:to demo:C .
    """,
    )
    assert_compile_raises(catalog_graph, "demo:Test", match="fan-out")


def test_fan_in_to_one_step_raises(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test .
        demo:C a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test .
        demo:conn1 a tcs:Connection ; tcs:from demo:A ; tcs:to demo:C .
        demo:conn2 a tcs:Connection ; tcs:from demo:B ; tcs:to demo:C .
    """,
    )
    assert_compile_raises(catalog_graph, "demo:Test", match="fan-in")


def test_to_pointing_at_a_channel_raises_with_hint(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test .
        demo:ch1 a tcs:Channel .
        demo:conn1 a tcs:Connection ; tcs:from demo:A ; tcs:to demo:ch1 .
    """,
    )
    assert_compile_raises(
        catalog_graph, "demo:Test", match="point tcs:to at a channel"
    )


def test_two_tcs_from_on_one_connection_raises(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + TWO_STEP + """
        demo:C a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test .
        demo:conn1 a tcs:Connection ; tcs:from demo:A , demo:C ; tcs:to demo:B .
    """,
    )
    assert_compile_raises(
        catalog_graph, "demo:Test", match="exactly one tcs:from"
    )


# ---------------------------------------------------------------------
# Pillar 2 — plan scoping, coexistence, no leakage
# ---------------------------------------------------------------------


def test_connection_between_steps_of_another_plan_is_ignored(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + TWO_STEP + """
        demo:Other a tcs:PipelineDefinition .
        demo:OtherA a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Other .
        demo:OtherB a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Other .
        demo:otherconn a tcs:Connection ; tcs:from demo:OtherA ; tcs:to demo:OtherB .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    # demo:Test itself declares no tcs:Connection — the other plan's
    # edge must never leak wiring onto demo:A / demo:B.
    assert not build.ask("demo:A tcs:writesTo ?c .")
    assert not build.ask("demo:B tcs:readsFrom ?c .")
    # GraphReducer must still narrow the build (it must not be blocked
    # forever by demo:Other's Connection, which SemanticModelMapper
    # never consumes since it's out of demo:Test's scope) — the other
    # plan's steps must not survive narrowing into demo:Test's build.
    assert not build.ask("?s a tcs:InstancePipelineComponent ; p-plan:isStepOfPlan demo:Other .")


def test_connection_and_isprecededby_on_same_pair_is_idempotent(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ; p-plan:isPrecededBy demo:A .
        demo:conn1 a tcs:Connection ; tcs:from demo:A ; tcs:to demo:B .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    writes = build.filter(sub="demo:A", pred="tcs:writesTo").df["obj"].to_list()
    reads = build.filter(sub="demo:B", pred="tcs:readsFrom").df["obj"].to_list()
    assert len(writes) == 1
    assert len(reads) == 1
    assert writes == reads


def test_no_connection_triple_survives_into_build(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + TWO_STEP + """
        demo:conn1 a tcs:Connection ; tcs:from demo:A ; tcs:to demo:B .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    assert build.filter(pred="rdf:type", obj="tcs:Connection").df.empty
    assert build.filter(pred="tcs:from").df.empty
    assert build.filter(pred="tcs:to").df.empty


def test_connection_free_pipeline_never_runs_the_mapper(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ; p-plan:isPrecededBy demo:A .
    """,
    )
    gen, build = compile_pipeline(catalog_graph, "demo:Test")
    ran = {cls.__name__ for cls in gen.compilers}
    assert "SemanticModelMapper" not in ran
    assert not build.ask("?s dct:creator tcs:SemanticModelMapper .")


# ---------------------------------------------------------------------
# Cross-container bridging (§1.6 regression)
# ---------------------------------------------------------------------

LDIO_TO_RDFC = PREFIXES + """
demo_lr:Test a tcs:PipelineDefinition .

demo_lr:Poll a tcs:InstancePipelineComponent ;
    prov:specializationOf ldio:HttpInPoller ;
    p-plan:isStepOfPlan demo_lr:Test ;
    p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [
        ldio:url "https://example.invalid/api" ;
        ldio:cron "*/10 * * * * *"
    ] ] .

demo_lr:Parse a tcs:InstancePipelineComponent ;
    prov:specializationOf ldio:JsonToLdAdapter ;
    p-plan:isStepOfPlan demo_lr:Test ;
    p-plan:isPrecededBy demo_lr:Poll ;
    p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [
        ldio:force-content-type true ;
        ldio:context "{}"
    ] ] .

demo_lr:Sink a tcs:InstancePipelineComponent ;
    prov:specializationOf rdfc:LogProcessorJs ;
    p-plan:isStepOfPlan demo_lr:Test ;
    p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [] ] .

[ a tcs:Connection ; tcs:from demo_lr:Parse ; tcs:to demo_lr:Sink ] .
"""


def test_cross_container_connection_is_bridged(catalog_graph):
    parse_extra(catalog_graph, LDIO_TO_RDFC)
    gen, build = compile_pipeline(catalog_graph, "demo_lr:Test")

    ran = {cls.__name__ for cls in gen.compilers}
    assert "BridgeTransportCompiler" in ran
    assert "RdfcHttpServerConfigCompiler" in ran
    assert "LdioHttpOutConfigCompiler" in ran

    endpoints = build.select(
        "?endpoint ?port",
        """
        ?entry  prov:specializationOf rdfc:HttpServer ;
                tcs:readsFrom ?channel .
        ?exit   prov:specializationOf ldio:HttpOut ;
                tcs:writesTo ?channel .
        ?channel tcs:endpoint ?endpoint ;
                 tcs:port ?port .
        """,
    )
    assert len(endpoints) == 1


# ---------------------------------------------------------------------
# Equivalence + drift guard
# ---------------------------------------------------------------------

_CHANNEL_PATTERN = re.compile(r":channel_\d+")


def _normalize_channel_names(text: str) -> str:
    return _CHANNEL_PATTERN.sub(":channel_N", text)


def _copy_graph(g: Graph) -> Graph:
    """A cheap in-memory duplicate of ``g``, namespace bindings included
    — same idiom as conftest.py's private ``_copy_graph`` (not imported
    directly: testing_helpers.py's docstring explains why nothing here
    imports by name from conftest.py)."""
    copy = Graph() + g
    for prefix, namespace in g.namespaces():
        copy.bind(prefix, namespace)
    return copy


def test_connection_syntax_compiles_to_same_rdfc_pipeline_as_isprecededby(
    catalog_graph,
):
    isprecededby_graph = _copy_graph(catalog_graph)
    parse_extra(
        isprecededby_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [] ] .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ; p-plan:isPrecededBy demo:A ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [] ] .
    """,
    )
    _, isprecededby_build = compile_pipeline(isprecededby_graph, "demo:Test")
    isprecededby_ttl = _normalize_channel_names(
        pipeline_ttl_content(isprecededby_build)
    )

    connection_graph = _copy_graph(catalog_graph)
    parse_extra(
        connection_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [] ] .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [] ] .
        [ a tcs:Connection ; tcs:from demo:A ; tcs:to demo:B ] .
    """,
    )
    _, connection_build = compile_pipeline(connection_graph, "demo:Test")
    connection_ttl = _normalize_channel_names(pipeline_ttl_content(connection_build))

    assert isprecededby_ttl == connection_ttl


def test_no_compiler_outside_mapper_mentions_connection_vocabulary():
    """Drift guard: the tcs:Connection / tcs:from / tcs:to authoring
    vocabulary must stay confined to SemanticModelMapper. The one
    documented exception is GraphReducer's belt-and-braces
    ``FILTER NOT EXISTS { ?c a tcs:Connection }`` guard (§1.2, layer 3)
    — a deliberate ordering safety net, not a leak of the authoring
    layer's semantics.
    """
    compilers_dir = Path(__file__).resolve().parents[1] / "src" / "compilers"
    allowed = {"semantic_model_mapper.py", "graph_reducer.py"}
    offenders = []
    for path in compilers_dir.rglob("*.py"):
        if path.name in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"tcs:Connection|tcs:from\b|tcs:to\b", text):
            offenders.append(str(path.relative_to(compilers_dir)))
    assert not offenders, f"tcs:Connection vocabulary leaked into: {offenders}"


# ---------------------------------------------------------------------
# The temporary no-branching shape
# ---------------------------------------------------------------------


def test_connection_cardinality_shape_fires_on_branching_source_graph(
    catalog_with_shapes,
):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test .
        demo:C a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test .
        demo:conn1 a tcs:Connection ; tcs:from demo:A ; tcs:to demo:B .
        demo:conn2 a tcs:Connection ; tcs:from demo:A ; tcs:to demo:C .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="Branching is not yet supported"
    )
