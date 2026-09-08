"""Tests for DockerComposeCompiler.fold_in_depends_on() — one test per
case in its own docstring, plus a real-demonstrator regression check."""

from compilers import DockerComposeCompiler

from testing_helpers import compile_pipeline, parse_extra

PREFIXES = """
@prefix demo: <http://example.org/example/demonstrator/> .
@prefix p-plan: <http://purl.org/net/p-plan#> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix tcs: <https://w3id.org/toolchain#> .
@prefix dct: <http://purl.org/dc/terms/> .
"""


def _two_microservices_ttl(*, requires: bool) -> str:
    """Two standalone microservice components (each owning its own
    tcs:DockerComposeConfig), optionally wired with an explicit
    dct:requires edge from A to B.
    """
    requires_triple = "demo:CompA dct:requires demo:CompB ." if requires else ""
    return PREFIXES + f"""
        demo:Test a tcs:PipelineDefinition .

        demo:CompA a tcs:PipelineComponent ; tcs:config demo:ConfigA .
        demo:ConfigA a tcs:Config, tcs:DockerComposeConfig ;
            tcs:literal "svca:\\n  image: a" ; dct:format "text/yaml" .
        demo:CompB a tcs:PipelineComponent ; tcs:config demo:ConfigB .
        demo:ConfigB a tcs:Config, tcs:DockerComposeConfig ;
            tcs:literal "svcb:\\n  image: b" ; dct:format "text/yaml" .
        {requires_triple}

        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf demo:CompA ;
            p-plan:isStepOfPlan demo:Test .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf demo:CompB ;
            p-plan:isStepOfPlan demo:Test .
    """


def _compose_file(catalog_graph, pipeline_id="demo:Test") -> dict:
    gen, _ = compile_pipeline(catalog_graph, pipeline_id)
    return gen.compilers[DockerComposeCompiler].compose_file


def test_explicit_requires_between_two_microservices_yields_depends_on(catalog_graph):
    parse_extra(catalog_graph, _two_microservices_ttl(requires=True))
    compose_file = _compose_file(catalog_graph)
    assert compose_file["services"]["svca"]["depends_on"] == ["svcb"]
    assert "depends_on" not in compose_file["services"]["svcb"]


def test_no_requires_and_no_channel_yields_no_depends_on_key(catalog_graph):
    parse_extra(catalog_graph, _two_microservices_ttl(requires=False))
    compose_file = _compose_file(catalog_graph)
    assert "depends_on" not in compose_file["services"]["svca"]
    assert "depends_on" not in compose_file["services"]["svcb"]


def test_requires_through_non_microservice_does_not_self_depend(catalog_graph):
    """A processor-style component with no compose config of its own,
    requiring a real microservice, gets folded into that microservice's
    *same* container by PipelineAssembler — there is only ever one
    service in play, so no depends_on (let alone a self-edge) should
    ever be emitted for it.
    """
    parse_extra(
        catalog_graph,
        PREFIXES + """
        demo:Test a tcs:PipelineDefinition .

        demo:CompA a tcs:PipelineComponent ; tcs:config demo:ConfigA .
        demo:ConfigA a tcs:Config, tcs:DockerComposeConfig ;
            tcs:literal "svca:\\n  image: a" ; dct:format "text/yaml" .
        demo:CompHelper a tcs:PipelineComponent ; dct:requires demo:CompA .

        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf demo:CompA ;
            p-plan:isStepOfPlan demo:Test .
        demo:H a tcs:InstancePipelineComponent ; prov:specializationOf demo:CompHelper ;
            p-plan:isStepOfPlan demo:Test .
    """,
    )
    compose_file = _compose_file(catalog_graph)
    assert "depends_on" not in compose_file["services"]["svca"]


