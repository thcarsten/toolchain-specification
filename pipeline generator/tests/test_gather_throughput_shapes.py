"""Tests for ValidationReportCompiler.gather_throughput_shapes(), run
against the real demonstrator pipeline (demo:DishacledPipeline) rather
than a synthetic snippet — pipeline_definition.ttl's own comments
already document which steps intentionally have no inputShape/
outputShape at any tier, which gives a ready-made set of resolved /
unresolved cases to check against."""


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


def test_channel_outputshape_resolves_from_reader_instance_inputshape(
    compiled_demonstrator,
):
    # demo:ThresholdMonitor's own (tier 2) inputShape becomes the
    # outputShape of the channel it reads from.
    _, build = compiled_demonstrator
    channel = (
        build.filter(sub="demo:ThresholdMonitor", pred="tcs:readsFrom")
        .df["obj"]
        .iloc[0]
    )
    assert _shapes_for(build, channel, "tcs:outputShape") == [
        "demo:SosaWaterLevelObservationShape"
    ]


def test_channel_inputshape_falls_back_to_writer_component_shape(compiled_demonstrator):
    # demo:SdsifyMeasurements has no instance-level outputShape of its
    # own — the channel's inputShape must fall back (tier 3) to
    # rdfc:Sdsify's trivial catalog-level outputShape.
    _, build = compiled_demonstrator
    channel = (
        build.filter(sub="demo:ThresholdMonitor", pred="tcs:readsFrom")
        .df["obj"]
        .iloc[0]
    )
    assert len(_shapes_for(build, channel, "tcs:inputShape")) == 1


def test_channel_between_two_instance_shapes_agrees_on_both_sides(
    compiled_demonstrator,
):
    # demo:JsonLdParse's outputShape and demo:SsnSosaMap's inputShape are
    # both demo:WaterLevelPropertyValueShape — the channel between them
    # should resolve to the same shape from either direction.
    _, build = compiled_demonstrator
    channel = (
        build.filter(sub="demo:SsnSosaMap", pred="tcs:readsFrom").df["obj"].iloc[0]
    )
    assert _shapes_for(build, channel, "tcs:inputShape") == [
        "demo:WaterLevelPropertyValueShape"
    ]
    assert _shapes_for(build, channel, "tcs:outputShape") == [
        "demo:WaterLevelPropertyValueShape"
    ]


def test_channel_outputshape_falls_back_to_empty_shape_when_reader_has_none(
    compiled_demonstrator,
):
    # demo:LogMeasurementsMeta specializes rdfc:LogProcessorJs, which has
    # no inputShape/outputShape anywhere in the catalog, so
    # gather_throughput_shapes itself can't resolve the metadata
    # sidechannel's outputShape — fill_missing_shapes gives it the
    # trivial placeholder by the end of the full pipeline instead of
    # leaving it genuinely empty.
    _, build = compiled_demonstrator
    assert _is_empty_shape_placeholder(
        _shapes_for(build, "demo:sdsMeasurementsMeta", "tcs:outputShape")
    )


def test_channel_outputshape_falls_back_to_empty_shape_when_upstream_is_non_rdf(
    compiled_demonstrator,
):
    # demo:ApiPoll -> demo:JsonLdParse: ApiPoll's component
    # (ldio:HttpInPoller) has a trivial tier-3 outputShape, so the
    # channel's inputShape resolves; JsonLdParse deliberately has no
    # inputShape at any tier (documented in pipeline_definition.ttl —
    # its upstream is raw JSON bytes, not RDF), so gather_throughput_shapes
    # itself can't resolve the channel's outputShape — fill_missing_shapes
    # gives it the trivial placeholder by the end of the full pipeline.
    _, build = compiled_demonstrator
    channel = (
        build.filter(sub="demo:JsonLdParse", pred="tcs:readsFrom").df["obj"].iloc[0]
    )
    assert len(_shapes_for(build, channel, "tcs:inputShape")) == 1
    assert _is_empty_shape_placeholder(_shapes_for(build, channel, "tcs:outputShape"))
