"""Tests for ValidationReportCompiler.normalize_passthrough_shapes(), run
against the real demonstrator pipeline (demo:DishacledPipeline).

The LDIO -> RDFC hop (demo:LdioForward -> demo:HttpIngest ->
demo:JsonLdToRdf) is a three-passthrough chain, so it exercises the
dataflow-order dependency directly: demo:HttpIngest's regularized
outputShape can only be resolved once demo:LdioForward's own
regularization has already propagated onto their shared channel."""

from testing_helpers import compile_pipeline


def _shapes_for(build, node_id: str, role: str) -> list[str]:
    df = build.select(
        "?shape",
        f"""
        {node_id} dcat:qualifiedRelation ?rel .
        ?rel dcat:hadRole {role} ; dct:relation ?shape .
        """,
    )
    return df["shape"].to_list()


def _is_empty_shape_placeholder(shapes: list[str]) -> bool:
    """True if `shapes` is exactly the trivial placeholder
    fill_missing_shapes() mints for a channel no producer or consumer
    ever supplied a real shape for."""
    return len(shapes) == 1 and shapes[0].startswith(":emptyshape_")


def test_passthrough_instance_gets_inputshape_copy_of_its_own_passthroughshape(
    demonstrator_graph,
):
    _, build = compile_pipeline(demonstrator_graph, "demo:DishacledPipeline")
    assert _shapes_for(build, "demo:LdioForward", "tcs:inputShape") == [
        "demo:SosaWaterLevelObservationShape"
    ]


def test_channel_resolves_on_both_sides_via_passthrough_regularization(
    demonstrator_graph,
):
    # demo:LdioForward -> demo:HttpIngest is the cross-framework HTTP
    # hop; both ends only carry a passthroughShape. LdioForward's
    # upstream channel (written by demo:SsnSosaMap, a real transform)
    # already has a resolved inputShape, so LdioForward regularizes
    # immediately and propagates its outputShape onto this channel as
    # its inputShape; HttpIngest's own regularized inputShape (a copy
    # of its passthroughShape) propagates back onto this same channel
    # as its outputShape — resolving both sides.
    _, build = compile_pipeline(demonstrator_graph, "demo:DishacledPipeline")
    channel = (
        build.filter(sub="demo:HttpIngest", pred="tcs:readsFrom").df["obj"].iloc[0]
    )
    assert _shapes_for(build, channel, "tcs:inputShape") == [
        "demo:SosaWaterLevelObservationShape"
    ]
    assert _shapes_for(build, channel, "tcs:outputShape") == [
        "demo:SosaWaterLevelObservationShape"
    ]


def test_passthrough_chain_propagates_through_a_second_hop(demonstrator_graph):
    # demo:HttpIngest -> demo:JsonLdToRdf: JsonLdToRdf's regularized
    # outputShape depends on demo:HttpIngest having already regularized
    # in this same pass — only correct if the two are processed in
    # dataflow order, not an arbitrary one.
    _, build = compile_pipeline(demonstrator_graph, "demo:DishacledPipeline")
    channel = (
        build.filter(sub="demo:JsonLdToRdf", pred="tcs:readsFrom").df["obj"].iloc[0]
    )
    assert _shapes_for(build, channel, "tcs:inputShape") == [
        "demo:SosaWaterLevelObservationShape"
    ]
    assert _shapes_for(build, channel, "tcs:outputShape") == [
        "demo:SosaWaterLevelObservationShape"
    ]


def test_passthrough_outputshape_falls_back_to_empty_shape_downstream_of_a_shapeless_producer(
    demonstrator_graph,
):
    # demo:ThresholdMonitor -> demo:SkolemizeViolations: ThresholdMonitor
    # has no outputShape at any tier (tm:ThresholdMonitorJs declares
    # none), so even though SkolemizeViolations is a passthrough (tier-3
    # fallback to rdfc:SkolemizationProcessor's trivial shape) and gets
    # its own inputShape regularized, it has nothing to resolve its own
    # outputShape from — normalize_passthrough_shapes must not invent
    # one, and fill_missing_shapes only fills tcs:Channels, never
    # instances, so this stays empty even at the end of the full
    # pipeline. The channel it reads from does get the placeholder
    # for its inputShape (the side ThresholdMonitor never supplied).
    _, build = compile_pipeline(demonstrator_graph, "demo:DishacledPipeline")
    assert _shapes_for(build, "demo:SkolemizeViolations", "tcs:inputShape") != []
    assert _shapes_for(build, "demo:SkolemizeViolations", "tcs:outputShape") == []

    channel = (
        build.filter(sub="demo:SkolemizeViolations", pred="tcs:readsFrom")
        .df["obj"]
        .iloc[0]
    )
    # The read channel's outputShape comes from SkolemizeViolations'
    # regularized inputShape (the bidirectional propagation), so it
    # resolves to a real shape even though the channel's inputShape
    # (from ThresholdMonitor's side) only ever gets the placeholder.
    assert _shapes_for(build, channel, "tcs:outputShape") != []
    assert not _is_empty_shape_placeholder(
        _shapes_for(build, channel, "tcs:outputShape")
    )
    assert _is_empty_shape_placeholder(_shapes_for(build, channel, "tcs:inputShape"))
