"""Read ``tcs:CatalogRequest`` statements out of a Turtle file.

The request file is the whole hand-written input to catalog generation.
One block per component, in the shape:

.. code-block:: turtle

    rdfc:SPARQLIngest a tcs:CatalogRequest ;
        tcs:package "@rdfc/sparql-ingest-processor-ts" ;
        spdx:versionInfo "^2.1.7" .

Everything else in the emitted catalog entry — label, description,
landing page, runner dependency, package manager, ``owl:imports``, and
the full SHACL config shape — is derived from the package's own
``processors.ttl``. See :mod:`catalog.emitter`.

This module uses ``rdflib`` directly rather than
:class:`rdfine.GraphReader`. GraphReader is the project's tool for
querying and transforming *pipeline* graphs; a request file is a small
declarative config read once at startup, and going through the
DataFrame view would add nothing but indirection.
"""

from __future__ import annotations

from pathlib import Path

from rdflib import Graph, URIRef
from rdflib.namespace import RDF

from .model import PREFIXES, CatalogRequest

TCS = PREFIXES["tcs"]
SPDX = PREFIXES["spdx"]

CATALOG_REQUEST = URIRef(f"{TCS}CatalogRequest")

# request predicate -> CatalogRequest field
_FIELDS: dict[URIRef, str] = {
    URIRef(f"{TCS}package"): "package",
    URIRef(f"{SPDX}versionInfo"): "version",
    URIRef(f"{TCS}fromPath"): "from_path",
    URIRef(f"{SPDX}downloadLocation"): "download_location",
    URIRef(f"{SPDX}suppliedBy"): "supplied_by",
    URIRef(f"{TCS}sourceFile"): "source_file",
}


def _compact(node: URIRef) -> str:
    """Render an IRI in the prefixed form the catalog files use.

    Longest-namespace-first so that ``tm:`` (a sub-path of no other
    namespace here) and the empty prefix cannot shadow a better match.
    """
    text = str(node)
    best_prefix, best_ns = None, ""
    for prefix, namespace in PREFIXES.items():
        if text.startswith(namespace) and len(namespace) > len(best_ns):
            best_prefix, best_ns = prefix, namespace
    if best_prefix is None:
        return f"<{text}>"
    return f"{best_prefix}:{text[len(best_ns):]}"


def load_requests(path: str | Path) -> list[CatalogRequest]:
    """Parse every ``tcs:CatalogRequest`` in ``path``.

    Returns the requests sorted by component IRI, so downstream
    harvesting and emission are order-stable regardless of how rdflib
    happened to hash the input.

    Raises:
        ValueError: if a request names neither ``tcs:package`` nor
            ``tcs:fromPath`` (nothing to resolve), or carries an
            unrecognised predicate — a typo in the request file should
            fail loudly rather than silently drop a field.
    """
    graph = Graph()
    graph.parse(str(path), format="turtle")

    requests: list[CatalogRequest] = []
    for subject in graph.subjects(RDF.type, CATALOG_REQUEST):
        if not isinstance(subject, URIRef):
            raise ValueError(
                "tcs:CatalogRequest must be an IRI naming the component to "
                f"define; found a blank node in {path}."
            )
        values: dict[str, str] = {}
        for predicate, obj in graph.predicate_objects(subject):
            if predicate == RDF.type:
                continue
            field = _FIELDS.get(predicate)
            if field is None:
                raise ValueError(
                    f"unrecognised predicate {_compact(predicate)} on catalog "
                    f"request {_compact(subject)}; expected one of "
                    f"{', '.join(sorted(_compact(p) for p in _FIELDS))}."
                )
            values[field] = str(obj)

        request = CatalogRequest(component=_compact(subject), **values)
        if request.package is None and request.from_path is None:
            raise ValueError(
                f"catalog request {request.component} must declare either "
                "tcs:package (resolve from a registry) or tcs:fromPath "
                "(read from a checked-out directory)."
            )
        requests.append(request)

    return sorted(requests, key=lambda r: r.component)
