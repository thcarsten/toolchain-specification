"""One test per edge case in EDGE_CASES.md. Keep the mapping 1:1 so the
checklist and the suite never drift apart."""

import pytest

from testing_helpers import (
    DATA_DIR,
    SHAPES_FILE,
    assert_compile_raises,
    assert_shacl_violation,
    compile_pipeline,
    load_reader,
    parse_extra,
    pipeline_ttl_content,
)

PREFIXES = """
@prefix demo: <http://example.org/example/demonstrator/> .
@prefix p-plan: <http://purl.org/net/p-plan#> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix ldio: <http://example.org/example/ldio/> .
@prefix rdfc: <https://w3id.org/rdf-connect#> .
@prefix sw: <https://semantic.works/services/> .
@prefix tcs: <https://w3id.org/toolchain#> .
@prefix dct: <http://purl.org/dc/terms/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
"""


# --- Pillar 2: supported --------------------------------------------------


def test_ldio_transformer_reuse_keeps_distinct_configs(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:In a tcs:InstancePipelineComponent ; prov:specializationOf ldio:HttpInPoller ;
            p-plan:isStepOfPlan demo:Test ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:literal "url: http://a" ; dct:format "text/yaml" ] ;
            tcs:writesTo demo:ch1 .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf ldio:SparqlConstructTransformer ;
            p-plan:isStepOfPlan demo:Test ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:literal "QUERY_A" ; dct:format "text/plain" ] ;
            tcs:readsFrom demo:ch1 ; tcs:writesTo demo:ch2 .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf ldio:SparqlConstructTransformer ;
            p-plan:isStepOfPlan demo:Test ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:literal "QUERY_B" ; dct:format "text/plain" ] ;
            tcs:readsFrom demo:ch2 ; tcs:writesTo demo:ch3 .
    """,
    )
    from compilers import LdioConfigCompiler

    gen, _ = compile_pipeline(catalog_graph, "demo:Test")
    transformers = gen.compilers[LdioConfigCompiler].output["transformers"]
    assert [t["config"] for t in transformers] == ["QUERY_A", "QUERY_B"]


def test_sw_component_reuse_folds_both_env_vars(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf sw:mu-dispatcher ;
            p-plan:isStepOfPlan demo:Test ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:literal "FOO_A: a" ; dct:format "text/yaml" ] .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf sw:mu-dispatcher ;
            p-plan:isStepOfPlan demo:Test ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:literal "FOO_B: b" ; dct:format "text/yaml" ] .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    compose = (
        build.filter(pred="tcs:literal", sub=":MuDispatcherDockerCompose")
        .df["obj"]
        .iloc[0]
    )
    assert "FOO_A" in compose and "FOO_B" in compose


# --- Pillar 1a: SHACL-shape-enforced --------------------------------------


def test_ldio_duplicate_input_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf ldio:HttpInPoller ;
            p-plan:isStepOfPlan demo:Test .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf ldio:HttpInPoller ;
            p-plan:isStepOfPlan demo:Test .
    """,
    )
    assert_shacl_violation(catalog_with_shapes, message_contains='second "Input" step')


def test_channel_self_loop_triggers_acyclic_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ; tcs:readsFrom demo:ch1 ; tcs:writesTo demo:ch1 .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="cycle in the channel graph"
    )


def test_two_configs_on_one_step_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:literal "x" ; dct:format "text/plain" ] ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:literal "y" ; dct:format "text/plain" ] .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="at most one p-plan:hasInputVar"
    )


# --- Pillar 1b: compiler-guard-enforced (no SHACL shape for these yet) ----


