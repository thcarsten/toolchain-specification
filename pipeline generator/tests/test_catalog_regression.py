"""Guard the generated catalog against the hand-written one it replaced.

``fixtures/catalog-rdfc-handwritten.ttl`` is the file as it stood before
generation existed. These tests assert the generated catalog says
everything that file said about each component — same runner, same
package, same manager, an ``owl:imports``, a config shape — so the switch
cannot quietly drop a fact the compilers depend on.

Deliberately compared per-fact rather than by graph isomorphism: the
generated file legitimately says *more* (descriptions and landing pages
upstream provides but the hand transcription omitted, and config shapes
for eight components that never had one).
"""

from pathlib import Path

import pytest
from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDF, RDFS

DCAT = "http://www.w3.org/ns/dcat#"
DCT = "http://purl.org/dc/terms/"
RDFC = "https://w3id.org/rdf-connect#"
SPDX = "http://spdx.org/rdf/terms#"
TCS = "https://w3id.org/toolchain#"

REQUIRES = URIRef(f"{DCT}requires")
SPDX_PACKAGE = URIRef(f"{SPDX}Package")
SPDX_NAME = URIRef(f"{SPDX}name")
SPDX_VERSION = URIRef(f"{SPDX}versionInfo")
SPDX_SUPPLIED_BY = URIRef(f"{SPDX}suppliedBy")
SPDX_DOWNLOAD = URIRef(f"{SPDX}downloadLocation")
QUALIFIED_RELATION = URIRef(f"{DCAT}qualifiedRelation")
PIPELINE_COMPONENT = URIRef(f"{TCS}PipelineComponent")

# Components the hand-written file defined that are now generated. The
# rest of that file (orchestrator, runners, unbuilt stubs,
# proc:JsonLdToNQuads) moved to catalog-rdfc-manual.ttl instead.
GENERATED_COMPONENTS = [
    f"{RDFC}SPARQLIngest",
    f"{RDFC}HttpServer",
    f"{RDFC}HttpOut",
    f"{RDFC}Sdsify",
    f"{RDFC}SkolemizationProcessor",
    f"{RDFC}LogProcessorJs",
    "https://w3id.org/rdf-connect/threshold-monitor#ThresholdMonitorJs",
]

# Moved to the hand-maintained file, not generated.
MANUAL_COMPONENTS = [
    f"{RDFC}Orchestrator",
    f"{RDFC}NodeRunner",
    f"{RDFC}PyRunner",
    f"{RDFC}HttpIn",
    f"{RDFC}thresholdMonitoringProcessor",
    "http://dishacled.example.org/processors#JsonLdToNQuads",
]


@pytest.fixture(scope="module")
def handwritten(repo_root: Path) -> Graph:
    graph = Graph()
    graph.parse(
        repo_root / "tests/fixtures/catalog-rdfc-handwritten.ttl",
        publicID="file:///workspace/pipeline/",
    )
    return graph


@pytest.fixture(scope="module")
def new(generated: str) -> Graph:
    graph = Graph()
    graph.parse(data=generated, publicID="file:///workspace/pipeline/", format="turtle")
    return graph


@pytest.fixture(scope="module")
def manual(data_dir: Path) -> Graph:
    graph = Graph()
    graph.parse(
        data_dir / "catalog-rdfc-manual.ttl", publicID="file:///workspace/pipeline/"
    )
    return graph


def _packages(graph: Graph, component: URIRef) -> dict[str, dict]:
    """``spdx:name`` -> its facts, for every package the component requires."""
    result = {}
    for target in graph.objects(component, REQUIRES):
        if (target, RDF.type, SPDX_PACKAGE) not in graph:
            continue
        name = str(graph.value(target, SPDX_NAME))
        result[name] = {
            "version": graph.value(target, SPDX_VERSION),
            "manager": graph.value(target, SPDX_SUPPLIED_BY),
            "download": graph.value(target, SPDX_DOWNLOAD),
        }
    return result


@pytest.mark.parametrize("iri", GENERATED_COMPONENTS)
def test_component_still_defined(new: Graph, iri: str):
    component = URIRef(iri)
    assert (component, RDF.type, PIPELINE_COMPONENT) in new
    assert (component, RDF.type, URIRef(f"{DCAT}Resource")) in new


@pytest.mark.parametrize("iri", GENERATED_COMPONENTS)
def test_runner_dependency_unchanged(handwritten: Graph, new: Graph, iri: str):
    """The runner link drives RdfcConfigCompiler's env grouping."""
    component = URIRef(iri)
    runners = {
        str(o)
        for o in handwritten.objects(component, REQUIRES)
        if str(o).endswith(("NodeRunner", "PyRunner"))
    }
    assert runners, f"fixture has no runner for {iri}"
    assert runners <= {str(o) for o in new.objects(component, REQUIRES)}


