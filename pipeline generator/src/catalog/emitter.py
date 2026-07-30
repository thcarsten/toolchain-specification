"""Render the generated catalog file from requests + harvest snapshot.

Pure function of its two inputs — no network, no clock, no environment.
Running it twice on the same snapshot produces identical bytes, which is
what makes the output safe to commit and review.

What each emitted component block gets, and where it comes from:

==============================  ==========================================
``rdfs:label``                  upstream ``rdfs:label``
``rdfs:description``            upstream ``rdfs:comment`` / ``rdfs:description``
``dcat:landingPage``            registry repository URL
``dct:requires`` <runner>       upstream ``rdfc:{js,py}ImplementationOf``
``dct:requires`` [spdx:Package] request package + version
``owl:imports``                 package layout + harvested file path
``dcat:qualifiedRelation``      upstream SHACL shape (see :mod:`catalog.shapes`)
==============================  ==========================================

Only ``spdx:versionInfo`` is not derivable: it is a policy choice, so it
comes from the request file.
"""

from __future__ import annotations

from pathlib import Path

from rdflib import Graph, URIRef

from . import shapes, snapshot
from .model import PREFIXES, CatalogRequest, HarvestRecord
from .turtle import INDENT, banner, inline_bnode, statement, wrap

# Python minor version whose site-packages the generated owl:imports
# point into. MUST match the `FROM python:X.Y-slim` line in the
# rdfc:Orchestrator tcs:DockerImageConfig (catalog-rdfc-manual.ttl) and
# the `requires-python` in RdfcDockerFileCompiler._PYPROJECT_TEMPLATE.
# Kept here because this module is what bakes it into IRIs.
PYTHON_VERSION = "3.13"

# Prefix of the base IRI every relative import resolves against. Must
# equal GraphReader._basepath and the container WORKDIR; the `../../../`
# in the Python import path is only correct because this path is exactly
# two segments deep.
CONTAINER_WORKDIR = "/workspace/pipeline/"

_DO_NOT_EDIT = """\
#################################################################
# GENERATED FILE - DO NOT EDIT
#
# Produced by `python -m catalog generate` from:
#   - data/catalog-rdfc-requests.ttl   (hand-written: which package)
#   - data/harvest/                    (frozen upstream definitions)
#
# To change a version or add a component, edit the request file and
# re-run `python -m catalog harvest && python -m catalog generate`.
# To change the orchestrator, the runners, or a component whose source
# is not resolvable, edit data/catalog-rdfc-manual.ttl instead.
#
# Every triple below is derived. The only hand-authored values that
# survive into this file are spdx:versionInfo and spdx:downloadLocation,
# which are policy, not facts about the package.
#################################################################
"""


def _prefix_header() -> str:
    lines = [banner("Prefixes"), ""]
    for prefix, namespace in sorted(PREFIXES.items()):
        lines.append(f"@prefix {prefix}: <{namespace}> .")
    return "\n".join(lines) + "\n"


def owl_imports_path(record: HarvestRecord, request: CatalogRequest) -> str:
    """Relative IRI the runner uses to locate the processor definition.

    The two ecosystems install to different places, and both paths are
    resolved at parse time against ``CONTAINER_WORKDIR``:

    - npm packages land under the container WORKDIR, so the path is
      ``./node_modules/<package>/<file>``.
    - Python packages land in the interpreter's site-packages, reached by
      climbing out of the WORKDIR — which is why the number of ``..``
      segments is coupled to ``CONTAINER_WORKDIR``'s depth.
    """
    if record.language == "python":
        depth = len([p for p in CONTAINER_WORKDIR.strip("/").split("/") if p])
        climb = "../" * (depth + 1)
        return (
            f"{climb}usr/local/lib/python{PYTHON_VERSION}"
            f"/site-packages/{record.source_file}"
        )
    package = record.package or request.package
    return f"./node_modules/{package}/{record.source_file}"


def _package_node(record: HarvestRecord, request: CatalogRequest) -> str:
    """Render the ``spdx:Package`` blank node for a component's dependency.

    Local packages carry ``spdx:downloadLocation`` (a container path pip
    or npm installs from) instead of a version; published ones carry
    ``spdx:versionInfo``. ``RdfcDockerFileCompiler`` branches on exactly
    that distinction.
    """
    manager = request.supplied_by or record.default_manager
    pairs = [("a", "spdx:Package"), ("spdx:name", f'"{record.package}"')]
    if request.download_location:
        pairs.append(("spdx:downloadLocation", f'"{request.download_location}"'))
    elif request.version:
        pairs.append(("spdx:versionInfo", f'"{request.version}"'))
    pairs.append(("spdx:suppliedBy", manager))
    return inline_bnode(pairs, INDENT)