def test_docker_compose_service_name_collision_raises(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:CompA a tcs:PipelineComponent ; tcs:config demo:ConfigA .
        demo:ConfigA a tcs:Config, tcs:DockerComposeConfig ;
            tcs:literal "database:\\n  image: postgres" ; dct:format "text/yaml" .
        demo:CompB a tcs:PipelineComponent ; tcs:config demo:ConfigB .
        demo:ConfigB a tcs:Config, tcs:DockerComposeConfig ;
            tcs:literal "database:\\n  image: mysql" ; dct:format "text/yaml" .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf demo:CompA ; p-plan:isStepOfPlan demo:Test .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf demo:CompB ; p-plan:isStepOfPlan demo:Test .
    """,
    )
    assert_compile_raises(
        catalog_graph,
        "demo:Test",
        match="both declare a 'services' entry named 'database'",
    )


def test_package_version_conflict_raises(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        @prefix spdx: <http://spdx.org/rdf/terms#> .
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix : <http://example.org/example/> .

        demo:Test a tcs:PipelineDefinition .

        demo:CompA a tcs:PipelineComponent ;
            dct:requires rdfc:NodeRunner ;
            dct:requires [ a spdx:Package ; spdx:name "shared-lib" ; spdx:versionInfo "^1.0.0" ; spdx:suppliedBy :npm ] ;
            owl:imports <./node_modules/fake-a/processors.ttl> .
        demo:CompB a tcs:PipelineComponent ;
            dct:requires rdfc:NodeRunner ;
            dct:requires [ a spdx:Package ; spdx:name "shared-lib" ; spdx:versionInfo "^2.0.0" ; spdx:suppliedBy :npm ] ;
            owl:imports <./node_modules/fake-b/processors.ttl> .

        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf demo:CompA ;
            p-plan:isStepOfPlan demo:Test ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [ rdfc:writer demo:ch1 ] ] ;
            tcs:writesTo demo:ch1 .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf demo:CompB ;
            p-plan:isStepOfPlan demo:Test ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [ rdfc:reader demo:ch1 ] ] ;
            tcs:readsFrom demo:ch1 .
    """,
    )
    assert_compile_raises(
        catalog_graph, "demo:Test", match="Conflicting spdx:versionInfo"
    )


def test_attach_file_duplicate_path_raises():
    from rdflib import Graph
    from rdfine import GraphReader

    from compilers.utils import attach_file

    g = Graph()
    g.parse(
        data="""
        @prefix tcs: <https://w3id.org/toolchain#> .
        @prefix demo: <http://example.org/example/demonstrator/> .
        @prefix : <http://example.org/example/> .
        demo:Build a tcs:PipelineBuild .
    """,
        format="turtle",
    )
    reader = attach_file(
        GraphReader(g), filename="foo.txt", filepath=".", content="first"
    )
    with pytest.raises(ValueError, match="already has a"):
        attach_file(reader, filename="foo.txt", filepath=".", content="second")


# --- More pillar 1a: SHACL-shape-enforced ---------------------------------


def test_ldio_duplicate_adapter_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf ldio:JsonToLdAdapter ;
            p-plan:isStepOfPlan demo:Test .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf ldio:JsonToLdAdapter ;
            p-plan:isStepOfPlan demo:Test .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains='second "Adapter" step'
    )


def test_multi_step_channel_cycle_triggers_acyclic_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ; tcs:readsFrom demo:ch2 ; tcs:writesTo demo:ch1 .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ; tcs:readsFrom demo:ch1 ; tcs:writesTo demo:ch2 .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="cycle in the channel graph"
    )


def test_same_step_uri_in_two_plans_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:PlanA a tcs:PipelineDefinition .
        demo:PlanB a tcs:PipelineDefinition .
        demo:SharedStep a tcs:InstancePipelineComponent ;
            prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:PlanA , demo:PlanB .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="exactly one tcs:PipelineDefinition"
    )


# --- More pillar 2: supported / graph-level robustness --------------------


def test_rdfc_component_reuse_keeps_distinct_configs(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [ rdfc:reader demo:ch1 ; rdfc:label "first" ] ] ;
            tcs:readsFrom demo:ch1 .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [ rdfc:reader demo:ch2 ; rdfc:label "second" ] ] ;
            tcs:readsFrom demo:ch2 .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    content = pipeline_ttl_content(build)
    assert "first" in content and "second" in content