@pytest.mark.parametrize("iri", GENERATED_COMPONENTS)
def test_package_facts_unchanged(handwritten: Graph, new: Graph, iri: str):
    """Name, version and manager feed package.json / pyproject.toml."""
    component = URIRef(iri)
    old_packages = _packages(handwritten, component)
    new_packages = _packages(new, component)

    for name, old in old_packages.items():
        assert name in new_packages, f"{iri} lost package {name}"
        got = new_packages[name]
        assert got["manager"] == old["manager"], f"{iri}/{name} manager changed"
        assert got["version"] == old["version"], f"{iri}/{name} version changed"
        assert got["download"] == old["download"], f"{iri}/{name} download changed"


@pytest.mark.parametrize("iri", GENERATED_COMPONENTS)
def test_owl_imports_present_and_plausible(new: Graph, iri: str):
    """Required by tcs:RdfcProcessorShape; nothing validates the value."""
    component = URIRef(iri)
    imports = [str(o) for o in new.objects(component, OWL.imports)]
    assert len(imports) == 1, f"{iri} should have exactly one owl:imports"
    target = imports[0]
    assert target.endswith(".ttl")
    assert "/node_modules/" in target or "/site-packages/" in target


@pytest.mark.parametrize("iri", GENERATED_COMPONENTS)
def test_label_preserved(handwritten: Graph, new: Graph, iri: str):
    """Every component the fixture labelled still has one."""
    component = URIRef(iri)
    if handwritten.value(component, RDFS.label) is None:
        pytest.skip("fixture had no label")
    assert new.value(component, RDFS.label) is not None


def test_every_generated_component_has_a_config_shape(new: Graph):
    """The hand-written file had one; all of them now do.

    This is the gap generation closes: only rdfc:SPARQLIngest carried a
    configShape before, so the other components' step configs were
    unvalidated.
    """
    for iri in GENERATED_COMPONENTS:
        assert (URIRef(iri), QUALIFIED_RELATION, None) in new, iri


def test_manual_components_moved_not_lost(handwritten: Graph, manual: Graph):
    """Nothing the fixture defined vanished — it is in the manual file."""
    for iri in MANUAL_COMPONENTS:
        component = URIRef(iri)
        assert (component, RDF.type, PIPELINE_COMPONENT) in handwritten, iri
        assert (component, RDF.type, PIPELINE_COMPONENT) in manual, iri


def test_orchestrator_configs_survived(manual: Graph):
    """Both Config literals are required by the RDFC compilers."""
    orchestrator = URIRef(f"{RDFC}Orchestrator")
    configs = set(manual.objects(orchestrator, URIRef(f"{TCS}config")))
    types = {str(t) for c in configs for t in manual.objects(c, RDF.type)}
    assert f"{TCS}DockerComposeConfig" in types
    assert f"{TCS}DockerImageConfig" in types
    for config in configs:
        assert manual.value(config, URIRef(f"{DCT}format")) is not None
        assert manual.value(config, URIRef(f"{TCS}literal")) is not None


def test_dangling_catalog_entries_are_gone(new: Graph, manual: Graph):
    """The two entries that were failing tcs:CatalogShape.

    ``rdfc:HttpFetch`` and ``rdfc:LogProcessorPy`` were listed as
    dcat:resource in catalog-core.ttl with no definition anywhere. Both
    are now generated with real definitions, so membership and definition
    cannot disagree.
    """
    listed = {
        str(o)
        for graph in (new, manual)
        for o in graph.objects(
            URIRef("http://example.org/example/DishacledCatalog"),
            URIRef(f"{DCAT}resource"),
        )
    }
    for iri in (f"{RDFC}HttpFetch", f"{RDFC}LogProcessorPy"):
        assert iri in listed, f"{iri} should be listed"
        assert (URIRef(iri), RDF.type, PIPELINE_COMPONENT) in new, (
            f"{iri} listed but not defined"
        )


def test_catalog_membership_matches_definitions(new: Graph, manual: Graph):
    """No entry may be listed without a definition, or defined unlisted."""
    catalog = URIRef("http://example.org/example/DishacledCatalog")
    for graph in (new, manual):
        listed = set(graph.objects(catalog, URIRef(f"{DCAT}resource")))
        defined = set(graph.subjects(RDF.type, PIPELINE_COMPONENT))
        assert listed == defined, (
            f"listed-but-undefined: {sorted(str(x) for x in listed - defined)}; "
            f"defined-but-unlisted: {sorted(str(x) for x in defined - listed)}"
        )
