"""LdioConfigCompiler emit: plan slug, IRI values, booleans, urls list."""

from compilers import LdioConfigCompiler

from testing_helpers import compile_pipeline, parse_extra

PREFIXES = """
@prefix demo: <http://example.org/example/demonstrator/> .
@prefix p-plan: <http://purl.org/net/p-plan#> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix ldio: <http://example.org/example/ldio/> .
@prefix tcs: <https://w3id.org/toolchain#> .
@prefix dct: <http://purl.org/dc/terms/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
"""


def test_ldio_yaml_slug_iris_booleans_and_urls_list(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES
        + """
        demo:Test a tcs:PipelineDefinition ;
            dct:identifier "plan-a" ;
            rdfs:comment "ingest stream" .

        demo:In a tcs:InstancePipelineComponent ; prov:specializationOf ldio:LdesClient ;
            p-plan:isStepOfPlan demo:Test ; tcs:writesTo demo:ch1 ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [
                ldio:urls "http://example.org/stream" ;
                ldio:enable-exactly-once false
            ] ] .
        demo:Voc a tcs:InstancePipelineComponent ; prov:specializationOf ldio:VersionObjectCreator ;
            p-plan:isStepOfPlan demo:Test ; tcs:readsFrom demo:ch1 ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [
                ldio:date-observed-property "http://purl.org/dc/terms/modified" ;
                ldio:member-type "http://example.org/ExampleType"
            ] ] .
        """,
    )
    gen, _ = compile_pipeline(catalog_graph, "demo:Test")
    segments = gen.compilers[LdioConfigCompiler].segment_outputs
    assert len(segments) == 1
    body = next(iter(segments.values()))
    assert body["name"] == "plan-a"
    assert body["description"] == "ingest stream"
    assert body["input"]["config"]["urls"] == ["http://example.org/stream"]
    assert body["input"]["config"]["enable-exactly-once"] is False
    voc = body["transformers"][0]["config"]
    # dct: is bound in the shared prefix store; drop() on values used to
    # emit "modified" here. A random IRI with no prefix would not catch that.
    assert voc["date-observed-property"] == "http://purl.org/dc/terms/modified"
    assert voc["member-type"] == "http://example.org/ExampleType"
