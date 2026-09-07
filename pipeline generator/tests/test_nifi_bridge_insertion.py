"""Bridge insertion tests for the NiFi Entry/Exit boundary components.

Two synthetic mini-pipelines exercise the auto-insertion path
:class:`BridgeTransportCompiler` opens up now that ``nifi:ListenHTTP``
is typed ``tcs:EntryBoundaryComponent`` and ``nifi:InvokeHTTP`` is typed
``tcs:ExitBoundaryComponent`` in the catalog:

- LDIO writer + NiFi reader: an LDIO transformer writes to a channel
  that a NiFi Funnel reads, both in their own container. The compiler
  is expected to insert ``ldio:HttpOut`` on the LDIO side and
  ``nifi:ListenHTTP`` on the NiFi side, and the two per-boundary config
  compilers agree the endpoint through the shared channel.
- NiFi writer + LDIO reader: symmetric. ``nifi:InvokeHTTP`` on the NiFi
  side (pointed at the LDIO listener's URL), ``ldio:HttpIn`` on the
  LDIO side.

Also covers port-collision behaviour: two independent ``nifi:ListenHTTP``
steps in the same NiFi container must not land on the same port.
"""

from __future__ import annotations

import json

from testing_helpers import compile_pipeline, parse_extra

PREFIXES = """
@prefix demo_ln: <http://example.org/example/ldio-nifi/> .
@prefix ldio: <http://example.org/example/ldio/> .
@prefix nifi: <http://example.org/example/nifi/> .
@prefix p-plan: <http://purl.org/net/p-plan#> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix tcs: <https://w3id.org/toolchain#> .
"""

LDIO_TO_NIFI = PREFIXES + """
demo_ln:Test a tcs:PipelineDefinition .

demo_ln:Poll a tcs:InstancePipelineComponent ;
    prov:specializationOf ldio:HttpInPoller ;
    p-plan:isStepOfPlan demo_ln:Test ;
    p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [
        ldio:url "https://example.invalid/api" ;
        ldio:cron "*/10 * * * * *"
    ] ] .

demo_ln:Parse a tcs:InstancePipelineComponent ;
    prov:specializationOf ldio:JsonToLdAdapter ;
    p-plan:isStepOfPlan demo_ln:Test ;
    p-plan:isPrecededBy demo_ln:Poll ;
    p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [
        ldio:force-content-type true ;
        ldio:context "{}"
    ] ] .

demo_ln:Sink a tcs:InstancePipelineComponent ;
    prov:specializationOf nifi:Funnel ;
    p-plan:isStepOfPlan demo_ln:Test ;
    p-plan:isPrecededBy demo_ln:Parse .
"""

NIFI_TO_LDIO = PREFIXES + """
demo_ln:Test a tcs:PipelineDefinition .

demo_ln:Emit a tcs:InstancePipelineComponent ;
    prov:specializationOf nifi:GenerateFlowFile ;
    p-plan:isStepOfPlan demo_ln:Test ;
    nifi:scheduledState "RUNNING" ;
    nifi:schedulingPeriod "10 sec" ;
    p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [
        nifi:customText "hello"
    ] ] .

demo_ln:Downstream a tcs:InstancePipelineComponent ;
    prov:specializationOf ldio:ConsoleOut ;
    p-plan:isStepOfPlan demo_ln:Test ;
    p-plan:isPrecededBy demo_ln:Emit ;
    p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [] ] .
"""

# Two LDIO-to-NiFi bridges in one pipeline. Each independent LDIO segment
# writes into its own channel that a separate NiFi Funnel reads. The bridge
# compiler must insert two ListenHTTP steps in the NiFi container without
# them colliding on the same port.
TWO_BRIDGES = PREFIXES + """
demo_ln:Test a tcs:PipelineDefinition .

demo_ln:PollA a tcs:InstancePipelineComponent ;
    prov:specializationOf ldio:HttpInPoller ;
    p-plan:isStepOfPlan demo_ln:Test ;
    p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [
        ldio:url "https://a.example.invalid/api" ;
        ldio:cron "*/10 * * * * *"
    ] ] .

demo_ln:ParseA a tcs:InstancePipelineComponent ;
    prov:specializationOf ldio:JsonToLdAdapter ;
    p-plan:isStepOfPlan demo_ln:Test ;
    p-plan:isPrecededBy demo_ln:PollA ;
    p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [
        ldio:force-content-type true ;
        ldio:context "{}"
    ] ] .

demo_ln:SinkA a tcs:InstancePipelineComponent ;
    prov:specializationOf nifi:Funnel ;
    p-plan:isStepOfPlan demo_ln:Test ;
    p-plan:isPrecededBy demo_ln:ParseA .

demo_ln:PollB a tcs:InstancePipelineComponent ;
    prov:specializationOf ldio:HttpInPoller ;
    p-plan:isStepOfPlan demo_ln:Test ;
    p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [
        ldio:url "https://b.example.invalid/api" ;
        ldio:cron "*/10 * * * * *"
    ] ] .

demo_ln:ParseB a tcs:InstancePipelineComponent ;
    prov:specializationOf ldio:JsonToLdAdapter ;
    p-plan:isStepOfPlan demo_ln:Test ;
    p-plan:isPrecededBy demo_ln:PollB ;
    p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [
        ldio:force-content-type true ;
        ldio:context "{}"
    ] ] .

demo_ln:SinkB a tcs:InstancePipelineComponent ;
    prov:specializationOf nifi:Funnel ;
    p-plan:isStepOfPlan demo_ln:Test ;
    p-plan:isPrecededBy demo_ln:ParseB .
"""