def test_channel_across_containers_yields_floworder_fallback_depends_on(catalog_graph):
    """No dct:requires at all between the two microservices — the
    producing step's container should still end up depending on the
    consuming step's container, purely from the channel crossing them
    (mirrors the demonstrator's hand-written ldio-workbench -> rdfc
    edge, which has no dct:requires counterpart either).
    """
    parse_extra(
        catalog_graph,
        _two_microservices_ttl(requires=False) + """
        demo:A tcs:writesTo demo:ch1 .
        demo:B tcs:readsFrom demo:ch1 .
    """,
    )
    compose_file = _compose_file(catalog_graph)
    assert compose_file["services"]["svca"]["depends_on"] == ["svcb"]
    assert "depends_on" not in compose_file["services"]["svcb"]


def test_explicit_requires_suppresses_reverse_floworder_fallback(catalog_graph):
    """dct:requires says A depends on B, but the channel flows the
    opposite way (B produces, A consumes) — the flow-order fallback
    would want to add a B -> A edge too, which must be suppressed: it's
    the same container pair the explicit source already has an opinion
    about, and docker-compose rejects a mutual (2-cycle) depends_on.
    """
    parse_extra(
        catalog_graph,
        _two_microservices_ttl(requires=True) + """
        demo:B tcs:writesTo demo:ch1 .
        demo:A tcs:readsFrom demo:ch1 .
    """,
    )
    compose_file = _compose_file(catalog_graph)
    assert compose_file["services"]["svca"]["depends_on"] == ["svcb"]
    assert "depends_on" not in compose_file["services"]["svcb"]


def test_real_demonstrator_ldio_workbench_depends_on_rdfc(demonstrator_graph):
    """End-to-end regression: the real demonstrator's hand-written
    ``ldio-workbench: depends_on: [rdfc]`` (see demonstrator/docker-compose.yml)
    should now be produced automatically by the flow-order fallback —
    there is no dct:requires edge between the two components at all.
    """
    gen, _ = compile_pipeline(demonstrator_graph, "demo:DishacledPipeline")
    compose_file = gen.compilers[DockerComposeCompiler].compose_file
    assert "rdfc" in compose_file["services"]["ldio-workbench"]["depends_on"]


def test_non_default_compose_replaces_default_and_keeps_networks(catalog_graph):
    """A pipeline-assigned DockerComposeConfig shadows the catalog
    DefaultConfig on the same component (tcs:DefaultConfig semantics).
    """
    parse_extra(
        catalog_graph,
        PREFIXES
        + """
        demo:Test a tcs:PipelineDefinition .

        demo:CompA a tcs:PipelineComponent ;
            tcs:config demo:DefaultA, demo:OverrideA .
        demo:DefaultA a tcs:Config, tcs:DefaultConfig, tcs:DockerComposeConfig ;
            tcs:literal "svca:\\n  image: from-default" ; dct:format "text/yaml" .
        demo:OverrideA a tcs:Config, tcs:DockerComposeConfig ;
            dct:format "text/yaml" ;
            tcs:literal \"\"\"svca:
  image: from-override
networks:
  default:
    name: extra-net
    external: true
\"\"\" .

        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf demo:CompA ;
            p-plan:isStepOfPlan demo:Test .
        """,
    )
    compose_file = _compose_file(catalog_graph)
    assert compose_file["services"]["svca"]["image"] == "from-override"
    assert compose_file["networks"]["default"]["name"] == "extra-net"
    assert compose_file["networks"]["default"]["external"] is True


def test_two_plans_compile_only_seeded_pipeline_services(catalog_graph):
    """Two ``tcs:PipelineDefinition`` nodes in one graph must not skip
    ``PipelineAssembler``. Compile keys off ``prov:hadPlan``, so PlanA
    gets ``svca`` and not PlanB's ``svcb``.
    """
    parse_extra(
        catalog_graph,
        PREFIXES
        + """
        demo:PlanA a tcs:PipelineDefinition .
        demo:PlanB a tcs:PipelineDefinition .

        demo:CompA a tcs:PipelineComponent ; tcs:config demo:ConfigA .
        demo:ConfigA a tcs:Config, tcs:DockerComposeConfig ;
            tcs:literal "svca:\\n  image: a" ; dct:format "text/yaml" .
        demo:CompB a tcs:PipelineComponent ; tcs:config demo:ConfigB .
        demo:ConfigB a tcs:Config, tcs:DockerComposeConfig ;
            tcs:literal "svcb:\\n  image: b" ; dct:format "text/yaml" .

        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf demo:CompA ;
            p-plan:isStepOfPlan demo:PlanA .
        demo:B a tcs:InstancePipelineComponent ; prov:specializationOf demo:CompB ;
            p-plan:isStepOfPlan demo:PlanB .
        """,
    )
    compose_a = _compose_file(catalog_graph, "demo:PlanA")
    assert set(compose_a["services"]) == {"svca"}
    compose_b = _compose_file(catalog_graph, "demo:PlanB")
    assert set(compose_b["services"]) == {"svcb"}


