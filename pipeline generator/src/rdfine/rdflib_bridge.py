"""
Glue between :class:`~rdfine.prefix_store.PrefixStore` and rdflib types.

The functions here translate between rdflib's :class:`Node` hierarchy and
native Python values, and bind a store's prefixes onto an rdflib
:class:`Graph` for serialization. They live outside :class:`PrefixStore` so
that the registry itself only deals with strings; rdflib-specific concerns are
isolated to this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rdflib import BNode, Graph, Literal, Node, URIRef

if TYPE_CHECKING:
    from .prefix_store import PrefixStore


def bind_to_namespace(graph: Graph, store: "PrefixStore") -> None:
    """
    Upsert every (prefix → url) pair from ``store`` onto ``graph`` so the
    rdflib serializer can emit compact IRIs.

    ``override=True`` makes each call replace any existing binding that
    collides with the pair being applied — either the prefix already
    pointing elsewhere, or the url already claimed by a different
    prefix (e.g. rdflib's own "core" default ``dcterms`` for the same
    URL our catalogs bind as ``dct``). Bindings ``store`` doesn't
    mention are left untouched. Callers that merge two stores onto one
    graph (see ``GraphReader.add``/``remove``) must apply the
    authoritative store last so its pairs are the ones left standing.
    """
    for prefix, url in store.prefixes.items():
        graph.bind(prefix, url, override=True)


def node_to_python(node: Node, store: "PrefixStore"):
    """
    Convert an rdflib ``Node`` to a native Python value.

    - ``URIRef``  → IRI string, compacted using ``store``.
    - ``BNode``   → ``"_:<identifier>"`` (N3 form).
    - ``Literal`` → ``node.toPython()``.
    - Anything else is returned unchanged.
    """
    if isinstance(node, URIRef):
        return store.compact_string(str(node))
    if isinstance(node, BNode):
        return node.n3()
    if isinstance(node, Literal):
        return node.toPython()
    return node


def python_to_node(value, node_class, store: "PrefixStore") -> Node:
    """
    Convert ``value`` to an rdflib node of class ``node_class``.

    ``node_class`` must be :class:`URIRef`, :class:`BNode`, or
    :class:`Literal`. Prefix expansion / lookup uses ``store``.
    """
    if node_class in (URIRef, BNode) and not isinstance(value, str):
        raise TypeError(
            f"Cannot convert {value} ({type(value).__name__}) to {node_class}; "
            "str expected."
        )

    if node_class is URIRef:
        return URIRef(store.expand_string(value))

    if node_class is BNode:
        prefix = store.fetch_prefix(value)
        if prefix is not None:
            raise TypeError(
                f"{value} has known prefix {prefix}. This indicates URIRef, "
                "but you tried converting to BNode."
            )
        if value.startswith("_:"):
            value = value.removeprefix("_:")
        return BNode(value)

    if node_class is Literal:
        return Literal(value)

    raise TypeError(f"node_class {node_class} is not URIRef, BNode or Literal.")
