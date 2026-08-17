"""Plain helper functions for the pipeline generator's edge-case test suite.

Kept in a separate module (not conftest.py) because pytest's default
import mode collides same-named ``conftest.py`` modules across
directories when helpers are imported by bare name (``from conftest
import ...``) — this bit us once rdfine's own ``src/rdfine/tests/
conftest.py`` was added. Fixtures still live in conftest.py (pytest
auto-discovers those regardless of import mode); only the plain
functions live here.

Two-pillar convention (see EDGE_CASES.md):
- Unsupported edge case -> assert it's caught explicitly, either by a
  SHACL shape (`assert_shacl_violation`) or a compiler-level guard
  (`assert_compile_raises`).
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
    "catalog-nifi.ttl",
    "catalog-rdfc.ttl",
    "catalog-sw.ttl",
]
SHAPES_FILE = "catalog-application-profile-shapes.ttl"
INFERENCE_RULES = "inference_rules.yaml"


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
