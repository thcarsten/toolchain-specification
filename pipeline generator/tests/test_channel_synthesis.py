"""Tests for PipelineEnricher.synthesize_channels() — one test per case in its docstring."""

from testing_helpers import compile_pipeline, parse_extra

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
