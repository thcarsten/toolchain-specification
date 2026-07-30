"""Data model shared by the harvester and the emitter.

Two record types cross the boundary between the two halves of this
package:

- :class:`CatalogRequest` — what the catalog *author* writes. One
  statement per component, read from ``data/catalog-rdfc-requests.ttl``.
- :class:`HarvestRecord` — what the *network* said, frozen into
  ``data/harvest/``. The emitter reads only these, never the network,
  so ``catalog generate`` is deterministic and offline.

Keeping them apart is what makes the split work: a request is a
promise about which package to look at, a harvest record is the
answer, and the generated catalog is a pure function of the pair.
"""

from __future__ import annotations

from dataclasses import dataclass

# Namespace prefixes bound in every file this package emits. Kept here
# rather than in the emitter so ``requests.py`` can parse against the
# same set. Mirrors the declarations at the top of the hand-written
# catalog files.
PREFIXES: dict[str, str] = {
    "": "http://example.org/example/",
    "dcat": "http://www.w3.org/ns/dcat#",
    "dct": "http://purl.org/dc/terms/",
    "owl": "http://www.w3.org/2002/07/owl#",
    "p-plan": "http://purl.org/net/p-plan#",
    "proc": "http://dishacled.example.org/processors#",
    "prov": "http://www.w3.org/ns/prov#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfc": "https://w3id.org/rdf-connect#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "sh": "http://www.w3.org/ns/shacl#",
    "spdx": "http://spdx.org/rdf/terms#",
    "tcs": "https://w3id.org/toolchain#",
    "tm": "https://w3id.org/rdf-connect/threshold-monitor#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}

# RDF-Connect declares a processor's implementation language by the
# predicate linking it to ``rdfc:Processor``. That single predicate is
# what lets the emitter derive both the runner dependency and the
# package manager — the two things the request file therefore does not
# have to state.
IMPLEMENTATION_PREDICATES: dict[str, str] = {
    "https://w3id.org/rdf-connect#jsImplementationOf": "js",
    "https://w3id.org/rdf-connect#pyImplementationOf": "python",
    # Note the spelling: upstream uses `java`, not `jvm`, even though the
    # runner is `rdfc:JvmRunner` (see rdf-connect/rml-processor-jvm's
    # index.ttl). Verified against the published definition, not inferred
    # from the runner name.
    "https://w3id.org/rdf-connect#javaImplementationOf": "jvm",
}

# language -> (runner component, package manager)
#
# Only "js" and "python" are exercised end to end. The "jvm" row is
# reachable by the harvester — it will detect the language and pick the
# right runner — but three things downstream are not built yet:
# `registries` has no Maven fetcher, `emitter.owl_imports_path` has no
# jvm branch (a jar-based runner does not locate its definition by
# filesystem path the way node_modules/site-packages do), and
# `RdfcDockerFileCompiler` renders only pyproject.toml and package.json.
#
# The `:gradle` manager below is therefore a placeholder. It is also the
# safe kind of placeholder: `tcs:RdfcPackageManagerShape` constrains
# `spdx:suppliedBy` to `sh:in ( :pip :npm )`, so a jvm package would fail
# validation with a message naming the compiler rather than being
# silently dropped from both dependency files. Adding a language means
# updating that shape too.
LANGUAGE_RUNTIME: dict[str, tuple[str, str]] = {
    "js": ("rdfc:NodeRunner", ":npm"),
    "python": ("rdfc:PyRunner", ":pip"),
    "jvm": ("rdfc:JvmRunner", ":gradle"),
}


@dataclass(frozen=True)
class CatalogRequest:
    """One line of author intent: "this component, from that package".

    Attributes:
        component: Compact IRI of the component to define, e.g.
            ``"rdfc:SPARQLIngest"``. Doubles as the subject of the
            emitted block and as the ``sh:targetClass`` to look for in
            the upstream Turtle.
        package: Registry package name (``"@rdfc/log-processor-ts"``,
            ``"rdfc_log-processor"``). ``None`` only for path-sourced
            components.
        version: Value for ``spdx:versionInfo``, copied verbatim. It
            must already carry its own operator (``^``, ``>=``) —
            ``RdfcDockerFileCompiler._dependency_lines_pip`` treats this
            as a catalog-modelling constraint, so this package does not
            invent one.
        from_path: Repo-relative directory holding an already-checked-out
            copy of the package. Set for components whose source lives
            in this repo rather than on a registry; harvesting then reads
            the filesystem instead of the network.
        download_location: Value for ``spdx:downloadLocation``. This is a
            *container* path consumed by pip/npm inside the image, and is
            unrelated to ``from_path`` (a host path used at harvest
            time). Presence of this field is what marks a package local.
        supplied_by: Override for ``spdx:suppliedBy`` (``":npm"`` /
            ``":pip"``). Normally left unset and derived from the
            upstream implementation language.
        source_file: Path of the Turtle file inside the package, relative
            to the package root. Only needed when a package ships several
            (``@rdfc/sds-processors-ts`` ships ten) and auto-detection by
            ``sh:targetClass`` is ambiguous.
    """

    component: str
    package: str | None = None
    version: str | None = None
    from_path: str | None = None
    download_location: str | None = None
    supplied_by: str | None = None
    source_file: str | None = None

    @property
    def slug(self) -> str:
        """Filesystem-safe stem for this component's snapshot files.

        Derived from the component IRI rather than the package name
        because several components can share one package
        (``rdfc:HttpServer`` and ``rdfc:HttpFetch`` both come from
        ``@rdfc/http-utils-processor-ts``), and each needs its own
        record.
        """
        return self.component.replace(":", "_").replace("/", "_")


@dataclass(frozen=True)
class HarvestRecord:
    """Frozen answer from a registry (or a local checkout).

    ``turtle`` holds the upstream definition **verbatim**. Everything
    the emitter produces is derived from it plus the handful of
    registry fields below, so a reviewer can always diff the generated
    catalog against the exact bytes it came from.
    """

    component: str
    source: str  # "npm" | "pypi" | "path"
    package: str | None
    resolved_version: str | None
    language: str
    label: str | None
    comment: str | None
    landing_page: str | None
    source_file: str
    module_path: str | None
    turtle: str

    @property
    def runner(self) -> str:
        return LANGUAGE_RUNTIME[self.language][0]

    @property
    def default_manager(self) -> str:
        return LANGUAGE_RUNTIME[self.language][1]
