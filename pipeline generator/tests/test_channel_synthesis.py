"""Tests for PipelineEnricher.synthesize_channels() — one test per case in its docstring."""

from testing_helpers import compile_pipeline, load_reader, parse_extra

PREFIXES = """
@prefix demo: <http://example.org/example/demonstrator/> .
@prefix p-plan: <http://purl.org/net/p-plan#> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix rdfc: <https://w3id.org/rdf-connect#> .
@prefix tcs: <https://w3id.org/toolchain#> .
"""


def test_isprecededby_synthesizes_channel_when_neither_side_wired(catalog_graph):
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
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    channel = build.filter(sub="demo:B", pred="tcs:readsFrom").df["obj"].iloc[0]
    assert build.ask(f"demo:A tcs:writesTo {channel} .")


def test_isprecededby_reuses_existing_predecessor_writesto(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ; tcs:writesTo demo:ch1 .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ; p-plan:isPrecededBy demo:A .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    assert build.ask("demo:B tcs:readsFrom demo:ch1 .")


def test_isprecededby_reuses_existing_successor_readsfrom(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ; p-plan:isPrecededBy demo:A ; tcs:readsFrom demo:ch1 .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    assert build.ask("demo:A tcs:writesTo demo:ch1 .")


def test_isprecededby_skipped_when_predecessor_branches(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ; tcs:writesTo demo:ch1 , demo:ch2 .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ; p-plan:isPrecededBy demo:A .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    assert build.filter(sub="demo:B", pred="tcs:readsFrom").df.empty


def test_isprecededby_skipped_when_both_sides_already_wired(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ; tcs:writesTo demo:ch1 .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ; p-plan:isPrecededBy demo:A ; tcs:readsFrom demo:ch2 .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    # Both sides already explicit (to possibly-different channels) — left untouched.
    assert build.filter(sub="demo:B", pred="tcs:readsFrom").df["obj"].to_list() == [
        "demo:ch2"
    ]
    assert build.filter(sub="demo:A", pred="tcs:writesTo").df["obj"].to_list() == [
        "demo:ch1"
    ]


def test_channel_synthesis_collision_safe_naming(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        @prefix : <http://example.org/example/> .
        demo:Test a tcs:PipelineDefinition .
        demo:C a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ; tcs:readsFrom :channel_0 .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ; p-plan:isPrecededBy demo:A .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    channel = build.filter(sub="demo:B", pred="tcs:readsFrom").df["obj"].iloc[0]
    assert channel != ":channel_0"


def test_synthesize_channels_wires_a_long_chain_losslessly(catalog_graph):
    # A five-step chain wired purely via isPrecededBy, with no explicit
    # channel anywhere — every hop must come out wired, and no two hops
    # may accidentally collapse onto the same channel.
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ; p-plan:isPrecededBy demo:A .
        demo:C a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ; p-plan:isPrecededBy demo:B .
        demo:D a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ; p-plan:isPrecededBy demo:C .
        demo:E a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ; p-plan:isPrecededBy demo:D .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")

    steps = ["demo:A", "demo:B", "demo:C", "demo:D", "demo:E"]
    channels = []
    for successor, predecessor in zip(steps[1:], steps[:-1]):
        channel = build.filter(sub=successor, pred="tcs:readsFrom").df["obj"].iloc[0]
        assert build.ask(f"{predecessor} tcs:writesTo {channel} .")
        channels.append(channel)

    # Four independent hops must never share a channel — that would
    # silently merge two unrelated dataflow edges into one.
    assert len(set(channels)) == 4


def test_synthesize_channels_loses_no_edge_in_the_real_demonstrator_pipeline(
    demonstrator_graph,
):
    """Every p-plan:isPrecededBy edge declared in pipeline_definition.ttl
    must end up with matching tcs:readsFrom/tcs:writesTo wiring after
    PipelineEnricher runs. ValidationReportCompiler.gather_throughput_shapes
    discovers channels purely from those two predicates (see its
    docstring), so a dropped edge here would silently vanish from
    validation instead of raising anywhere — this is the precondition
    that guarantees it doesn't.
    """
    edges = load_reader(demonstrator_graph).select(
        "?successor ?predecessor",
        "?successor p-plan:isPrecededBy ?predecessor .",
    )
    assert not edges.empty, "sanity check: the real pipeline uses isPrecededBy"

    _, build = compile_pipeline(demonstrator_graph, "demo:DishacledPipeline")

    for _, row in edges.iterrows():
        successor = row["successor"]
        predecessor = row["predecessor"]
        successor_channels = set(
            build.filter(sub=successor, pred="tcs:readsFrom").df["obj"].tolist()
        )
        predecessor_channels = set(
            build.filter(sub=predecessor, pred="tcs:writesTo").df["obj"].tolist()
        )
        assert successor_channels, f"{successor} lost its readsFrom wiring"
        assert predecessor_channels, f"{predecessor} lost its writesTo wiring"
        assert (
            successor_channels & predecessor_channels
        ), f"{successor} and {predecessor} share no channel after enrichment"
