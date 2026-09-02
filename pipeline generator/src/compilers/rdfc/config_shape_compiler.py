"""Derive a component's ``tcs:configShape`` from the shape its own
package ships, instead of transcribing it into the catalog by hand.

Every RDF-Connect processor publishes a SHACL node shape describing its
parameters, targeted at the processor class::

    [] a sh:NodeShape ;
        sh:targetClass tm:ThresholdMonitorJs ;
        sh:property [ sh:class rdfc:Reader ; sh:path rdfc:reader ; ... ],
                    [ sh:datatype xsd:double ; sh:path tm:max ; ... ] .

``catalog-rdfc.ttl`` currently restates that shape as a
``tcs:configShape``, and the copies have already drifted from the
originals — ``tm:ThresholdMonitorJs``'s catalog entry drops the
``sh:minCount 1`` upstream puts on ``rdfc:reader`` / ``rdfc:writer``, and
flattens ``tm:path``'s ``sh:class rdfl:PathLens`` to a plain property
(the catalog comment says so outright: *"isn't ported here — left as a
plain property until that's needed"*). Deriving removes the hand-copy,
and with it the drift.

Runs after :class:`RdfcImportExpander`, which is what puts the upstream
shapes in the build graph in the first place; the fixpoint loop sequences
the two automatically via their ``applies_to`` triggers.
"""

import re
from typing import Iterator

from rdflib import BNode, Graph, URIRef

from rdfine import GraphReader

from ..base import Compiler


