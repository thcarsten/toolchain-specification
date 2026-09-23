"""Tests for the tcs:Connection authoring vocabulary.

Module-level PREFIXES, the catalog_graph fixture + parse_extra, then
compile_pipeline. Malformed-Connection cases used to be caught by
SemanticModelMapper raising; per the channel-to-connection plan [D6]
that compiler is deleted and the same checks now live in
tcs:ConnectionCardinalityShape, exercised via assert_shacl_violation.
"""

from __future__ import annotations

import re

from rdflib import Graph

from testing_helpers import (
    assert_shacl_violation,
    compile_pipeline,
    load_reader,
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
# Pillar 2 — compiles
# ---------------------------------------------------------------------
#
# The "resolution table" this section used to cover (named/blank
# Connection minting a channel, reusing a pre-existing tcs:writesTo /
# tcs:readsFrom) was SemanticModelMapper's job: turning an authored
# tcs:Connection into tcs:readsFrom/tcs:writesTo/tcs:Channel wiring for
# downstream compilers to consume. Per the channel-to-connection plan
# [D6], that compiler is deleted — a Connection's tcs:from/tcs:to *is*
# the wiring now, consumed directly by every downstream compiler, so
# there is no separate resolution step left to exercise. Those cases
# retired with the compiler they tested; see
# test_no_connection_triple_survives_into_build below for the coverage
# that replaces them (authored tcs:from/tcs:to must survive narrowing
# unchanged, since nothing derives anything from them anymore).


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
#
# "Existing wiring conflicts with an authored Connection" and "two
# tcs:from on one Connection" were SemanticModelMapper's ValueErrors —
# reconciling a Connection against pre-existing tcs:readsFrom/
# tcs:writesTo, and rejecting a malformed Connection outright. Per [D6]
# both retire with the compiler: there is no pre-existing wiring left
# to reconcile against (a Connection's tcs:from/tcs:to is the only
# wiring there is), and a malformed Connection now *reports* rather
# than *raises* — tcs:ConnectionCardinalityShape's job, exercised in
# test_connection_cardinality_shape_fires_on_two_tcs_from below. Fan-in
# and fan-out ("wires both branches") tested the same mapper minting a
# channel per branch; under a 1:1 Connection, branching is just two
# Connections sharing an endpoint — nothing left to mint, and its
# legality is covered by
# test_connection_cardinality_shape_is_silent_on_a_branching_source_graph.
#
# The other guard, endpoint typing (tcs:to must not point at a
# tcs:Channel), is also absorbed into that same SHACL shape ([D2]) —
# rewritten below to assert the violation declaratively instead of a
# raise.


def test_connection_cardinality_shape_fires_when_to_is_not_a_component(
    catalog_with_shapes,
):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test .
        demo:ch1 a tcs:Channel .
        demo:conn1 a tcs:Connection ; tcs:from demo:A ; tcs:to demo:ch1 .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes,
        message_contains="must have exactly one tcs:to, pointing at a tcs:InstancePipelineComponent",
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
    # edge must never leak into demo:Test's build at all.
    assert not build.ask("demo:otherconn ?p ?o .")
    # GraphReducer must still narrow the build (it must not be blocked
    # forever by demo:Other's Connection, which is out of demo:Test's
    # scope) — the other plan's steps must not survive narrowing into
    # demo:Test's build.
    assert not build.ask("?s a tcs:InstancePipelineComponent ; p-plan:isStepOfPlan demo:Other .")


def test_connection_triple_survives_into_build(catalog_graph):
    """Drift guard for the risk the plan itself calls out (§10): forward
    narrowing cannot reach a tcs:Connection (nothing points *at* it), so
    GraphReducer walks tcs:from/tcs:to against their normal direction
    specifically to keep it. Per [D6]/[D7] a Connection is no longer
    detached after being consumed by a mapper — there is no mapper —
    so it must show up in the build exactly as authored.
    """
    parse_extra(
        catalog_graph,
        PREFIXES + TWO_STEP + """
        demo:conn1 a tcs:Connection ; tcs:from demo:A ; tcs:to demo:B .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    assert build.ask("demo:conn1 a tcs:Connection ; tcs:from demo:A ; tcs:to demo:B .")


def test_connection_free_pipeline_never_runs_the_mapper(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ; tcs:readsFrom demo:ch1 .
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
    p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [
        ldio:force-content-type true ;
        ldio:context "{}"
    ] ] .

demo_lr:Sink a tcs:InstancePipelineComponent ;
    prov:specializationOf rdfc:LogProcessorJs ;
    p-plan:isStepOfPlan demo_lr:Test ;
    p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [] ] .

[ a tcs:Connection ; tcs:from demo_lr:Poll ; tcs:to demo_lr:Parse ] .
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
        ?entry  prov:specializationOf rdfc:HttpServer .
        ?exit   prov:specializationOf ldio:HttpOut .
        ?channel a tcs:Connection ;
                 tcs:from ?exit ; tcs:to ?entry ;
                 tcs:endpoint ?endpoint ; tcs:port ?port .
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


def test_blank_connection_channel_is_run_stable(catalog_graph):
    """Drift guard: a blank Connection's minted channel is
    `PipelineSeeder.name_blind_nodes`'s `:connection_N`, not a
    separately-numbered `:channel_N` — regenerating the same pipeline in
    two fresh processes must not disagree on the name.
    """
    first_graph = _copy_graph(catalog_graph)
    parse_extra(first_graph, PREFIXES + TWO_STEP + """
        [ a tcs:Connection ; tcs:from demo:A ; tcs:to demo:B ] .
    """)
    _, first_build = compile_pipeline(first_graph, "demo:Test")
    first_ttl = _normalize_channel_names(pipeline_ttl_content(first_build))

    second_graph = _copy_graph(catalog_graph)
    parse_extra(second_graph, PREFIXES + TWO_STEP + """
        [ a tcs:Connection ; tcs:from demo:A ; tcs:to demo:B ] .
    """)
    _, second_build = compile_pipeline(second_graph, "demo:Test")
    second_ttl = _normalize_channel_names(pipeline_ttl_content(second_build))

    assert first_ttl == second_ttl


# The drift guard that used to live here ("no compiler outside the
# mapper mentions tcs:Connection vocabulary") asserted the opposite of
# what C2 deliberately did: per §7 of the channel-to-connection plan,
# every framework consumer (RdfcConfigCompiler, the LDIO/NiFi/sw
# boundary compilers, BridgeTransportCompiler, DockerComposeCompiler,
# ...) now reads tcs:from/tcs:to directly, with no translation layer to
# confine the vocabulary to. Confining it to one file was only ever a
# property of the now-deleted SemanticModelMapper design; there is no
# analogous invariant to guard in the new one, so the test retires with
# the compiler it was guarding.


# ---------------------------------------------------------------------
# tcs:ConnectionCardinalityShape — per-Connection, not per-step
# ---------------------------------------------------------------------


def test_connection_cardinality_shape_is_silent_on_a_branching_source_graph(
    catalog_with_shapes,
):
    """Branching (two Connections sharing a tcs:from) is legal ([D2]) —
    the shape must not fire on it, only on a structurally malformed
    Connection (see the next test).
    """
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
    report = load_reader(catalog_with_shapes).validate(advanced=True)
    violations = report.select(
        "?focus ?message",
        "?r a sh:ValidationResult ; sh:focusNode ?focus ; sh:resultMessage ?message .",
    )
    # Narrowed to tcs:ConnectionCardinalityShape's own wording rather
    # than a generic "tcs:Connection" substring: other shapes (e.g.
    # RdfcMandatoryReaderWiringShape, since rdfc:LogProcessorJs
    # mandates exactly one reader) now legitimately mention
    # tcs:Connection in their own messages too, and demo:A here has no
    # incoming Connection — a real, unrelated violation the old filter
    # would have swept up as if it were this shape firing.
    matches = violations[
        violations["message"].str.contains(
            "must have exactly one tcs:(?:from|to)", case=False, na=False, regex=True
        )
    ]
    assert matches.empty, f"unexpected Connection-cardinality violation(s): {matches}"


def test_connection_cardinality_shape_fires_on_two_tcs_from(catalog_with_shapes):
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
        demo:conn1 a tcs:Connection ; tcs:from demo:A , demo:C ; tcs:to demo:B .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="exactly one tcs:from"
    )