def test_pipeline_id_substring_does_not_corrupt_output(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [ rdfc:reader demo:TestArchive ] ] ;
            tcs:readsFrom demo:TestArchive .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    content = pipeline_ttl_content(build)
    assert "<>Archive" not in content
    assert "TestArchive" in content


def test_typo_pipeline_id_raises_nameerror(catalog_graph):
    # No pipeline content is parsed here — just bind the `demo:` prefix
    # (used by the nonexistent id below) via a triple-less snippet.
    parse_extra(catalog_graph, PREFIXES)
    assert_compile_raises(
        catalog_graph,
        "demo:DoesNotExist",
        match="not found in graph",
        exc_type=NameError,
    )


def test_zero_step_pipeline_compiles_to_minimal_build(catalog_graph):
    parse_extra(catalog_graph, PREFIXES + "demo:Empty a tcs:PipelineDefinition .")
    gen, _ = compile_pipeline(catalog_graph, "demo:Empty")
    # PipelineEnricher triggers on tcs:PipelineBuild existing (seeded
    # unconditionally by PipelineExtractor), so it still runs here as a
    # no-op enrichment pass — there are no steps to synthesize channels
    # or configs for, but the compiler is still eligible and does run.
    # ValidationReportCompiler triggers on PipelineEnricher's dct:creator
    # provenance, so it follows for the same reason.
    assert [c.__name__ for c in gen.compilers] == [
        "PipelineExtractor",
        "PipelineEnricher",
        "ValidationReportCompiler",
    ]


def test_non_deployable_pipeline_compiles_with_zero_containers(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:FloatingComponent a tcs:PipelineComponent .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf demo:FloatingComponent ;
            p-plan:isStepOfPlan demo:Test .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    assert build.filter(pred="rdf:type", obj="tcs:DockerContainer").df.empty


def test_rdfc_segment_with_zero_channel_wiring_compiles(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test .
    """,
    )
    gen, _ = compile_pipeline(catalog_graph, "demo:Test")
    assert "RdfcConfigCompiler" in [c.__name__ for c in gen.compilers]


def test_unused_runner_type_does_not_crash(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        rdfc:PythonRunner a rdfc:Runner .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs ;
            p-plan:isStepOfPlan demo:Test ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [ rdfc:reader demo:ch1 ] ] ;
            tcs:readsFrom demo:ch1 .
    """,
    )
    gen, _ = compile_pipeline(catalog_graph, "demo:Test")
    assert "RdfcConfigCompiler" in [c.__name__ for c in gen.compilers]


def test_ldio_fanout_both_outputs_survive(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:In a tcs:InstancePipelineComponent ; prov:specializationOf ldio:HttpInPoller ;
            p-plan:isStepOfPlan demo:Test ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:literal "url: http://a" ; dct:format "text/yaml" ] ;
            tcs:writesTo demo:ch1 .
        demo:OutA a tcs:InstancePipelineComponent ; prov:specializationOf ldio:HttpOut ;
            p-plan:isStepOfPlan demo:Test ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:literal "target: http://a" ; dct:format "text/yaml" ] ;
            tcs:readsFrom demo:ch1 .
        demo:OutB a tcs:InstancePipelineComponent ; prov:specializationOf ldio:HttpOut ;
            p-plan:isStepOfPlan demo:Test ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:literal "target: http://b" ; dct:format "text/yaml" ] ;
            tcs:readsFrom demo:ch1 .
    """,
    )
    from compilers import LdioConfigCompiler

    gen, _ = compile_pipeline(catalog_graph, "demo:Test")
    outputs = gen.compilers[LdioConfigCompiler].output["outputs"]
    targets = {o["config"]["target"] for o in outputs}
    assert targets == {"http://a", "http://b"}


def test_cyclic_dct_requires_does_not_hang_or_crash(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:CompA a tcs:PipelineComponent ; dct:requires demo:CompB ; tcs:config demo:ConfigA .
        demo:ConfigA a tcs:Config, tcs:DockerComposeConfig ;
            tcs:literal "a:\\n  image: fake-a" ; dct:format "text/yaml" .
        demo:CompB a tcs:PipelineComponent ; dct:requires demo:CompA .
        demo:StepB a tcs:InstancePipelineComponent ; prov:specializationOf demo:CompB ;
            p-plan:isStepOfPlan demo:Test .
    """,
    )
    _, build = compile_pipeline(catalog_graph, "demo:Test")
    assert not build.filter(pred="tcs:runs", obj="demo:StepB").df.empty