class RdfcConfigShapeCompiler(Compiler):
    """Attach a derived ``tcs:configShape`` to every RDF-Connect
    component that has an imported shape but no configShape yet.

    The derived shape is a *whole* copy of upstream's property list,
    channel slots included. That is deliberate and not an oversight:
    ``RdfcConfigCompiler._lookup_channel_predicate`` finds a step's
    reader/writer wiring predicate by querying the component's
    configShape for ``sh:property`` entries carrying ``sh:class
    rdfc:Reader`` / ``rdfc:Writer``. Splitting channel slots out into a
    separate shape would silently break that lookup, and with it the
    automatic channel wiring in ``describe_channel_wiring``.

    What is *not* copied is ``sh:targetClass``. Upstream's shape targets
    the processor class; a ``tcs:configShape`` describes the shape of the
    component's ``tcs:PipelineConfig``, and takes its target from the
    ``dcat:Relationship`` it hangs off rather than from ``sh:target*``
    (see ``test suite/README.md``, "configShape"). Hand-written catalog
    configShapes carry no target either, so the derived ones match.

    The property nodes themselves are *referenced*, not duplicated: the
    minted shape points its ``sh:property`` at the same blank nodes the
    imported shape uses. Two shapes sharing property nodes is ordinary
    RDF, and it keeps the derived shape in lockstep with the import by
    construction — there is no second copy to fall out of date.
    """

    #: Only components whose implementation is registered under one of
    #: these predicates are considered. RDF-Connect declares processors
    #: with ``rdfc:jsImplementationOf`` / ``rdfc:pyImplementationOf`` /
    #: ``rdfc:jvmImplementationOf``; matching on those rather than on
    #: "has an imported shape" keeps the compiler from claiming shapes
    #: that happen to target something else in the same file.
    IMPLEMENTATION_PREDICATES = (
        "rdfc:jsImplementationOf",
        "rdfc:pyImplementationOf",
        "rdfc:jvmImplementationOf",
    )

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        #: ``{component_iri: minted_shape_iri}`` for everything derived.
        self.derived: dict[str, str] = {}

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Triggered by a component that has an upstream shape and no
        configShape of its own."""
        return bool(cls._components_needing_shapes(graph_reader))

    @classmethod
    def _components_needing_shapes(cls, graph_reader: GraphReader) -> list[str]:
        """Components with an imported parameter shape but no configShape.

        A component already carrying a hand-written configShape is left
        alone — this compiler fills gaps, it never overrides what the
        catalog author stated explicitly.
        """
        # UNION over the implementation predicates: a single triple
        # pattern can't match "any of these three".
        union = " UNION ".join(
            f"{{ ?component {predicate} ?impl . }}"
            for predicate in cls.IMPLEMENTATION_PREDICATES
        )

        rows = graph_reader.select(
            "DISTINCT ?component",
            f"""
            {union}
            ?shape sh:targetClass ?component ;
                   sh:property ?property .
            FILTER NOT EXISTS {{
                ?component dcat:qualifiedRelation ?rel .
                ?rel dcat:hadRole tcs:configShape .
            }}
            """,
        )
        return sorted(rows["component"].to_list())

    def compile(self) -> Graph:
        for component_id in self._components_needing_shapes(self.output_reader):
            shape_id = self._mint_shape(component_id)
            self._copy_properties(component_id, shape_id)
            self._attach_config_shape(component_id, shape_id)
            self.derived[component_id] = shape_id
        return self.output_reader.graph

    def _mint_shape(self, component_id: str) -> str:
        """Mint a named, empty ``sh:NodeShape`` for ``component_id``.

        Named after the component rather than a running index so the
        derived shape is recognisable in a serialized build, and stable
        across runs. Built from raw rdflib triples, not a ``CONSTRUCT``
        template: pairing a fresh node with a broad WHERE clause mints a
        distinct node per matched row (same reasoning as
        ``ValidationReportCompiler._mint_empty_shape``).
        """
        prefix_store = self.output_reader.prefix_store
        slug = re.sub(
            r"[^a-z0-9]+", "_", prefix_store.drop_string(component_id).lower()
        ).strip("_")
        shape_id = f":rdfcconfigshape_{slug}"
        index = 0
        while self.output_reader.check_exists(shape_id):
            index += 1
            shape_id = f":rdfcconfigshape_{slug}_{index}"

        new_triples = Graph()
        prefix_store.bind_to_namespace(new_triples)
        new_triples.add(
            (
                URIRef(prefix_store.expand_string(shape_id)),
                URIRef(prefix_store.expand_string("rdf:type")),
                URIRef(prefix_store.expand_string("sh:NodeShape")),
            )
        )
        self.output_reader = self.output_reader.add(new_triples)
        return shape_id

    def _copy_properties(self, component_id: str, shape_id: str) -> None:
        """Point ``shape_id`` at every ``sh:property`` of the imported
        shape(s) targeting ``component_id``.

        Done over the raw rdflib graph because the property objects are
        blank nodes: their labels are not stable across separate SPARQL
        executions, so they can never be safely stringified into a
        ``CONSTRUCT`` template the way a named IRI can. Referencing the
        node objects directly sidesteps the problem entirely.
        """
        prefix_store = self.output_reader.prefix_store
        shape_uri = URIRef(prefix_store.expand_string(shape_id))
        property_uri = URIRef(prefix_store.expand_string("sh:property"))

        new_triples = Graph()
        prefix_store.bind_to_namespace(new_triples)
        for property_node in self._imported_properties(component_id):
            new_triples.add((shape_uri, property_uri, property_node))
        self.output_reader = self.output_reader.add(new_triples)

    def _imported_properties(self, component_id: str) -> Iterator[BNode | URIRef]:
        """Every ``sh:property`` object of an imported shape targeting
        ``component_id``, skipping any shape this compiler itself minted
        (so a re-run can't nest a derived shape inside another)."""
        prefix_store = self.output_reader.prefix_store
        graph = self.output_reader.graph
        component_uri = URIRef(prefix_store.expand_string(component_id))
        target_class_uri = URIRef(prefix_store.expand_string("sh:targetClass"))
        property_uri = URIRef(prefix_store.expand_string("sh:property"))

        for shape_node, _, _ in graph.triples((None, target_class_uri, component_uri)):
            for _, _, property_node in graph.triples((shape_node, property_uri, None)):
                yield property_node

    def _attach_config_shape(self, component_id: str, shape_id: str) -> None:
        """Hang ``shape_id`` off ``component_id`` as a ``tcs:configShape``,
        using the same ``dcat:qualifiedRelation`` idiom the hand-written
        catalog entries and ``ValidationReportCompiler._attach_shape`` use.
        """
        relation_id = f"{shape_id}_rel"
        index = 0
        while self.output_reader.check_exists(relation_id):
            index += 1
            relation_id = f"{shape_id}_rel_{index}"

        # Anchored on the unconstrained "?s ?p ?o ." because the template
        # mints no blank node — repeated identical triples are a no-op
        # under RDF set semantics (same idiom as _attach_shape).
        new_triples = self.output_reader.construct(
            f"""
            {component_id} dcat:qualifiedRelation {relation_id} .
            {relation_id} a dcat:Relationship ;
                dcat:hadRole tcs:configShape ;
                dct:relation {shape_id} .
            """,
            "?s ?p ?o .",
        ).graph
        self.output_reader = self.output_reader.add(new_triples)
