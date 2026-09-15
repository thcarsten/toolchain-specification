"""Tests for the second requirement-closure pass and the endpoint guard.

:class:`PipelineAssembler` expands the ``dct:requires`` closure of the
components a pipeline *declares*, and the runner runs every compiler at
most once. :class:`BridgeTransportCompiler` runs later and can introduce
a component that was in nobody's closure, whose own requirements then
never get containers — the generated compose file is missing services
the build's own ``tcs:endpoint`` values point at.

:class:`RequirementClosureCompiler` closes that gap by running the
assembler's minting pass a second time, and
``tcs:BridgeEndpointReachableShape`` is the backstop that surfaces the
whole class of mistake in the validation report instead of leaving a
compose file that boots and then 404s. The shape reasons over
``tcs:serviceName``, which :class:`ContainerServiceNameCompiler` projects
out of the compose YAML so SHACL can see it.

The sw-specific end of this is in ``test_sw_ingest_bridge.py``; what
follows is about the core machinery.
"""

from __future__ import annotations

import pytest
import yaml

from rdfine import GraphReader
from testing_helpers import compile_pipeline, parse_extra

PREFIXES = """
@prefix demo_rc: <http://example.org/example/req-closure/> .
@prefix ldio: <http://example.org/example/ldio/> .
@prefix sw: <https://semantic.works/services/> .
@prefix p-plan: <http://purl.org/net/p-plan#> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix tcs: <https://w3id.org/toolchain#> .
"""

_POLLER = """
demo_rc:Test a tcs:PipelineDefinition .

demo_rc:Poll a tcs:InstancePipelineComponent ;
    prov:specializationOf ldio:HttpInPoller ;
    p-plan:isStepOfPlan demo_rc:Test ;
    p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [
        ldio:url "https://example.invalid/api" ;
        ldio:cron "*/10 * * * * *"
    ] ] .
"""

# Cross-framework hop with both boundaries undeclared: the bridge pair is
# auto-inserted, and the sw Entry brings in a mu stack that was in no
# closure when the assembler ran.
NEEDS_SECOND_PASS = PREFIXES + _POLLER + """
demo_rc:Store a tcs:InstancePipelineComponent ;
    prov:specializationOf sw:triple-store ;
    p-plan:isStepOfPlan demo_rc:Test ;
    p-plan:isPrecededBy demo_rc:Poll .
"""

# Single framework, single container: no bridge, so nothing to re-expand.
NO_BRIDGE = PREFIXES + _POLLER + """
demo_rc:Sink a tcs:InstancePipelineComponent ;
    prov:specializationOf ldio:ConsoleOut ;
    p-plan:isStepOfPlan demo_rc:Test ;
    p-plan:isPrecededBy demo_rc:Poll .
"""

# A bridge inserted into a container whose requirement closure is already
# complete: the error-alert step pulls in the whole mu stack on its own, so
# RequirementClosureCompiler has nothing to do, while BridgeTransportCompiler
# still adds rdf-ingest to the container minted for the triple store.
BRIDGE_INTO_COMPLETE_CLOSURE = PREFIXES + _POLLER + """
demo_rc:Store a tcs:InstancePipelineComponent ;
    prov:specializationOf sw:triple-store ;
    p-plan:isStepOfPlan demo_rc:Test ;
    p-plan:isPrecededBy demo_rc:Poll .

demo_rc:Alert a tcs:InstancePipelineComponent ;
    prov:specializationOf sw:loket-error-alert-service ;
    p-plan:isStepOfPlan demo_rc:Test .
"""

# A container declared by hand in the pipeline definition. It carries no
# dct:hasPart edge from the build — a pipeline author has no build node to
# attach one to — so the minting pass must not treat it as satisfying the
# microservice and skip it.
DECLARED_CONTAINER = PREFIXES + _POLLER + """
demo_rc:MyBox a tcs:DockerContainer ;
    tcs:instantiates ldio:LinkedDataInteractionsOrchestrator .
"""


def compose_services(build: GraphReader) -> list[str]:
    node = build.filter(pred="tcs:filename", obj="docker-compose.yml").df["sub"]
    body = str(build.filter(sub=node.iloc[0], pred="tcs:literal").df["obj"].iloc[0])
    return sorted((yaml.safe_load(body).get("services") or {}).keys())


def ran(gen) -> set[str]:
    return {cls.__name__ for cls in gen.compilers}


def test_second_pass_mints_containers_for_inserted_requirements(catalog_graph):
    parse_extra(catalog_graph, NEEDS_SECOND_PASS)
    gen, build = compile_pipeline(catalog_graph, "demo_rc:Test")

    assert "RequirementClosureCompiler" in ran(gen)
    assert {"identifier", "dispatcher", "database"} <= set(compose_services(build))


def test_second_pass_runs_after_the_bridge_that_creates_the_need(catalog_graph):
    """Ordering comes from the trigger, not from list position: straight
    after the assembler the closure is complete, so the compiler is not
    yet eligible."""
    parse_extra(catalog_graph, NEEDS_SECOND_PASS)
    gen, _ = compile_pipeline(catalog_graph, "demo_rc:Test")

    order = [cls.__name__ for cls in gen.compilers]
    assert order.index("RequirementClosureCompiler") > order.index(
        "BridgeTransportCompiler"
    )
    assert order.index("RequirementClosureCompiler") > order.index("PipelineAssembler")