def test_plan_compose_overlay_and_identifier_isolates_ldio(catalog_graph):
    """Plan compose overlays ports; dct:identifier becomes the LDIO service name."""
    parse_extra(
        catalog_graph,
        PREFIXES
        + """
        demo:Test a tcs:PipelineDefinition ;
            dct:identifier "plan-a" ;
            tcs:config demo:Ports .

        demo:CompA a tcs:PipelineComponent ; tcs:config demo:ConfigA .
        demo:ConfigA a tcs:Config, tcs:DockerComposeConfig ;
            dct:format "text/yaml" ;
            tcs:literal \"\"\"ldio-workbench:
  container_name: ldio-workbench
  image: a
  ports:
    - "8080:8080"
\"\"\" .
        demo:Ports a tcs:Config, tcs:DockerComposeConfig ;
            dct:format "text/yaml" ;
            tcs:literal \"\"\"ldio-workbench:
  image: a
  ports:
    - "8084:8080"
\"\"\" .

        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf demo:CompA ;
            p-plan:isStepOfPlan demo:Test .
        """,
    )
    compose_file = _compose_file(catalog_graph)
    assert "ldio-workbench" not in compose_file["services"]
    service = compose_file["services"]["plan-a"]
    assert service["image"] == "a"
    assert service["ports"] == ["8084:8080"]
    assert "container_name" not in service


def test_plan_compose_overlay_publishes_triplestore_ports(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES
        + """
        demo:Test a tcs:PipelineDefinition ;
            tcs:config demo:VirtuosoPorts .

        demo:CompA a tcs:PipelineComponent ; tcs:config demo:ConfigA .
        demo:ConfigA a tcs:Config, tcs:DockerComposeConfig ;
            dct:format "text/yaml" ;
            tcs:literal \"\"\"triplestore:
  image: redpencil/virtuoso:1.4.0
  environment:
    SPARQL_UPDATE: 'true'
\"\"\" .
        demo:VirtuosoPorts a tcs:Config, tcs:DockerComposeConfig ;
            dct:format "text/yaml" ;
            tcs:literal \"\"\"triplestore:
  image: redpencil/virtuoso:1.4.0
  ports:
    - "8890:8890"
\"\"\" .

        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf demo:CompA ;
            p-plan:isStepOfPlan demo:Test .
        """,
    )
    service = _compose_file(catalog_graph)["services"]["triplestore"]
    assert service["image"] == "redpencil/virtuoso:1.4.0"
    assert service["environment"]["SPARQL_UPDATE"] == "true"
    assert service["ports"] == ["8890:8890"]


def test_identifier_retags_rdf_connect_image(catalog_graph):
    parse_extra(
        catalog_graph,
        PREFIXES
        + """
        demo:Test a tcs:PipelineDefinition ; dct:identifier "consumer-pipe" .

        demo:CompA a tcs:PipelineComponent ; tcs:config demo:ConfigA .
        demo:ConfigA a tcs:Config, tcs:DockerComposeConfig ;
            dct:format "text/yaml" ;
            tcs:literal "rdfc:\\n  container_name: rdfc\\n  image: rdf-connect:latest" .

        demo:A a tcs:InstancePipelineComponent ; prov:specializationOf demo:CompA ;
            p-plan:isStepOfPlan demo:Test .
        """,
    )
    compose_file = _compose_file(catalog_graph)
    assert compose_file["services"]["rdfc"]["image"] == "rdf-connect:consumer-pipe"
    assert "container_name" not in compose_file["services"]["rdfc"]

