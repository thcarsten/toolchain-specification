"""Load the RDF graphs a component's ``owl:imports`` points at.

RDF-Connect processors ship their own SHACL parameter shape inside the
package that implements them (``processor.ttl`` / ``processors.ttl``),
and ``catalog-rdfc.ttl`` already records where that file lives via
``owl:imports``. Until now nothing read those files at compile time —
``RdfcConfigCompiler`` only copies the ``owl:imports`` triple through
into the emitted ``pipeline.ttl`` so the RDF-Connect runtime resolves it
itself. This compiler is what makes the imported graphs available to the
*generator*, so downstream compilers can derive catalog metadata from
upstream's own declarations instead of transcribing it by hand
(see :class:`RdfcConfigShapeCompiler`).
"""

from pathlib import Path
from typing import ClassVar

from rdflib import Graph, OWL

from rdfine import GraphReader

from ..base import Compiler


class RdfcImportExpander(Compiler):
    """Parse every resolvable ``owl:imports`` target into the build graph.

    ``owl:imports`` objects in the catalog are absolute ``file:`` IRIs
    rooted at the paths the *generated container* will use, because the
    catalog files are parsed with ``publicID="file:///workspace/pipeline/"``
    and the imports themselves are written relative
    (``<./node_modules/@rdfc/...>``). So an import reads e.g.::

        file:///workspace/pipeline/node_modules/@rdfc/threshold-monitor-processor-ts/processor.ttl
        file:///usr/local/lib/python3.13/site-packages/rdfc_log_processor/processor.ttl

    Those paths do not exist on the machine running the generator, so
    this compiler needs an explicit mapping from container-side IRI
    prefix to a local directory. Set :attr:`import_roots` before
    compiling::

        RdfcImportExpander.import_roots = {
            "file:///workspace/pipeline/": "/path/to/demonstrator/RDFC",
        }

    **The mapping is empty by default and :meth:`applies_to` returns
    ``False`` while it is**, so a generator run that has not opted in
    behaves exactly as before. This is deliberate: expanding imports
    merges third-party triples into the build graph, which is a
    meaningful change in what downstream compilers and the SHACL
    validation pass see, and should never happen implicitly.

    Imports with no matching root (typically the Python-side processors,
    which live under ``site-packages`` in a different image) are recorded
    on :attr:`unresolved` and skipped rather than raising — a pipeline
    that mixes JS and Python processors should still compile with only
    the JS side expanded.

    Note on scope: the whole imported graph is merged, which is what
    ``owl:imports`` means, but it does bring along declarations beyond
    the parameter shape — ``rdfc:jsImplementationOf``, entrypoints, and
    in some packages a data shape for what the processor emits (see
    ``threshold-monitor-processor-ts``'s ``ex:ErrorShape``, "Shape of the
    ``oslc:Error`` reports written to ``writer``"). Those extra shapes
    carry their own ``sh:targetClass`` and therefore become live
    validation targets in the build. Harmless for the demonstrator today
    (nothing in the build graph is typed ``oslc:Error``), but worth
    revisiting if a package ever ships a shape targeting a class the
    build does contain.
    """

    #: Container-side IRI prefix -> local directory holding those files.
    #: Longest matching prefix wins. Empty (the default) disables the
    #: compiler entirely.
    import_roots: ClassVar[dict[str, str]] = {}

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        #: Import IRIs successfully parsed into the build graph.
        self.expanded: list[str] = []
        #: Import IRIs skipped — no configured root, or no file there.
        self.unresolved: list[str] = []

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Triggered when roots are configured and the build has imports.

        Reads ``owl:imports`` off the raw rdflib graph rather than
        through ``GraphReader.filter``: the objects are ``file:`` IRIs
        with no prefix binding, and the DataFrame view compacts nodes
        through the prefix store. Going direct keeps the IRI byte-exact,
        which matters because it is about to be turned into a filesystem
        path.
        """
        if not cls.import_roots:
            return False
        return next(graph_reader.graph.triples((None, OWL.imports, None)), None) is not None

    def compile(self) -> Graph:
        for import_iri in self._import_iris():
            path = self._resolve(import_iri)
            if path is None:
                self.unresolved.append(import_iri)
                continue

            imported = Graph(bind_namespaces="none")
            # publicID is the import's own IRI so any relative reference
            # inside the file (e.g. rdfc:file <./lib/index.js>) resolves
            # against the file it was written in, exactly as the
            # RDF-Connect runtime would resolve it.
            imported.parse(str(path), format="turtle", publicID=import_iri)
            self.output_reader = self.output_reader.add(imported)
            self.expanded.append(import_iri)

        return self.output_reader.graph

    def _import_iris(self) -> list[str]:
        """Every distinct ``owl:imports`` object, in a stable order."""
        return sorted(
            {str(obj) for _, _, obj in self.output_reader.graph.triples((None, OWL.imports, None))}
        )

    def _resolve(self, import_iri: str) -> Path | None:
        """Map a container-side import IRI onto a local file, or ``None``.

        Longest configured prefix wins, so a specific root can override
        a broader one. Returns ``None`` when no root matches or the file
        is absent — the caller records it as unresolved and moves on.
        """
        for prefix in sorted(self.import_roots, key=len, reverse=True):
            if not import_iri.startswith(prefix):
                continue
            relative = import_iri[len(prefix) :].lstrip("/")
            candidate = Path(self.import_roots[prefix]) / relative
            return candidate if candidate.is_file() else None
        return None