def test_dangling_specialization_target_triggers_cataloged_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf demo:GhostComponent ;
            p-plan:isStepOfPlan demo:Test .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="not listed as a dcat:resource"
    )


# --- Structural application-profile shapes (Section 1 of the profile) ----


def test_pipeline_component_non_deployable_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:Leaf a tcs:PipelineComponent .
        demo:CompA a tcs:PipelineComponent ; dct:requires demo:Leaf .
    """,
    )
    assert_shacl_violation(catalog_with_shapes, message_contains="not deployable")


def test_pipeline_component_duplicate_default_config_per_subtype_triggers_shape(
    catalog_with_shapes,
):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:CompA a tcs:PipelineComponent ; tcs:config demo:Def1, demo:Def2 .
        demo:Def1 a tcs:DefaultConfig, tcs:DockerComposeConfig ; tcs:literal "svc1: {}" ; dct:format "text/yaml" .
        demo:Def2 a tcs:DefaultConfig, tcs:DockerComposeConfig ; tcs:literal "svc2: {}" ; dct:format "text/yaml" .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="more than one tcs:DefaultConfig"
    )


def test_pipeline_component_requires_wrong_class_triggers_shape(catalog_with_shapes):
    # Only reachable now that inference_rules.yaml no longer entails
    # tcs:PipelineComponent from bare dct:requires usage (2026-08-04
    # inference-scoping pass) — previously any dct:requires target got
    # auto-typed, making this sh:or check unconditionally satisfied.
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:CompA a tcs:PipelineComponent ; dct:requires demo:NotAComponentOrPackage .
        demo:NotAComponentOrPackage a tcs:Config .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes,
        message_contains="must point at a tcs:PipelineComponent or spdx:Package",
    )


def test_instance_component_specialization_wrong_class_triggers_shape(
    catalog_with_shapes,
):
    # Only reachable now that inference_rules.yaml no longer entails
    # tcs:PipelineComponent from bare prov:specializationOf usage —
    # distinct from test_dangling_specialization_target_triggers_cataloged_shape,
    # where the target doesn't exist at all; here it exists but has the
    # wrong type.
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; p-plan:isStepOfPlan demo:Test ;
            prov:specializationOf demo:NotAComponent .
        demo:NotAComponent a tcs:Config .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="must specialize exactly one"
    )


def test_instance_component_missing_specialization_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; p-plan:isStepOfPlan demo:Test .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="must specialize exactly one"
    )


def test_instance_component_two_specializations_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:CompA a tcs:PipelineComponent .
        demo:CompB a tcs:PipelineComponent .
        demo:A a tcs:InstancePipelineComponent ; p-plan:isStepOfPlan demo:Test ;
            prov:specializationOf demo:CompA, demo:CompB .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="must specialize exactly one"
    )


def test_instance_component_missing_plan_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:LogProcessorJs .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="must belong to exactly one"
    )


def test_pipeline_definition_zero_steps_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
    """,
    )
    assert_shacl_violation(catalog_with_shapes, message_contains="has no steps")


def test_config_neither_embedded_nor_literal_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:BadConfig a tcs:Config .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="must conform to exactly one shape"
    )


def test_config_both_embedded_and_literal_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:BadConfig a tcs:Config ;
            tcs:embedded [ rdfc:reader demo:ch1 ] ;
            tcs:literal "x" ; dct:format "text/plain" .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="must conform to exactly one shape"
    )


