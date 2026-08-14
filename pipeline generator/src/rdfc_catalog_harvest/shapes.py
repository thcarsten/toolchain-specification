"""Translate an upstream processor shape into a toolchain config shape.

An RDF-Connect processor ships a SHACL shape describing its own
parameters. That shape almost fits the toolchain catalog already — but
"almost" hides four systematic differences, and they are the whole reason
the hand-written catalog could not simply paste upstream Turtle.

The root cause of the first three is the same: a toolchain pipeline
definition supplies parameter values as **bare IRIs and untyped blank
nodes**, because the values live inside a ``tcs:embedded`` config block
rather than as standalone typed resources. Any upstream constraint that
depends on a value carrying an ``rdf:type`` is therefore unsatisfiable as
written, and has to be re-expressed against what the toolchain actually
asserts.

**1. Channels.** Upstream distinguishes the ends of a channel
(``sh:class rdfc:Reader`` / ``rdfc:Writer``). The toolchain models a
channel as one thing, ``tcs:Channel`` — the type
``inference_rules.yaml`` derives from ``tcs:readsFrom`` /
``tcs:writesTo``. Rewritten to ``sh:class tcs:Channel``; the read/write
direction is preserved on a non-constraining ``tcs:upstreamClass`` so a
future cross-framework channel model can recover it.

**2. Nested config objects.** Upstream says ``sh:class rdfc:IngestConfig``
and separately defines ``sh:targetClass rdfc:IngestConfig``. But the
pipeline writes ``rdfc:ingestConfig [ rdfc:operationMode "Sync" ; ... ]``
— an untyped blank node. Rewritten to ``sh:node`` against a named shape,
with that nested shape targeted by ``sh:targetObjectsOf`` on the property
path instead of by class.

**3. Foreign classes.** ``sh:class`` pointing at a class from another
vocabulary can only ever fail, because the value is never ``rdf:type``d:
``rdfl:PathLens`` on ``tm:path`` receives ``sosa:hasSimpleResult``, a
valid one-step rdf-lens path but not a typed instance. Two outcomes,
depending on whether the toolchain models the class's *value space*:

- Listed in :data:`EXTERNAL_SHAPES` → rewritten to ``sh:node`` against a
  hand-written shape. ``rdfl:PathLens`` gets ``:ShaclPathShape``, which
  describes the property-path grammar rdf-lens can interpret. This is
  worth doing rather than skipping: the path is consumed at execution
  time to extract values from each record, so an invalid one is a runtime
  failure that validation can catch first.
- Otherwise → demoted to a ``tcs:upstreamClass`` annotation, because a
  constraint the toolchain cannot satisfy is worse than a documented one
  it does not check.

**4. ``xsd:iri``.** Upstream uses ``sh:datatype xsd:iri`` for IRI-valued
parameters. This is not a typo: rdf-lens keys its own extractor off that
exact term (``ShaclPredicatePath = extractLeaf(XSD.terms.custom("iri"))``),
so the marker is load-bearing at execution time. It is simply not valid
*validation* — there is no ``xsd:iri`` datatype, so as SHACL it demands a
literal no IRI can be. Rewritten to ``sh:nodeKind sh:IRI`` for checking,
with the original preserved on ``tcs:upstreamDatatype`` so the runtime
convention is not lost.

Rules 1 and 2 were reverse-engineered from the one hand-written shape in
the catalog that actually fires (``:SparqlIngestShape``). Rules 3 and 4
surfaced only once generation made every component's shape live —
neither was reachable before, so neither had ever been noticed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rdflib import Graph, URIRef

from .model import iri
from .turtle import INDENT, compact, inline_bnode, render_term

SH_PROPERTY = iri("sh:property")
SH_PATH = iri("sh:path")
SH_CLASS = iri("sh:class")
SH_DATATYPE = iri("sh:datatype")
SH_NODE = iri("sh:node")
SH_NODEKIND = iri("sh:nodeKind")
SH_TARGET_CLASS = iri("sh:targetClass")

RDFC_READER = iri("rdfc:Reader")
RDFC_WRITER = iri("rdfc:Writer")
CHANNEL_CLASSES = {RDFC_READER, RDFC_WRITER}

# Upstream writes `sh:datatype xsd:iri` to mean "this parameter takes an
# IRI, not a literal". There is no such XSD datatype, so read as real
# SHACL the constraint says "a literal whose datatype is xsd:iri" — which
# no IRI can ever be, making it permanently unsatisfiable. `sh:nodeKind
# sh:IRI` is the standard way to say what upstream meant.
XSD_IRI = iri("xsd:iri")

TCS_CHANNEL = compact(iri("tcs:Channel"))
TCS_UPSTREAM_CLASS = compact(iri("tcs:upstreamClass"))
TCS_UPSTREAM_DATATYPE = compact(iri("tcs:upstreamDatatype"))

# Classes from vocabularies outside RDF-Connect whose value space the
# toolchain can nonetheless check, mapped to a hand-written shape in
# catalog-rdfc-manual.ttl.
#
# This matters for more than tidiness: an ``rdfl:PathLens`` value is a
# SHACL property path that rdf-lens walks at *execution* time to pull a
# value out of each record, so a malformed path is a runtime extraction
# failure, not a cosmetic problem. Checking it here catches the mistake
# before deployment — in particular a quoted string, which upstream's
# ``sh:datatype xsd:iri`` convention actively invites.
#
# Anything absent from this registry still degrades to a
# ``tcs:upstreamClass`` annotation, so adding an entry is how a foreign
# constraint gets promoted from documented to enforced.
EXTERNAL_SHAPES: dict[URIRef, str] = {
    URIRef("https://w3id.org/rdf-lens/ontology#PathLens"): ":ShaclPathShape",
}

# Canonical predicate order inside an emitted property shape. Anything
# not listed sorts alphabetically after these, so an upstream addition
# lands in a predictable place instead of reshuffling the file.
_PREDICATE_ORDER = [
    str(iri(curie))
    for curie in (
        "sh:path",
        "sh:name",
        "sh:description",
        "sh:datatype",
        "sh:class",
        "sh:node",
        "sh:nodeKind",
        "sh:in",
        "sh:hasValue",
        "sh:minCount",
        "sh:maxCount",
    )
]

# SHACL constructs this translator understands. Upstream currently uses
# a strict subset; anything new fails the run rather than being dropped
# silently, because a silently dropped constraint is exactly the class
# of bug hand-transcription produced.
_SUPPORTED = set(_PREDICATE_ORDER) | {str(SH_PROPERTY), str(SH_TARGET_CLASS)}


def _order_key(predicate: URIRef) -> tuple[int, str]:
    text = str(predicate)
    if text in _PREDICATE_ORDER:
        return (_PREDICATE_ORDER.index(text), "")
    return (len(_PREDICATE_ORDER), text)


def shape_iri(class_node: URIRef) -> str:
    """Name for the toolchain shape derived from ``class_node``.

    ``rdfc:SPARQLIngest`` becomes ``:SPARQLIngestShape``. Collisions are
    impossible because a component and its nested config classes are
    distinct classes with distinct local names.
    """
    local = str(class_node).rsplit("#", 1)[-1].rsplit("/", 1)[-1]
    return f":{local}Shape"


@dataclass
class TranslatedShape:
    """One emitted ``sh:NodeShape``.

    ``target`` is the rendered targeting clause — a SPARQL target for a
    component's own shape, ``sh:targetObjectsOf`` for a nested one.
    """

    iri: str
    target: list[tuple[str, str]]
    properties: list[list[tuple[str, str]]]
    comment: str = ""
    nested: list["TranslatedShape"] = field(default_factory=list)


def _property_shapes(graph: Graph, node) -> list:
    return list(graph.objects(node, SH_PROPERTY))


def node_shape_for(graph: Graph, class_node: URIRef):
    """The shape node whose ``sh:targetClass`` is ``class_node``, if any."""
    for subject in graph.subjects(SH_TARGET_CLASS, class_node):
        return subject
    return None


def _translate_property(
    graph: Graph,
    property_node,
    nested_targets: dict[URIRef, URIRef],
) -> tuple[list[tuple[str, str]], URIRef | None]:
    """Render one property shape, applying both rewrite rules.

    Returns the rendered predicate/object pairs and, when the property
    points at a nested config class, that class — so the caller can emit
    its shape and target it by this property's path.
    """
    pairs: list[tuple[str, str]] = []
    nested_class: URIRef | None = None
    path = graph.value(property_node, SH_PATH)

    for predicate in sorted(set(graph.predicates(property_node)), key=_order_key):
        if str(predicate) not in _SUPPORTED:
            raise NotImplementedError(
                f"unsupported SHACL construct {compact(predicate)} on a property "
                f"shape for path {compact(path) if path else '?'}. Extend "
                "rdfc_catalog_harvest.shapes._SUPPORTED once its translation is decided."
            )
        for value in graph.objects(property_node, predicate):
            if predicate == SH_CLASS and isinstance(value, URIRef):
                if value in CHANNEL_CLASSES:
                    # Rewrite 1: collapse Reader/Writer to tcs:Channel,
                    # keeping the direction as a non-constraining hint.
                    pairs.append((compact(SH_CLASS), TCS_CHANNEL))
                    pairs.append((TCS_UPSTREAM_CLASS, compact(value)))
                    continue
                if value in nested_targets:
                    # Rewrite 2: class constraint -> shape reference, so
                    # an untyped embedded blank node can still validate.
                    nested_class = value
                    pairs.append((compact(SH_NODE), shape_iri(value)))
                    continue
                # Rewrite 3a: a foreign class whose value space the
                # toolchain models by hand. Constrain against that shape
                # and keep the original class as provenance.
                if value in EXTERNAL_SHAPES:
                    pairs.append((compact(SH_NODE), EXTERNAL_SHAPES[value]))
                    pairs.append((TCS_UPSTREAM_CLASS, compact(value)))
                    continue

                # Rewrite 3b: a class the toolchain never asserts and has
                # no shape for. A pipeline definition supplies bare IRIs
                # and untyped blank nodes, so `sh:class` here could only
                # ever fail. Recording it as a non-constraining
                # annotation keeps the information without asserting
                # something uncheckable. Promote it by adding an entry to
                # EXTERNAL_SHAPES.
                pairs.append((TCS_UPSTREAM_CLASS, compact(value)))
                continue

            if predicate == SH_DATATYPE and value == XSD_IRI:
                # Rewrite 4: xsd:iri is not a datatype. Say what upstream
                # meant, and keep what it wrote.
                pairs.append((compact(SH_NODEKIND), "sh:IRI"))
                pairs.append((TCS_UPSTREAM_DATATYPE, compact(XSD_IRI)))
                continue

            pairs.append((compact(predicate), render_term(value)))

    return pairs, nested_class


def _sparql_target(component: URIRef) -> list[tuple[str, str]]:
    """Targeting clause selecting a component's embedded step config.

    ``sh:targetObjectsOf`` is not expressive enough here: the focus node
    is two hops from the step (``p-plan:hasInputVar/tcs:embedded``) and
    must be reached only for steps specialising *this* component.
    """
    query = (
        "\n"
        f"{INDENT * 3}SELECT ?this WHERE {{\n"
        f"{INDENT * 4}?step prov:specializationOf {compact(component)} ;\n"
        f"{INDENT * 5}  p-plan:hasInputVar/tcs:embedded ?this .\n"
        f"{INDENT * 3}}}\n"
        f"{INDENT * 2}"
    )
    inner = [
        ("a", "sh:SPARQLTarget"),
        ("sh:prefixes", "tcs:prefixes"),
        ("sh:select", f'"""{query}"""'),
    ]
    return [("sh:target", inline_bnode(inner, INDENT))]


def translate(
    graph: Graph,
    component: URIRef,
) -> TranslatedShape | None:
    """Build the toolchain config shape for ``component``.

    Returns ``None`` when the package ships no shape for it — a
    parameterless processor, which is legitimate.
    """
    root = node_shape_for(graph, component)
    if root is None:
        return None

    # Which classes referenced by sh:class have their own shape here?
    # Only those become sh:node references; everything else passes
    # through untouched.
    nested_targets: dict[URIRef, URIRef] = {}
    for class_value in graph.objects(None, SH_CLASS):
        if not isinstance(class_value, URIRef) or class_value in CHANNEL_CLASSES:
            continue
        shape_node = node_shape_for(graph, class_value)
        if shape_node is not None:
            nested_targets[class_value] = shape_node

    def _build(
        shape_node,
        iri: str,
        target: list[tuple[str, str]],
        comment: str,
        seen: set,
    ) -> TranslatedShape:
        translated = TranslatedShape(
            iri=iri, target=target, properties=[], comment=comment
        )
        entries: list[tuple[str, list[tuple[str, str]], URIRef | None]] = []
        for property_node in _property_shapes(graph, shape_node):
            pairs, nested_class = _translate_property(
                graph, property_node, nested_targets
            )
            path = graph.value(property_node, SH_PATH)
            entries.append((str(path) if path else "", pairs, nested_class))

        # sh:property is set-valued, so upstream order is not meaningful.
        # Sorting by path makes the emitted file byte-stable.
        for _, pairs, nested_class in sorted(entries, key=lambda e: e[0]):
            translated.properties.append(pairs)
            if nested_class is None or nested_class in seen:
                continue
            seen.add(nested_class)
            nested_path = next(
                (p for p, _, c in entries if c == nested_class),
                None,
            )
            translated.nested.append(
                _build(
                    nested_targets[nested_class],
                    shape_iri(nested_class),
                    [
                        (
                            "sh:targetObjectsOf",
                            compact(URIRef(nested_path)) if nested_path else "?",
                        )
                    ],
                    f"Nested config object of {compact(component)}, reached via "
                    f"{compact(URIRef(nested_path)) if nested_path else '?'}. "
                    "Targeted by sh:targetObjectsOf rather than sh:targetClass "
                    "because pipeline definitions embed it untyped.",
                    seen,
                )
            )
        return translated

    return _build(
        root,
        shape_iri(component),
        _sparql_target(component),
        f"Config shape for {compact(component)}, generated from its upstream "
        "SHACL shape. Referenced from the component via dcat:qualifiedRelation "
        "with role tcs:configShape, and additionally carries a SPARQL target so "
        "pySHACL validates the embedded step config directly.",
        set(),
    )
