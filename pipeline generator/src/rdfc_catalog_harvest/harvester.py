"""Turn catalog requests into snapshot records.

For each request: resolve the package, find the Turtle file inside it
that actually declares the requested component, and freeze that file
plus the surrounding registry facts into the snapshot.

Selecting the file by *content* rather than by name is what lets a
request stay a one-liner. ``@rdfc/sds-processors-ts`` ships ten Turtle
files and ``@rdfc/http-utils-processor-ts`` declares two processors in
one file; in both cases the discriminator is which file contains
``<component> rdfc:{js,py,jvm}ImplementationOf rdfc:Processor``.
"""

from __future__ import annotations

from pathlib import Path

from rdflib import Graph, URIRef
from rdflib.namespace import RDFS

from . import registries, snapshot
from .model import (
    IMPLEMENTATION_PREDICATES,
    CatalogRequest,
    HarvestRecord,
    iri,
)

MODULE_PATH = iri("rdfc:modulePath")

# Not a term in rdflib's RDFS namespace object, but upstream processors
# use it interchangeably with rdfs:comment for the prose description.
RDFS_DESCRIPTION = iri("rdfs:description")


def _declaration(graph: Graph, component: URIRef) -> str | None:
    """Implementation language ``component`` is declared with, if any."""
    for predicate_iri, language in IMPLEMENTATION_PREDICATES.items():
        if (component, URIRef(predicate_iri), None) in graph:
            return language
    return None


def _select_source(
    fetched: registries.FetchedPackage,
    request: CatalogRequest,
) -> tuple[str, Graph, str]:
    """Pick the Turtle file declaring ``request.component``.

    Returns ``(path_inside_package, parsed_graph, language)``.

    Raises:
        LookupError: when no shipped Turtle file declares the component.
            This is the check that catches a renamed or removed
            upstream processor, which is the failure mode hand-editing
            never surfaced.
    """
    component = iri(request.component)

    candidates = fetched.turtle_files
    if request.source_file is not None:
        if request.source_file not in candidates:
            raise LookupError(
                f"{request.component}: tcs:sourceFile {request.source_file!r} is not "
                f"in {request.package} (ships: {', '.join(sorted(candidates)) or 'no .ttl'})"
            )
        candidates = {request.source_file: candidates[request.source_file]}

    for path in sorted(candidates):
        graph = Graph()
        graph.parse(data=candidates[path], format="turtle")
        language = _declaration(graph, component)
        if language is not None:
            return path, graph, language

    raise LookupError(
        f"{request.component} is not declared in {request.package or request.from_path}. "
        f"Searched: {', '.join(sorted(candidates)) or 'no .ttl files'}. "
        "Upstream may have renamed or dropped it."
    )


def harvest_one(request: CatalogRequest, repo_root: Path) -> HarvestRecord:
    """Resolve one request into a :class:`HarvestRecord`."""
    if request.from_path is not None:
        # A local checkout wins over any registry: the point of
        # tcs:fromPath is that this repo's copy is authoritative.
        fetched = registries.fetch_path(request.package, repo_root / request.from_path)
    elif request.supplied_by == ":npm" or request.package.startswith("@"):
        # Scoped names are npm-only by construction.
        fetched = registries.fetch_npm(request.package, request.version)
    elif request.supplied_by == ":pip":
        fetched = registries.fetch_pypi(request.package, request.version)
    else:
        # Unscoped name with no declared manager. Try npm first, then
        # PyPI; whichever answers decides the ecosystem.
        try:
            fetched = registries.fetch_npm(request.package, request.version)
        except Exception:
            fetched = registries.fetch_pypi(request.package, request.version)

    source_file, graph, language = _select_source(fetched, request)
    component = iri(request.component)

    def _literal(*predicates) -> str | None:
        """First non-empty literal among ``predicates``.

        Upstream is not consistent about which predicate carries the
        prose: ``@rdfc/sparql-ingest-processor-ts`` uses ``rdfs:comment``
        while ``@rdfc/http-utils-processor-ts`` uses ``rdfs:description``.
        Both mean the same thing here and both land on the catalog's
        ``rdfs:description``.
        """
        for predicate in predicates:
            value = graph.value(component, predicate)
            if value is not None and str(value).strip():
                return str(value)
        return None

    return HarvestRecord(
        component=request.component,
        source=fetched.source,
        package=fetched.package,
        resolved_version=fetched.resolved_version,
        language=language,
        label=_literal(RDFS.label),
        comment=_literal(RDFS.comment, RDFS_DESCRIPTION),
        landing_page=fetched.landing_page,
        source_file=source_file,
        module_path=_literal(MODULE_PATH),
        turtle=fetched.turtle_files[source_file],
    )


def harvest(
    requests: list[CatalogRequest],
    snapshot_dir: Path,
    repo_root: Path,
) -> tuple[list[HarvestRecord], list[tuple[CatalogRequest, Exception]]]:
    """Harvest every request, collecting failures instead of aborting.

    A single unreachable registry should not stop the other eight
    components from refreshing, so failures come back as data. The CLI
    reports them and exits non-zero.
    """
    records: list[HarvestRecord] = []
    failures: list[tuple[CatalogRequest, Exception]] = []

    for request in requests:
        try:
            record = harvest_one(request, repo_root)
        except Exception as error:  # noqa: BLE001 - reported, not swallowed
            failures.append((request, error))
            continue
        snapshot.write_record(snapshot_dir, record)
        records.append(record)

    return records, failures
