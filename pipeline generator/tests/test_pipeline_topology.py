"""Cross-check between a step's channel config and its topology annotation.

A step states its wiring twice — framework-specifically inside
`tcs:embedded` (`rdfc:reader demo:x`) and framework-neutrally as
`tcs:readsFrom` / `tcs:writesTo`. The compilers read the first, the
application-profile shapes read the second, and until now nothing
connected them, so the pair could disagree in silence.

`inference_rules.yaml` derives `tcs:derivedReadsFrom` /
`tcs:derivedWritesTo` from the config plus the generated config shapes;
`tcs:StepChannelWiringShape` requires that everything derived was also
declared. These tests pin both halves, and the one-directional nature of
the check — plenty of declared edges are legitimately underivable.
"""

from pathlib import Path

import pytest

from conftest import CATALOG_FILES, PIPELINE_ID


DERIVED = """
{ ?step tcs:derivedReadsFrom ?channel . BIND('read' AS ?dir) }
UNION
{ ?step tcs:derivedWritesTo ?channel . BIND('write' AS ?dir) }
"""

DECLARED = """
{ ?step tcs:readsFrom ?channel . BIND('read' AS ?dir) }
UNION
{ ?step tcs:writesTo ?channel . BIND('write' AS ?dir) }
"""

# A step whose config wires demo:sdsMeasurements but whose annotation
# claims demo:sdsViolations — the disagreement the shape must catch.
MISWIRED = """
@prefix demo: <http://example.org/example/demonstrator/> .
@prefix rdfc: <https://w3id.org/rdf-connect#> .
@prefix p-plan: <http://purl.org/net/p-plan#> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix tcs: <https://w3id.org/toolchain#> .
demo:MiswiredLog a tcs:InstancePipelineComponent ;
  prov:specializationOf rdfc:LogProcessorJs ;
  p-plan:isStepOfPlan demo:DishacledPipeline ;
  p-plan:hasInputVar [ a tcs:PipelineConfig ; tcs:embedded [
      rdfc:reader demo:sdsMeasurements ] ] ;
  tcs:readsFrom demo:sdsViolations .
"""


def _load(data_dir: Path, extra: str | None = None):
    from rdflib import Graph
    from rdfine import GraphReader

    graph = Graph()
    for name in CATALOG_FILES:
        graph.parse(data_dir / name, publicID="file:///workspace/pipeline/")
    if extra:
        graph.parse(data=extra, format="turtle")
    return GraphReader(graph).infer(str(data_dir / "inference_rules.yaml"))


@pytest.fixture(scope="module")
def reader(data_dir: Path):
    return _load(data_dir)


def _edges(reader, pattern: str) -> set[tuple[str, str, str]]:
    rows = reader.select("?step ?dir ?channel", pattern)
    return {(r.step, r.dir, r.channel) for r in rows.itertuples()}


def test_derivation_produces_edges(reader):
    """If this is empty the rules silently stopped matching."""
    assert len(_edges(reader, DERIVED)) == 14


def test_every_derived_edge_was_declared(reader):
    """The invariant the shape enforces, asserted directly.

    Zero derived-but-undeclared edges is what makes the check safe to
    turn on: it never contradicts a correctly authored pipeline.
    """
    assert _edges(reader, DERIVED) <= _edges(reader, DECLARED)


def test_check_is_one_directional(reader):
    """Declared-but-underivable edges are expected, not errors.

    LDIO steps have no channel parameters (topology lives in slot
    ordering), the LDIO->RDFC boundary channel has no RDFC-side config
    property, and hand-maintained components have no config shape.
    """
    underivable = _edges(reader, DECLARED) - _edges(reader, DERIVED)
    assert underivable, "expected some declared edges to be underivable"


def test_ldio_steps_yield_no_derived_edges(reader):
    """LDIO expresses topology by ordering, so nothing is derivable."""
    ldio = set(
        reader.select(
            "?step",
            "?step prov:specializationOf ?c . "
            "?c dct:requires ldio:LinkedDataInteractionsOrchestrator .",
        )["step"]
    )
    assert ldio, "fixture should contain LDIO steps"
    derived_steps = {step for step, _, _ in _edges(reader, DERIVED)}
    assert ldio & derived_steps == set()


def test_non_channel_upstream_class_is_not_mistaken_for_a_channel(reader):
    """`tcs:upstreamClass` is overloaded; the rules must not be fooled.

    On a channel property it records the direction (rdfc:Reader/Writer);
    on `tm:path` it records a translated foreign class (rdfl:PathLens).
    Keying on it without also requiring `sh:class tcs:Channel` derives
    `sosa:hasSimpleResult` as a written channel.
    """
    channels = {channel for _, _, channel in _edges(reader, DERIVED)}
    assert "sosa:hasSimpleResult" not in channels
    assert not any("hasSimpleResult" in c for c in channels)


def test_real_pipeline_conforms(reader):
    report = reader.validate(advanced=True, inference="rdfs")
    if not report.ask("?r sh:conforms true"):
        messages = report.select(
            "?message", "?r a sh:ValidationResult ; sh:resultMessage ?message ."
        )
        pytest.fail("\n".join(str(m) for m in messages["message"]))


def test_miswired_step_is_rejected(data_dir: Path):
    reader = _load(data_dir, MISWIRED)
    report = reader.validate(advanced=True, inference="rdfs")
    assert not report.ask("?r sh:conforms true")
    messages = " ".join(
        str(m)
        for m in report.select(
            "?message", "?r a sh:ValidationResult ; sh:resultMessage ?message ."
        )["message"]
    )
    assert "MiswiredLog" in messages
    assert "sdsMeasurements" in messages
    assert "disagree" in messages


def test_derived_predicates_do_not_leak_into_emitted_files(reader):
    """They are a validation aid, not part of any generated artifact."""
    from compilers import PipelineGenerator, ProjectBuilder

    build = PipelineGenerator("demo:DishacledPipeline", reader.graph).compile()
    files = ProjectBuilder(build).files
    for _, row in files.iterrows():
        assert "derivedReadsFrom" not in str(row["content"])
        assert "derivedWritesTo" not in str(row["content"])
