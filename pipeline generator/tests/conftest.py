"""Shared fixtures for the pipeline generator's edge-case test suite.

Two-pillar convention (see tests/EDGE_CASES.md for the checklist):
- Unsupported edge case -> assert it's caught explicitly, either by a
  SHACL shape (`assert_shacl_violation`) or a compiler-level guard
  (`assert_compile_raises`) — some edge cases are enforced one way,
  some the other; see EDGE_CASES.md for which.
- Supported edge case -> compile it for real and assert the resulting
  PipelineBuild graph looks exactly as expected (`compile_pipeline`).
"""

from pathlib import Path

import pytest
from rdflib import Graph
from rdfine import GraphReader

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
CATALOG_FILES = [
    "catalog-core.ttl",
    "catalog-ldio.ttl",
    "catalog-rdfc.ttl",
    "catalog-sw.ttl",
]
SHAPES_FILE = "catalog-application-profile-shapes.ttl"
INFERENCE_RULES = "inference_rules.yaml"


@pytest.fixture
def catalog_graph() -> Graph:
    """The four framework catalog files only — no shapes, no pipeline
    definition. Edge-case tests parse their own tiny synthetic pipeline
    into this via `parse_extra`."""
    g = Graph()
    for filename in CATALOG_FILES:
        g.parse(str(DATA_DIR / filename), publicID="file:///workspace/pipeline/")
    return g


@pytest.fixture
def catalog_with_shapes(catalog_graph: Graph) -> Graph:
    """Catalog + the application-profile SHACL shapes, for violation tests."""
    catalog_graph.parse(
        str(DATA_DIR / SHAPES_FILE), publicID="file:///workspace/pipeline/"
    )
    return catalog_graph


def parse_extra(graph: Graph, ttl: str) -> Graph:
    """Parse an edge case's synthetic Turtle snippet into `graph` in place."""
    graph.parse(data=ttl, format="turtle", publicID="file:///workspace/pipeline/")
    return graph


def load_reader(graph: Graph) -> GraphReader:
    """Wrap `graph` and run RDFS/channel inference — the same prep step
    ``PipelineGenerator`` expects. Public so tests that need to invoke
    ``PipelineGenerator`` directly (e.g. to assert on the exception
    type) don't have to duplicate it."""
    return GraphReader(graph).infer(str(DATA_DIR / INFERENCE_RULES))


def assert_shacl_violation(graph: Graph, *, message_contains: str) -> None:
    """Pillar 1a — assert some SHACL violation mentions `message_contains`
    (case-insensitive substring match on `sh:resultMessage`)."""
    report = load_reader(graph).validate(advanced=True, inference="rdfs")
    violations = report.select(
        "?focus ?message",
        "?r a sh:ValidationResult ; sh:focusNode ?focus ; sh:resultMessage ?message .",
    )
    matches = violations[
        violations["message"].str.contains(message_contains, case=False, na=False)
    ]
    assert not matches.empty, (
        f"Expected a SHACL violation mentioning {message_contains!r}, "
        f"got: {violations['message'].tolist()}"
    )


def assert_compile_raises(
    graph: Graph,
    pipeline_id: str,
    *,
    match: str,
    exc_type: type[Exception] = ValueError,
) -> None:
    """Pillar 1b — some edge cases are caught by a compiler-level guard
    (a Python exception) instead of a SHACL shape; assert that."""
    from compilers import PipelineGenerator

    with pytest.raises(exc_type, match=match):
        PipelineGenerator(pipeline_id, load_reader(graph).graph).compile()


def compile_pipeline(graph: Graph, pipeline_id: str):
    """Pillar 2 — run the real generator end-to-end; returns
    (generator, GraphReader-over-build-graph) for the test to inspect."""
    from compilers import PipelineGenerator

    reader = load_reader(graph)
    gen = PipelineGenerator(pipeline_id, reader.graph)
    build_graph = gen.compile()
    return gen, GraphReader(build_graph)


def pipeline_ttl_content(build: GraphReader) -> str:
    """Return the emitted rdfc/pipeline.ttl body from a compiled build."""
    df = build.filter(pred="tcs:filename", obj="pipeline.ttl").df
    file_node = df["sub"].iloc[0]
    content_df = build.filter(sub=file_node, pred="tcs:literal").df
    return str(content_df["obj"].iloc[0])