def test_config_literal_without_format_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:BadConfig a tcs:Config ; tcs:literal "x" .
    """,
    )
    assert_shacl_violation(catalog_with_shapes, message_contains="no dct:format")


def test_catalog_resource_not_pipeline_component_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        @prefix dcat: <http://www.w3.org/ns/dcat#> .
        demo:TestCatalog a tcs:Catalog ; dcat:resource demo:GhostyResource .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes,
        message_contains="dcat:resource entries must be tcs:PipelineComponent",
    )


def test_pipeline_build_missing_hadplan_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:Build a tcs:PipelineBuild .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="exactly one PipelineDefinition"
    )


def test_pipeline_build_haspart_wrong_class_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:Plan a tcs:PipelineDefinition .
        demo:Build a tcs:PipelineBuild ; prov:hadPlan demo:Plan ; dct:hasPart demo:NotAContainer .
        demo:NotAContainer a tcs:Config .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="must point at a tcs:DockerContainer"
    )


def test_pipeline_build_compiledfile_wrong_class_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:Plan a tcs:PipelineDefinition .
        demo:Build a tcs:PipelineBuild ; prov:hadPlan demo:Plan ; tcs:compiledFile demo:NotAFile .
        demo:NotAFile a tcs:Config .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="must point at an spdx:File"
    )


def test_docker_container_missing_instantiates_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:Container a tcs:DockerContainer .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="must instantiate at least one"
    )


def test_docker_container_instantiates_wrong_class_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:Container a tcs:DockerContainer ; tcs:instantiates demo:NotAComponent .
        demo:NotAComponent a tcs:Config .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="must instantiate at least one"
    )


def test_docker_container_runs_wrong_class_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:CompX a tcs:PipelineComponent .
        demo:Container a tcs:DockerContainer ; tcs:instantiates demo:CompX ; tcs:runs demo:NotAStep .
        demo:NotAStep a tcs:Config .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="tcs:runs must point at"
    )


def test_spdx_package_missing_name_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        @prefix spdx: <http://spdx.org/rdf/terms#> .
        demo:Pkg a spdx:Package ; spdx:suppliedBy demo:SomeSupplier .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="must declare spdx:name"
    )


def test_spdx_package_missing_suppliedby_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        @prefix spdx: <http://spdx.org/rdf/terms#> .
        demo:Pkg a spdx:Package ; spdx:name "some-pkg" .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="must declare spdx:suppliedBy"
    )


# --- Structural application-profile shapes (Section 2 — framework-specific)


def test_rdfc_processor_missing_imports_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:BadProcessor a tcs:PipelineComponent ; dct:requires rdfc:NodeRunner .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="must declare owl:imports"
    )


def test_rdfc_runner_missing_orchestrator_requirement_triggers_shape(
    catalog_with_shapes,
):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:BadRunner a tcs:PipelineComponent, rdfc:Runner ; rdfs:label "Bad Runner" .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="must dct:requires rdfc:Orchestrator"
    )


def test_rdfc_orchestrator_missing_dockercompose_config_triggers_shape():
    # Isolated graph (shapes file only) — sh:targetNode rdfc:Orchestrator
    # fires even with no catalog loaded, letting us see the violation the
    # real catalog's tcs:config triples normally satisfy.
    from rdflib import Graph

    g = Graph()
    g.parse(str(DATA_DIR / SHAPES_FILE), publicID="file:///workspace/pipeline/")
    assert_shacl_violation(
        g, message_contains="must carry at least one tcs:DockerComposeConfig"
    )


def test_rdfc_orchestrator_missing_dockerimage_config_triggers_shape():
    from rdflib import Graph

    g = Graph()
    g.parse(str(DATA_DIR / SHAPES_FILE), publicID="file:///workspace/pipeline/")
    assert_shacl_violation(
        g, message_contains="must carry at least one tcs:DockerImageConfig"
    )


def test_rdfc_package_manager_invalid_supplier_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        @prefix spdx: <http://spdx.org/rdf/terms#> .
        demo:MyComp a tcs:PipelineComponent ; dct:requires rdfc:Orchestrator, demo:BadPkg .
        demo:BadPkg a spdx:Package ; spdx:name "weird-lib" ; spdx:suppliedBy demo:SomeWeirdManager .
    """,
    )
    assert_shacl_violation(catalog_with_shapes, message_contains="must be :pip or :npm")


