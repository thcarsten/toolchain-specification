"""End-to-end: the generated catalog validates and still compiles.

Mirrors what the demo notebook does, so a change to the catalog generator
that breaks the actual pipeline build fails here rather than in a
notebook run.
"""

from pathlib import Path

import pytest

from conftest import CATALOG_FILES, PIPELINE_ID


@pytest.fixture(scope="module")
def catalog_reader(data_dir: Path):
    from rdflib import Graph
    from rdfine import GraphReader

    graph = Graph()
    for name in CATALOG_FILES:
        graph.parse(data_dir / name, publicID="file:///workspace/pipeline/")
    return GraphReader(graph).infer(str(data_dir / "inference_rules.yaml"))


def test_notebook_file_list_matches_this_test(repo_root: Path):
    """Keep the notebook's load list and this test's in sync."""
    import json

    notebook = json.loads((repo_root / "src/demo.ipynb").read_text(encoding="utf-8"))
    source = "".join(notebook["cells"][2]["source"])
    for name in CATALOG_FILES:
        assert f'"{name}"' in source, f"{name} missing from demo.ipynb load list"


def test_catalog_conforms_to_its_application_profile(catalog_reader):
    """The whole merged graph must validate.

    Before generation this failed with two violations: catalog-core.ttl
    listed rdfc:HttpFetch and rdfc:LogProcessorPy as dcat:resource with no
    definition anywhere.
    """
    report = catalog_reader.validate(advanced=True, inference="rdfs")
    if not report.ask("?r sh:conforms true"):
        violations = report.select(
            "?focus ?message",
            "?r a sh:ValidationResult ; sh:focusNode ?focus ; sh:resultMessage ?message .",
        )
        pytest.fail(f"catalog does not conform:\n{violations.to_string(index=False)}")


def test_pipeline_still_compiles(catalog_reader):
    from compilers import PipelineGenerator, ProjectBuilder

    generator = PipelineGenerator(PIPELINE_ID, catalog_reader.graph)
    build = generator.compile()

    ran = {cls.__name__ for cls in generator.compilers}
    assert "RdfcConfigCompiler" in ran
    assert "RdfcDockerFileCompiler" in ran
    assert "DockerComposeCompiler" in ran

    files = ProjectBuilder(build).files
    produced = {
        f"{row['filepath']}/{row['filename']}" for _, row in files.iterrows()
    }
    assert "rdfc/pipeline.ttl" in produced
    assert "rdfc/package.json" in produced
    assert "rdfc/pyproject.toml" in produced
    assert "rdfc/Dockerfile" in produced


def test_emitted_pipeline_imports_only_used_processors(catalog_reader):
    """owl:imports must cover exactly the processors the pipeline uses.

    RdfcConfigCompiler copies imports from components that both require a
    runner and are specialised by a step, so an unused catalog entry (now
    that HttpFetch and LogProcessorPy exist) must not leak in.
    """
    from compilers import PipelineGenerator, ProjectBuilder

    build = PipelineGenerator(PIPELINE_ID, catalog_reader.graph).compile()
    files = ProjectBuilder(build).files
    pipeline_ttl = next(
        row["content"]
        for _, row in files.iterrows()
        if row["filename"] == "pipeline.ttl"
    )
    assert "http-utils-processor-ts" in pipeline_ttl
    # Present in the catalog but unused by this pipeline.
    assert "log-processor-py" not in pipeline_ttl
    assert "rdfc_log_processor" not in pipeline_ttl


def test_generated_package_json_lists_used_packages(catalog_reader):
    import json

    from compilers import PipelineGenerator, ProjectBuilder

    build = PipelineGenerator(PIPELINE_ID, catalog_reader.graph).compile()
    files = ProjectBuilder(build).files
    body = next(
        row["content"]
        for _, row in files.iterrows()
        if row["filename"] == "package.json"
    )
    dependencies = json.loads(body)["dependencies"]
    assert dependencies["@rdfc/sparql-ingest-processor-ts"] == "^2.1.7"
    assert dependencies["@rdfc/js-runner"] == "^3.2.0"
    # Version strings must survive verbatim from the request file.
    assert dependencies["@rdfc/threshold-monitor-processor-ts"] == "^0.0.1-alpha.2"
