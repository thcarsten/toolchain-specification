"""Bridge tests for ``sw:rdf-ingest-service``, the first semantic.works
``tcs:EntryBoundaryComponent``.

The sw side of an HTTP bridge differs from the LDIO / NiFi / RDF-Connect
Entry components in two ways, and most of what follows pins those down:

- The endpoint names the mu stack's **gateway** (``sw:mu-identifier``),
  not the ingest service itself. A mu write enters at the identifier so
  that mu-authorization can derive the target graph from the session;
  that is why the service's INSERTs carry no ``GRAPH`` clause, and why
  POSTing straight at ``rdf-ingest`` would be refused.
- :class:`SwRdfIngestConfigCompiler` attaches no config to its own step,
  so its trigger is the *channel* lacking a ``tcs:endpoint`` and the hop
  being cross-container — not the step lacking a config.

``test_config_compiler_runs_after_pipeline_assembler`` is the regression
guard for the second point: an earlier trigger that only asked for an
unannotated channel fired on the very first pass, before containers
existed at all.
"""

from __future__ import annotations

import yaml
from rdflib import Graph

from rdfine import GraphReader
from testing_helpers import compile_pipeline, parse_extra

PREFIXES = """
@prefix demo_sw: <http://example.org/example/ldio-sw/> .
@prefix ldio: <http://example.org/example/ldio/> .
@prefix sw: <https://semantic.works/services/> .
@prefix p-plan: <http://purl.org/net/p-plan#> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix tcs: <https://w3id.org/toolchain#> .
"""

_POLLER = """
demo_sw:Test a tcs:PipelineDefinition .

demo_sw:Poll a tcs:InstancePipelineComponent ;
    prov:specializationOf ldio:HttpInPoller ;
    p-plan:isStepOfPlan demo_sw:Test ;
    p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [
        ldio:url "https://example.invalid/api" ;
        ldio:cron "*/10 * * * * *"
    ] ] .
"""

# An LDIO poller feeding a semantic.works triple store, with both sides of
# the cross-container hop left undeclared: BridgeTransportCompiler is
# expected to insert ldio:HttpOut upstream and sw:rdf-ingest-service
# downstream, because the latter's dct:requires chain reaches the store.
LDIO_TO_SW_AUTO = PREFIXES + _POLLER + """
demo_sw:Store a tcs:InstancePipelineComponent ;
    prov:specializationOf sw:triple-store ;
    p-plan:isStepOfPlan demo_sw:Test ;
    p-plan:isPrecededBy demo_sw:Poll .
"""

# The same hop with both boundary steps named by hand. BridgeTransportCompiler
# trusts a declared boundary at face value and inserts nothing; the config
# compilers still have to agree the endpoint through the shared channel.
LDIO_TO_SW_EXPLICIT = PREFIXES + _POLLER + """
demo_sw:Out a tcs:InstancePipelineComponent ;
    prov:specializationOf ldio:HttpOut ;
    p-plan:isStepOfPlan demo_sw:Test ;
    p-plan:isPrecededBy demo_sw:Poll .

demo_sw:Ingest a tcs:InstancePipelineComponent ;
    prov:specializationOf sw:rdf-ingest-service ;
    p-plan:isStepOfPlan demo_sw:Test ;
    p-plan:isPrecededBy demo_sw:Out .
"""

# rdf-ingest as the *writer* of the channel rather than its reader. The
# service only ever receives, so the compiler must stay out of it.
SW_INGEST_AS_WRITER = PREFIXES + """
demo_sw:Test a tcs:PipelineDefinition .

demo_sw:Ingest a tcs:InstancePipelineComponent ;
    prov:specializationOf sw:rdf-ingest-service ;
    p-plan:isStepOfPlan demo_sw:Test .

demo_sw:Store a tcs:InstancePipelineComponent ;
    prov:specializationOf sw:triple-store ;
    p-plan:isStepOfPlan demo_sw:Test ;
    p-plan:isPrecededBy demo_sw:Ingest .
"""


def compose_services(build: GraphReader) -> list[str]:
    """Service keys of the emitted docker-compose.yml."""
    node = build.filter(pred="tcs:filename", obj="docker-compose.yml").df["sub"].iloc[0]
    body = str(build.filter(sub=node, pred="tcs:literal").df["obj"].iloc[0])
    return sorted((yaml.safe_load(body).get("services") or {}).keys())


def test_ldio_to_sw_inserts_ingest_entry_pair(catalog_graph):
    parse_extra(catalog_graph, LDIO_TO_SW_AUTO)
    gen, build = compile_pipeline(catalog_graph, "demo_sw:Test")

    ran = {cls.__name__ for cls in gen.compilers}
    assert "BridgeTransportCompiler" in ran
    assert "SwRdfIngestConfigCompiler" in ran
    assert "LdioHttpOutConfigCompiler" in ran

    assert build.ask("""
        ?step a tcs:InstancePipelineComponent ;
              prov:specializationOf sw:rdf-ingest-service ;
              tcs:readsFrom ?channel .
    """)
    assert build.ask("""
        ?step a tcs:InstancePipelineComponent ;
              prov:specializationOf ldio:HttpOut ;
              tcs:writesTo ?channel .
    """)