def test_rdfc_mandatory_writer_wiring_missing_triggers_shape(catalog_with_shapes):
    # rdfc:Sdsify's configShape marks rdfc:output/rdfc:metadataOutput
    # sh:minCount 1 (both sh:class rdfc:Writer) — a step with neither
    # explicit nor synthesized tcs:writesTo cannot actually run.
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:BadSdsify a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:Sdsify ;
            p-plan:isStepOfPlan demo:Test .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="mandatory rdfc:Writer"
    )


def test_rdfc_mandatory_writer_wiring_present_conforms(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:GoodSdsify a tcs:InstancePipelineComponent ; prov:specializationOf rdfc:Sdsify ;
            p-plan:isStepOfPlan demo:Test ; tcs:writesTo demo:ch1 , demo:ch2 .
    """,
    )
    report = load_reader(catalog_with_shapes).validate(advanced=True, inference="rdfs")
    violations = report.select(
        "?message",
        "?r a sh:ValidationResult ; sh:resultMessage ?message .",
    )
    assert (
        not violations["message"]
        .str.contains("mandatory rdfc:Writer", case=False, na=False)
        .any()
    )


def test_ldio_component_type_missing_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:BadLdioComp a tcs:PipelineComponent ;
            dct:requires ldio:LinkedDataInteractionsOrchestrator ; rdfs:label "Bad" .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="must declare exactly one ldio:type"
    )


def test_ldio_component_type_invalid_value_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:BadLdioComp a tcs:PipelineComponent ;
            dct:requires ldio:LinkedDataInteractionsOrchestrator ;
            rdfs:label "Bad" ; ldio:type "Bogus" .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="must declare exactly one ldio:type"
    )


def test_ldio_component_type_missing_label_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:BadLdioComp a tcs:PipelineComponent ;
            dct:requires ldio:LinkedDataInteractionsOrchestrator ; ldio:type "Input" .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="must declare rdfs:label"
    )


def test_ldio_step_seriality_two_reads_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf ldio:SparqlConstructTransformer ;
            p-plan:isStepOfPlan demo:Test ; tcs:readsFrom demo:ch1, demo:ch2 .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="reads from more than one channel"
    )


def test_ldio_step_seriality_two_writes_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf ldio:SparqlConstructTransformer ;
            p-plan:isStepOfPlan demo:Test ; tcs:writesTo demo:ch1, demo:ch2 .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="writes to more than one channel"
    )


def test_ldio_step_ordering_output_not_terminal_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:Out a tcs:InstancePipelineComponent ; prov:specializationOf ldio:HttpOut ;
            p-plan:isStepOfPlan demo:Test ; tcs:writesTo demo:ch1 .
        demo:Next a tcs:InstancePipelineComponent ; prov:specializationOf ldio:SparqlConstructTransformer ;
            p-plan:isStepOfPlan demo:Test ; tcs:readsFrom demo:ch1 .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="Output must be terminal"
    )


def test_ldio_step_ordering_input_not_initial_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:Prev a tcs:InstancePipelineComponent ; prov:specializationOf ldio:SparqlConstructTransformer ;
            p-plan:isStepOfPlan demo:Test ; tcs:writesTo demo:ch2 .
        demo:In a tcs:InstancePipelineComponent ; prov:specializationOf ldio:HttpInPoller ;
            p-plan:isStepOfPlan demo:Test ; tcs:readsFrom demo:ch2 .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="Input must be initial"
    )


def test_sw_component_missing_docker_config_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        sw:TestService a tcs:PipelineComponent .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes,
        message_contains="must directly carry a tcs:DockerComposeConfig",
    )


def test_sw_step_env_value_non_literal_triggers_shape(catalog_with_shapes):
    parse_extra(
        catalog_with_shapes,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .
        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf sw:mu-dispatcher ;
            p-plan:isStepOfPlan demo:Test ;
            p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [ demo:someVar demo:SomeIri ] ] .
    """,
    )
    assert_shacl_violation(
        catalog_with_shapes, message_contains="non-literal value at predicate"
    )