def _component_block(
    record: HarvestRecord,
    request: CatalogRequest,
    shape: shapes.TranslatedShape | None,
) -> str:
    """Render one component's full catalog entry."""
    pairs: list[tuple[str, str]] = [("a", "tcs:PipelineComponent, dcat:Resource")]

    if record.label:
        pairs.append(("rdfs:label", f'"{record.label}"'))
    if record.comment:
        pairs.append(("rdfs:description", f'"{record.comment}"'))
    if record.landing_page:
        pairs.append(("dcat:landingPage", f'"{record.landing_page}"'))

    pairs.append(("dct:requires", record.runner))
    pairs.append(("dct:requires", _package_node(record, request)))
    pairs.append(("owl:imports", f"<{owl_imports_path(record, request)}>"))

    if shape is not None:
        relation = inline_bnode(
            [
                ("a", "dcat:Relationship"),
                ("dcat:hadRole", "tcs:configShape"),
                ("dct:relation", shape.iri),
            ],
            INDENT,
        )
        pairs.append(("dcat:qualifiedRelation", relation))

    provenance = [
        f"# {record.component}",
        f"#   source:  {record.source}"
        + (f" {record.package}@{record.resolved_version}" if record.resolved_version else ""),
        f"#   defined: {record.source_file}",
    ]
    return "\n".join(provenance) + "\n" + statement(record.component, pairs)


def _shape_block(shape: shapes.TranslatedShape) -> str:
    """Render a translated shape and, after it, its nested shapes."""
    pairs: list[tuple[str, str]] = [("a", "sh:NodeShape")]
    pairs.extend(shape.target)
    for property_pairs in shape.properties:
        pairs.append(("sh:property", inline_bnode(property_pairs, INDENT)))

    wrapped = "\n".join(f"# {line}" for line in wrap(shape.comment))
    block = wrapped + "\n" + statement(shape.iri, pairs)
    for nested in shape.nested:
        block += "\n" + _shape_block(nested)
    return block


def generate(
    requests: list[CatalogRequest],
    snapshot_dir: Path,
) -> str:
    """Render the complete generated catalog file.

    Raises:
        FileNotFoundError: if a request has no harvest record.
        NotImplementedError: if an upstream shape uses a SHACL construct
            the translator does not handle.
    """
    sections: list[str] = [_DO_NOT_EDIT, _prefix_header()]

    component_blocks: list[str] = []
    shape_blocks: list[str] = []
    members: list[str] = []

    # Sort here rather than trusting the caller: the output is committed,
    # so byte-stability must not depend on how the request file happened
    # to be ordered or on rdflib's subject iteration.
    for request in sorted(requests, key=lambda r: r.component):
        record = snapshot.read_record(snapshot_dir, request.component)
        upstream = Graph()
        upstream.parse(data=record.turtle, format="turtle")

        component = URIRef(_expand(request.component))
        shape = shapes.translate(upstream, component)

        component_blocks.append(_component_block(record, request, shape))
        if shape is not None:
            shape_blocks.append(_shape_block(shape))
        members.append(request.component)

    sections.append(
        banner(
            "RDF-Connect components",
            "One block per tcs:CatalogRequest. The comment above each block "
            "records which package and file it was derived from.",
        )
        + "\n\n"
        + "\n\n".join(component_blocks)
    )

    sections.append(
        banner(
            "Catalog membership",
            "Emitted here rather than in catalog-core.ttl so the dcat:resource "
            "list cannot drift from the component definitions. RDF is additive: "
            "the other framework files contribute their own entries to the same "
            "catalog node.",
        )
        + "\n\n"
        + statement(
            ":DishacledCatalog",
            [("a", "tcs:Catalog, dcat:Catalog")]
            + [("dcat:resource", member) for member in members],
        )
    )

    sections.append(
        banner(
            "Component-scoped SHACL config shapes",
            "Translated from each package's own shape. Two systematic "
            "rewrites are applied - Reader/Writer collapse to tcs:Channel, and "
            "nested class constraints become sh:node references targeted by "
            "sh:targetObjectsOf. See catalog/shapes.py for why.",
        )
        + "\n\n"
        + "\n".join(shape_blocks)
    )

    return "\n\n".join(section.rstrip() for section in sections) + "\n"


def _expand(compact_iri: str) -> str:
    prefix, _, local = compact_iri.partition(":")
    namespace = PREFIXES.get(prefix)
    if namespace is None:
        raise ValueError(f"unknown prefix in {compact_iri!r}")
    return f"{namespace}{local}"
