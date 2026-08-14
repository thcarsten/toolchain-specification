"""Tests for RdfcConfigCompiler.describe_channel_wiring() /
_lookup_channel_predicate() — the unambiguous reader/writer auto-injection
added alongside PipelineEnricher."""

from testing_helpers import compile_pipeline, parse_extra, pipeline_ttl_content

PREFIXES = """
@prefix demo: <http://example.org/example/demonstrator/> .
@prefix p-plan: <http://purl.org/net/p-plan#> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix rdfc: <https://w3id.org/rdf-connect#> .
@prefix tcs: <https://w3id.org/toolchain#> .
"""


def test_reader_injected_when_unambiguous(catalog_graph):
    # rdfc:LogProcessorJs's generated configShape declares exactly one
    # reader slot (rdfc:reader, tcs:upstreamClass rdfc:Reader) and no
    # writer slot.
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [ ] ] ;
            tcs:readsFrom demo:ch1 .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    content = pipeline_ttl_content(build)
    assert "rdfc:reader" in content and "ch1" in content


def test_writer_injected_when_unambiguous(catalog_graph):
    # rdfc:HttpServer's generated configShape declares exactly one
    # writer slot (rdfc:writer, tcs:upstreamClass rdfc:Writer) and no
    # reader slot.
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:HttpServer ;
            p-plan:isStepOfPlan demo:Test ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [ rdfc:port 9000 ] ] ;
            tcs:writesTo demo:ch2 .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    content = pipeline_ttl_content(build)
    assert "rdfc:writer" in content and "ch2" in content


def test_ambiguous_writer_paths_left_unwired(catalog_graph):
    # rdfc:Sdsify's generated configShape declares two writer slots
    # (rdfc:output / rdfc:metadataOutput) — genuinely
    # ambiguous, so neither gets auto-injected even though there are
    # exactly two tcs:writesTo channels to match them.
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:Sdsify ;
            p-plan:isStepOfPlan demo:Test ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [ ] ] ;
            tcs:readsFrom demo:ch1 ; tcs:writesTo demo:ch2 , demo:ch3 .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    content = pipeline_ttl_content(build)
    # Reader side is unambiguous (single rdfc:input path) and gets wired.
    assert "rdfc:input" in content and "ch1" in content
    # Writer side stays unwired — neither predicate name appears.
    assert "rdfc:output" not in content
    assert "rdfc:metadataOutput" not in content


def test_already_explicit_key_is_never_overwritten(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [ rdfc:reader demo:explicit_channel ] ] ;
            tcs:readsFrom demo:ch1 .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    content = pipeline_ttl_content(build)
    assert "explicit_channel" in content
    assert "ch1" not in content


def test_pipeline_ttl_excludes_validation_report_bookkeeping(demonstrator_graph):
    # ValidationReportCompiler runs before RdfcConfigCompiler and attaches
    # dcat:qualifiedRelation (inputshaperel/outputshaperel, emptyshape)
    # onto channels; extract_config's traversal must not leak that
    # validation bookkeeping into the emitted pipeline.ttl.
    _, build = compile_pipeline(demonstrator_graph, "demo:DishacledPipeline")
    content = pipeline_ttl_content(build)
    assert "qualifiedRelation" not in content
    assert "inputshaperel" not in content
    assert "outputshaperel" not in content
    assert "emptyshape" not in content