def _flow_json(build) -> dict:
    """Extract the emitted ``nifi/flow.json`` as a parsed dict."""
    df = build.filter(pred="tcs:filename", obj="flow.json").df
    file_node = df["sub"].iloc[0]
    content_df = build.filter(sub=file_node, pred="tcs:literal").df
    return json.loads(str(content_df["obj"].iloc[0]))


def _segment_yaml(build, filename: str = "segment_1.yml") -> str:
    """Return the body of an emitted ``ldio/pipelines/<name>`` file."""
    df = build.filter(pred="tcs:filename", obj=filename).df
    file_node = df["sub"].iloc[0]
    content_df = build.filter(sub=file_node, pred="tcs:literal").df
    return str(content_df["obj"].iloc[0])


def test_ldio_to_nifi_inserts_boundary_pair(catalog_graph):
    parse_extra(catalog_graph, LDIO_TO_NIFI)
    gen, build = compile_pipeline(catalog_graph, "demo_ln:Test")

    ran = {cls.__name__ for cls in gen.compilers}
    assert "BridgeTransportCompiler" in ran
    assert "NifiListenHttpConfigCompiler" in ran
    assert "LdioHttpOutConfigCompiler" in ran

    inserted_listen = build.ask("""
        ?step a tcs:InstancePipelineComponent ;
              prov:specializationOf nifi:ListenHTTP .
    """)
    assert inserted_listen, "expected an auto-inserted nifi:ListenHTTP boundary step"

    inserted_httpout = build.ask("""
        ?step a tcs:InstancePipelineComponent ;
              prov:specializationOf ldio:HttpOut .
    """)
    assert inserted_httpout, "expected an auto-inserted ldio:HttpOut boundary step"


def test_ldio_to_nifi_agrees_endpoint_through_channel(catalog_graph):
    parse_extra(catalog_graph, LDIO_TO_NIFI)
    _, build = compile_pipeline(catalog_graph, "demo_ln:Test")

    endpoints = build.select(
        "?endpoint",
        """
        ?listen prov:specializationOf nifi:ListenHTTP ;
                tcs:readsFrom ?channel .
        ?exit   prov:specializationOf ldio:HttpOut ;
                tcs:writesTo ?channel .
        ?channel tcs:endpoint ?endpoint .
        """,
    )
    assert len(endpoints) == 1
    endpoint = str(endpoints["endpoint"].iloc[0])
    assert endpoint.startswith("http://nifip:9000/")


def test_ldio_to_nifi_listen_config_lands_in_flow_json(catalog_graph):
    parse_extra(catalog_graph, LDIO_TO_NIFI)
    _, build = compile_pipeline(catalog_graph, "demo_ln:Test")
    flow = _flow_json(build)

    processors = flow["rootGroup"]["processors"]
    listen = next(
        p
        for p in processors
        if p["type"] == "org.apache.nifi.processors.standard.ListenHTTP"
    )
    assert listen["properties"]["Listening Port"] == "9000"
    assert listen["properties"]["Base Path"] == listen["name"]


def test_ldio_to_nifi_ldio_output_carries_endpoint(catalog_graph):
    parse_extra(catalog_graph, LDIO_TO_NIFI)
    _, build = compile_pipeline(catalog_graph, "demo_ln:Test")
    yaml_body = _segment_yaml(build)

    assert "Ldio:HttpOut" in yaml_body
    assert "http://nifip:9000/" in yaml_body


def test_nifi_to_ldio_inserts_boundary_pair(catalog_graph):
    parse_extra(catalog_graph, NIFI_TO_LDIO)
    gen, build = compile_pipeline(catalog_graph, "demo_ln:Test")

    ran = {cls.__name__ for cls in gen.compilers}
    assert "BridgeTransportCompiler" in ran
    assert "NifiInvokeHttpConfigCompiler" in ran
    assert "LdioHttpInConfigCompiler" in ran

    inserted_invoke = build.ask("""
        ?step a tcs:InstancePipelineComponent ;
              prov:specializationOf nifi:InvokeHTTP .
    """)
    assert inserted_invoke, "expected an auto-inserted nifi:InvokeHTTP boundary step"

    inserted_httpin = build.ask("""
        ?step a tcs:InstancePipelineComponent ;
              prov:specializationOf ldio:HttpIn .
    """)
    assert inserted_httpin, "expected an auto-inserted ldio:HttpIn boundary step"