def test_endpoint_targets_the_gateway_not_the_service(catalog_graph):
    """The whole point of the sw Entry: traffic enters at the identifier.

    An endpoint naming ``rdf-ingest`` directly would bypass the session
    header mu-authorization needs, and the write would come back 403.
    """
    parse_extra(catalog_graph, LDIO_TO_SW_AUTO)
    _, build = compile_pipeline(catalog_graph, "demo_sw:Test")

    endpoints = build.filter(pred="tcs:endpoint").df["obj"].astype(str).tolist()
    assert endpoints == ["http://identifier/ingest"]


def test_channel_carries_port_and_content_type(catalog_graph):
    parse_extra(catalog_graph, LDIO_TO_SW_AUTO)
    _, build = compile_pipeline(catalog_graph, "demo_sw:Test")

    assert build.ask("""
        ?channel tcs:endpoint "http://identifier/ingest" ;
                 tcs:port 80 ;
                 tcs:contentType "application/n-triples" .
    """)


def test_ldio_exit_adopts_channel_endpoint_and_content_type(catalog_graph):
    """The Exit side must take the channel's content type over its own
    ``application/ld+json`` default — rdf-ingest picks its parser from
    the request header and would reject a serialisation it can't read."""
    parse_extra(catalog_graph, LDIO_TO_SW_AUTO)
    _, build = compile_pipeline(catalog_graph, "demo_sw:Test")

    rows = build.select("?endpoint ?content_type", """
        ?step prov:specializationOf ldio:HttpOut ;
              p-plan:hasInputVar/tcs:embedded ?config .
        ?config ldio:endpoint ?endpoint ;
                ldio:rdf-writer/ldio:content-type ?content_type .
    """)
    assert len(rows) == 1
    assert rows["endpoint"].iloc[0] == "http://identifier/ingest"
    assert rows["content_type"].iloc[0] == "application/n-triples"


def test_config_compiler_runs_after_pipeline_assembler(catalog_graph):
    """Regression: the trigger used to ask only for an unannotated
    channel, which is true on the very first pass — so the compiler fired
    before PipelineAssembler had placed any step in a container, and
    would have annotated channels that never cross a container boundary.
    Requiring a cross-container hop is what orders it correctly."""
    parse_extra(catalog_graph, LDIO_TO_SW_EXPLICIT)
    gen, _ = compile_pipeline(catalog_graph, "demo_sw:Test")

    order = [cls.__name__ for cls in gen.compilers]
    assert order.index("SwRdfIngestConfigCompiler") > order.index("PipelineAssembler")
    assert order.index("SwRdfIngestConfigCompiler") > order.index(
        "BridgeTransportCompiler"
    )


def test_does_not_fire_when_ingest_is_the_writer(catalog_graph):
    parse_extra(catalog_graph, SW_INGEST_AS_WRITER)
    gen, build = compile_pipeline(catalog_graph, "demo_sw:Test")

    assert "SwRdfIngestConfigCompiler" not in {c.__name__ for c in gen.compilers}
    assert build.filter(pred="tcs:endpoint").df.empty


def test_explicit_declaration_emits_the_full_mu_stack(catalog_graph):
    """Declared up front, rdf-ingest is in PipelineAssembler's
    dct:requires closure, so the gateway it points at is really there."""
    parse_extra(catalog_graph, LDIO_TO_SW_EXPLICIT)
    _, build = compile_pipeline(catalog_graph, "demo_sw:Test")

    services = compose_services(build)
    assert {"identifier", "dispatcher", "database", "rdf-ingest"} <= set(services)


def test_auto_inserted_bridge_emits_its_gateway_services(catalog_graph):
    """The counterpart of the test above, for the auto-inserted bridge.

    This is the case RequirementClosureCompiler exists for: rdf-ingest is
    in no closure when PipelineAssembler runs, so without a second pass
    the endpoint would name a gateway the compose file never declares.
    """
    parse_extra(catalog_graph, LDIO_TO_SW_AUTO)
    _, build = compile_pipeline(catalog_graph, "demo_sw:Test")

    services = compose_services(build)
    assert {"identifier", "dispatcher", "database"} <= set(services)


RDFC_EXIT = """
@prefix rdfc: <https://w3id.org/rdf-connect#> .
@prefix tcs: <https://w3id.org/toolchain#> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix p-plan: <http://purl.org/net/p-plan#> .
@prefix : <http://example.org/example/> .

:advertised a tcs:InstancePipelineComponent ;
    prov:specializationOf rdfc:HttpOut ; tcs:writesTo :ch_advertised .
:ch_advertised a tcs:Channel ;
    tcs:endpoint "http://identifier/ingest" ;
    tcs:contentType "application/n-triples" .

:silent a tcs:InstancePipelineComponent ;
    prov:specializationOf rdfc:HttpOut ; tcs:writesTo :ch_silent .
:ch_silent a tcs:Channel ; tcs:endpoint "http://elsewhere/in" .
"""


def test_rdfc_exit_forwards_content_type_only_when_advertised():
    """``rdfc:content_type`` is optional per :HttpOutShape, so it is
    omitted rather than defaulted when the channel says nothing — that
    leaves the processor's own default standing."""
    from compilers import RdfcHttpOutConfigCompiler

    graph = Graph().parse(data=RDFC_EXIT, format="turtle")
    build = GraphReader(RdfcHttpOutConfigCompiler(graph).compile())

    assert build.ask("""
        :advertised p-plan:hasInputVar/tcs:embedded ?config .
        ?config rdfc:endpoint "http://identifier/ingest" ;
                rdfc:content_type "application/n-triples" .
    """)
    assert not build.ask("""
        :silent p-plan:hasInputVar/tcs:embedded ?config .
        ?config rdfc:content_type ?any .
    """)
