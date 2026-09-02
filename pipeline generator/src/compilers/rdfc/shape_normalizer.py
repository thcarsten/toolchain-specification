"""Repair SHACL idioms that upstream RDF-Connect packages write in a
form no SHACL validator can satisfy.

:class:`RdfcImportExpander` brings each processor's own parameter shape
into the build graph so :class:`RdfcConfigShapeCompiler` can derive a
``tcs:configShape`` from it rather than from a hand-transcribed copy.
That removes drift — but it also faithfully imports upstream's mistakes,
and there is at least one it makes systematically.

**The ``xsd:iri`` idiom.** RDF-Connect packages mark IRI-valued
parameters as::

    sh:datatype xsd:iri

``xsd:iri`` is not a datatype. XML Schema defines ``xsd:anyURI``; there
is no ``xsd:iri``. And ``sh:datatype`` constrains *literals* — a value
node that is an IRI is not a literal, so it can never carry a datatype
at all. The constraint is therefore unsatisfiable as written: every
value fails it, whether or not it is the IRI the author meant to
require. The intended constraint is ``sh:nodeKind sh:IRI``.

This is an idiom, not a typo. Across the packages the demonstrator
installs it appears 24 times, in ``threshold-monitor-processor-ts`` and
throughout ``sds-processors-ts``'s configs — while the only *correct*
``sh:nodeKind sh:IRI`` uses in the same files sit in a data shape
(``ex:ErrorShape``), never in a parameter shape. Whoever hand-wrote
``catalog-rdfc.ttl``'s configShapes silently corrected it on the way in;
deriving from upstream without this pass would regress that correction.

Because the rewrite is a *correctness* fix rather than a preference, it
applies to any occurrence in the build graph, not only to imported
subgraphs. Nothing in ``data/`` uses ``xsd:iri`` (the catalog uses
``sh:nodeKind sh:IRI`` in all 7 places it needs it), so this compiler is
naturally inert unless imports have been expanded.

Ordering against :class:`RdfcConfigShapeCompiler` does not matter: that
compiler *references* upstream's property nodes rather than copying
them, so a shape derived before this pass is repaired by it too.
"""

from typing import ClassVar, NamedTuple

from rdflib import Graph, URIRef

from rdfine import GraphReader

from ..base import Compiler


class ShapeRewrite(NamedTuple):
    """One "upstream wrote X, it must be Y" repair rule.

    Terms are CURIEs, expanded against the build graph's prefix store at
    apply time so a rule reads the way it would be written in Turtle.
    """

    match_predicate: str
    match_object: str
    replace_predicate: str
    replace_object: str
    reason: str


class RdfcShapeNormalizer(Compiler):
    """Rewrite unsatisfiable SHACL constraints imported from upstream
    RDF-Connect packages into the constraint they were meant to express.

    Rules live in :attr:`REWRITES` so adding one is a data change, not a
    code change. Each application removes the offending triple and adds
    the corrected one; the pair is visible through the base class's
    :attr:`added_triples` / :attr:`removed_triples`, and the full list is
    kept on :attr:`applied` for inspection after a run::

        gen.compilers[RdfcShapeNormalizer].applied

    Deliberately *not* opt-in the way :class:`RdfcImportExpander` is.
    Expanding imports changes what the build contains and so should be a
    conscious choice; repairing a constraint that no value can ever
    satisfy does not change intent, it restores it.
    """

    #: The repair rules. Extend this rather than the code below.
    REWRITES: ClassVar[tuple[ShapeRewrite, ...]] = (
        ShapeRewrite(
            match_predicate="sh:datatype",
            match_object="xsd:iri",
            replace_predicate="sh:nodeKind",
            replace_object="sh:IRI",
            reason=(
                "xsd:iri is not a datatype (XSD defines xsd:anyURI), and "
                "sh:datatype only ever matches literals — an IRI value node "
                "carries no datatype, so the constraint is unsatisfiable. "
                "sh:nodeKind sh:IRI is the intended constraint."
            ),
        ),
    )

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        #: ``(subject, rule)`` for every rewrite actually applied.
        self.applied: list[tuple[str, ShapeRewrite]] = []

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Triggered when any rule has something to repair."""
        return any(cls._matches(graph_reader, rule) for rule in cls.REWRITES)

    @classmethod
    def _matches(cls, graph_reader: GraphReader, rule: ShapeRewrite) -> list:
        """Subjects carrying ``rule``'s offending predicate/object pair.

        Queried over the raw rdflib graph rather than through
        ``GraphReader.filter``: the subjects are the anonymous
        ``sh:property`` nodes of imported shapes, and a blank node's
        label is not stable across separate SPARQL executions, so it can
        never be safely round-tripped through a query string. Holding
        the node objects directly sidesteps that entirely.
        """
        prefix_store = graph_reader.prefix_store
        predicate = URIRef(prefix_store.expand_string(rule.match_predicate))
        obj = URIRef(prefix_store.expand_string(rule.match_object))
        return [sub for sub, _, _ in graph_reader.graph.triples((None, predicate, obj))]

    def compile(self) -> Graph:
        for rule in self.REWRITES:
            self._apply(rule)
        return self.output_reader.graph

    def _apply(self, rule: ShapeRewrite) -> None:
        prefix_store = self.output_reader.prefix_store
        old_predicate = URIRef(prefix_store.expand_string(rule.match_predicate))
        old_object = URIRef(prefix_store.expand_string(rule.match_object))
        new_predicate = URIRef(prefix_store.expand_string(rule.replace_predicate))
        new_object = URIRef(prefix_store.expand_string(rule.replace_object))

        subjects = self._matches(self.output_reader, rule)
        if not subjects:
            return

        stale = Graph()
        fresh = Graph()
        prefix_store.bind_to_namespace(stale)
        prefix_store.bind_to_namespace(fresh)
        for subject in subjects:
            stale.add((subject, old_predicate, old_object))
            fresh.add((subject, new_predicate, new_object))
            self.applied.append((str(subject), rule))

        self.output_reader = self.output_reader.remove(stale).add(fresh)