def test_nifi_to_ldio_invoke_url_points_at_ldio(catalog_graph):
    parse_extra(catalog_graph, NIFI_TO_LDIO)
    _, build = compile_pipeline(catalog_graph, "demo_ln:Test")
    flow = _flow_json(build)

    processors = flow["rootGroup"]["processors"]
    invoke = next(
        p
        for p in processors
        if p["type"] == "org.apache.nifi.processors.standard.InvokeHTTP"
    )
    assert invoke["properties"]["HTTP Method"] == "POST"
    url = invoke["properties"]["HTTP URL"]
    assert url.startswith("http://ldio-workbench:8080/")
    # Content-Type is propagated from LdioHttpInConfigCompiler through the
    # shared channel so LDIO's RdfAdapter can pick a parser without the
    # user having to hand-set it on either side.
    assert invoke["properties"]["Request Content-Type"] == "application/n-triples"


def test_two_bridges_get_distinct_ports(catalog_graph):
    parse_extra(catalog_graph, TWO_BRIDGES)
    _, build = compile_pipeline(catalog_graph, "demo_ln:Test")
    flow = _flow_json(build)

    listen_ports = sorted(
        p["properties"]["Listening Port"]
        for p in flow["rootGroup"]["processors"]
        if p["type"] == "org.apache.nifi.processors.standard.ListenHTTP"
    )
    assert listen_ports == [
        "9000",
        "9001",
    ], f"expected two ListenHTTP steps on ports 9000/9001, got {listen_ports}"


# A hand-authored nifi:ListenHTTP with an explicit p-plan:hasInputVar must be
# left untouched by NifiListenHttpConfigCompiler — the trigger's
# FILTER NOT EXISTS gate is what keeps user configs authoritative.
HAND_AUTHORED_LISTEN = PREFIXES + """
demo_ln:Test a tcs:PipelineDefinition .

demo_ln:PollA a tcs:InstancePipelineComponent ;
    prov:specializationOf ldio:HttpInPoller ;
    p-plan:isStepOfPlan demo_ln:Test ;
    p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [
        ldio:url "https://a.example.invalid/api" ;
        ldio:cron "*/10 * * * * *"
    ] ] .

demo_ln:ParseA a tcs:InstancePipelineComponent ;
    prov:specializationOf ldio:JsonToLdAdapter ;
    p-plan:isStepOfPlan demo_ln:Test ;
    p-plan:isPrecededBy demo_ln:PollA ;
    p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [
        ldio:force-content-type true ;
        ldio:context "{}"
    ] ] .

demo_ln:ForwardA a tcs:InstancePipelineComponent ;
    prov:specializationOf ldio:HttpOut ;
    p-plan:isStepOfPlan demo_ln:Test ;
    p-plan:isPrecededBy demo_ln:ParseA ;
    tcs:writesTo demo_ln:ldio_to_nifi ;
    p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [
        ldio:endpoint "http://nifip:7777/custom" ;
        ldio:rdf-writer [ ldio:content-type "application/n-triples" ]
    ] ] .

demo_ln:ReceiveA a tcs:InstancePipelineComponent ;
    prov:specializationOf nifi:ListenHTTP ;
    p-plan:isStepOfPlan demo_ln:Test ;
    nifi:scheduledState "RUNNING" ;
    tcs:readsFrom demo_ln:ldio_to_nifi ;
    tcs:writesTo demo_ln:receive_out ;
    p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [
        nifi:listeningPort "7777" ;
        nifi:basePath "custom"
    ] ] .

demo_ln:SinkA a tcs:InstancePipelineComponent ;
    prov:specializationOf nifi:Funnel ;
    p-plan:isStepOfPlan demo_ln:Test ;
    tcs:readsFrom demo_ln:receive_out .
"""


def test_hand_authored_boundary_steps_are_left_untouched(catalog_graph):
    parse_extra(catalog_graph, HAND_AUTHORED_LISTEN)
    _, build = compile_pipeline(catalog_graph, "demo_ln:Test")
    flow = _flow_json(build)

    listen = next(
        p
        for p in flow["rootGroup"]["processors"]
        if p["type"] == "org.apache.nifi.processors.standard.ListenHTTP"
    )
    assert (
        listen["properties"]["Listening Port"] == "7777"
    ), "hand-authored ListenHTTP config was overwritten by the boundary compiler"
    assert listen["properties"]["Base Path"] == "custom"