def test_second_pass_stays_out_of_the_way_when_nothing_is_missing(catalog_graph):
    parse_extra(catalog_graph, NO_BRIDGE)
    gen, build = compile_pipeline(catalog_graph, "demo_rc:Test")

    assert "RequirementClosureCompiler" not in ran(gen)
    assert compose_services(build) == ["ldio-workbench"]


def test_no_component_ends_up_with_two_containers(catalog_graph):
    """The minting pass runs twice on this pipeline; the second run must
    skip everything the first one already placed."""
    parse_extra(catalog_graph, NEEDS_SECOND_PASS)
    _, build = compile_pipeline(catalog_graph, "demo_rc:Test")

    duplicated = build.select("?component", """
        ?c1 a tcs:DockerContainer ; tcs:instantiates ?component .
        ?c2 a tcs:DockerContainer ; tcs:instantiates ?component .
        FILTER (?c1 != ?c2)
    """)
    assert duplicated.empty


def test_declared_container_does_not_suppress_minting(catalog_graph):
    """Regression: an early version of the skip-check matched any
    ``tcs:DockerContainer``, so a container declared in the pipeline
    definition — which is not part of the build — made the pass skip the
    microservice, and the build ended up with no container for it and an
    empty compose file."""
    parse_extra(catalog_graph, DECLARED_CONTAINER)
    _, build = compile_pipeline(catalog_graph, "demo_rc:Test")

    assert compose_services(build) == ["ldio-workbench"]
    assert build.ask("?container tcs:runs demo_rc:Poll .")


def reachability_violations(gen) -> list[str]:
    """Messages `tcs:BridgeEndpointReachableShape` produced, if any."""
    from compilers import ValidationReportCompiler

    report = gen.compilers[ValidationReportCompiler]._shacl_report
    messages = report.select(
        "?message",
        "?result a sh:ValidationResult ; sh:resultMessage ?message .",
    )["message"].astype(str)
    return [m for m in messages if "not declared as a compose service" in m]


def test_service_names_are_projected_for_shacl_to_read(catalog_with_shapes):
    """The compose service name lives only inside YAML in a tcs:literal,
    which SHACL cannot parse — hence the projection onto the container."""
    parse_extra(catalog_with_shapes, NEEDS_SECOND_PASS)
    _, build = compile_pipeline(catalog_with_shapes, "demo_rc:Test")

    projected = set(
        build.select("?name", "?container tcs:serviceName ?name .")["name"].astype(str)
    )
    assert {"identifier", "dispatcher", "database", "rdf-ingest", "triplestore"} <= (
        projected
    )


def test_a_container_projects_every_service_it_contributes(catalog_with_shapes):
    """BridgeTransportCompiler attaches an inserted boundary component to
    the container on its side of the hop, so one container can carry both
    the ingest service and the triple store. Taking only the first name
    would leave the other invisible to the shape."""
    parse_extra(catalog_with_shapes, NEEDS_SECOND_PASS)
    _, build = compile_pipeline(catalog_with_shapes, "demo_rc:Test")

    assert build.ask("""
        ?container tcs:serviceName "rdf-ingest" , "triplestore" .
    """)


def test_service_names_are_projected_after_the_last_instantiates_edge(
    catalog_with_shapes,
):
    """Why the projection is a finalize-phase pass and not done when a
    container is minted.

    Here the error-alert step's dct:requires chain already drags in the
    whole mu stack, so the closure is complete and
    RequirementClosureCompiler never runs — yet
    BridgeTransportCompiler still attaches rdf-ingest to the container
    PipelineAssembler minted for the triple store alone. Deriving the
    name at mint time would record only "triplestore" there, with
    nothing running afterwards to top it up: tcs:instantiates edges are
    written later than any mint point, and by a compiler that does not
    always run.
    """
    parse_extra(catalog_with_shapes, BRIDGE_INTO_COMPLETE_CLOSURE)
    gen, build = compile_pipeline(catalog_with_shapes, "demo_rc:Test")

    assert "RequirementClosureCompiler" not in ran(gen)
    assert build.ask("""
        ?container tcs:instantiates sw:triple-store , sw:rdf-ingest-service ;
                   tcs:serviceName "triplestore" , "rdf-ingest" .
    """)
    assert reachability_violations(gen) == []


@pytest.mark.parametrize("pipeline", [NEEDS_SECOND_PASS, NO_BRIDGE])
def test_reachability_shape_is_silent_on_a_well_formed_build(
    catalog_with_shapes, pipeline
):
    parse_extra(catalog_with_shapes, pipeline)
    gen, _ = compile_pipeline(catalog_with_shapes, "demo_rc:Test")

    assert reachability_violations(gen) == []


def test_reachability_shape_catches_a_host_no_service_declares(
    catalog_with_shapes, monkeypatch
):
    """With the second pass disabled the build reverts to the original
    defect — an endpoint naming a gateway the compose file never declares.

    Reported rather than raised: the check lives in SHACL, so generation
    still produces its files and the violation lands in the report, the
    way `tcs:CatalogMissingBridgeShape` handles an equally broken build.
    """
    from compilers import RequirementClosureCompiler

    monkeypatch.setattr(
        RequirementClosureCompiler,
        "applies_to",
        classmethod(lambda cls, graph_reader: False),
    )
    parse_extra(catalog_with_shapes, NEEDS_SECOND_PASS)
    gen, _ = compile_pipeline(catalog_with_shapes, "demo_rc:Test")

    violations = reachability_violations(gen)
    assert len(violations) == 1
    assert "http://identifier/ingest" in violations[0]
