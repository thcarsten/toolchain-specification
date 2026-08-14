"""Deterministic Turtle text emission.

The generated catalog is a checked-in, human-reviewed file, so it is
built as *text* rather than via ``rdflib.Graph.serialize``. Two reasons:

- **Stable diffs.** rdflib relabels blank nodes on every run and orders
  set-valued predicates arbitrarily, so serialising the same graph twice
  can produce different bytes. A reviewer needs a diff that only moves
  when the meaning moves.
- **Readability.** The hand-written catalog files group and comment
  their blocks. Generated output should look like it belongs next to
  them, which a generic serialiser cannot do.

Correctness is not taken on faith: :mod:`rdfc_catalog_harvest.emitter` re-parses its
own output and the test suite compares it to the upstream source
graph-wise, so a rendering bug surfaces as a failed isomorphism rather
than a silently wrong catalog.
"""

from __future__ import annotations

from rdflib import BNode, Literal, URIRef
from rdflib.namespace import XSD

from .model import PREFIX_STORE

INDENT = "    "


def compact(node: URIRef) -> str:
    """Render an IRI prefixed if possible, else as a full ``<IRI>``.

    The prefix matching itself is :class:`rdfine.PrefixStore`'s — it
    already resolves longest-namespace-wins from a deterministically
    ordered table, which is the property the generated file's byte
    stability rests on, and a second copy of that loop here could drift
    from it. What stays local is the Turtle *syntax* policy, which is
    not the store's business:

    - Unknown namespaces fall through to the angle-bracket form rather
      than getting a synthesised prefix. Upstream shapes reference
      foreign vocabularies (``rdf-lens:PathLens``), and inventing a
      prefix per vocabulary would churn the prefix header every time
      upstream adds one.
    - A local part containing a slash, or starting with a digit, is not
      a legal prefixed name, so it also falls back rather than emitting
      something unparseable.
    """
    text = str(node)
    qname = PREFIX_STORE.compact_string(text)
    if qname == text:  # no namespace matched
        return f"<{text}>"
    _, _, local = qname.partition(":")
    if not local or "/" in local or local[0].isdigit():
        return f"<{text}>"
    return qname


def render_literal(value: Literal) -> str:
    """Render a literal in its shortest unambiguous Turtle form."""
    datatype = value.datatype
    if datatype in (XSD.integer, XSD.int, XSD.long):
        return str(int(value))
    if datatype == XSD.boolean:
        return "true" if str(value).lower() in ("true", "1") else "false"
    if datatype in (XSD.decimal, XSD.double, XSD.float):
        return str(value)

    text = str(value)
    if "\n" in text or '"' in text:
        # Long-quoted form; escape only a trailing quote run, which would
        # otherwise merge with the closing delimiter.
        escaped = text.replace('\\', '\\\\').replace('"""', '\\"\\"\\"')
        if escaped.endswith('"'):
            escaped = escaped[:-1] + '\\"'
        body = f'"""{escaped}"""'
    else:
        body = '"' + text.replace('\\', '\\\\') + '"'

    if value.language:
        return f"{body}@{value.language}"
    if datatype is not None and datatype != XSD.string:
        return f"{body}^^{compact(datatype)}"
    return body


def render_term(node) -> str:
    """Render any RDF term. Blank nodes are rejected.

    Every blank node the emitter produces is written structurally as an
    inline ``[ ... ]``; a bare blank-node label reaching this function
    means a subgraph was inlined incorrectly, so it fails loudly.
    """
    if isinstance(node, URIRef):
        return compact(node)
    if isinstance(node, Literal):
        return render_literal(node)
    if isinstance(node, BNode):
        raise ValueError(
            "blank nodes must be rendered inline as [ ... ], not by label; "
            f"got {node!r}"
        )
    raise TypeError(f"cannot render {type(node).__name__}")


def predicate_object_block(
    pairs: list[tuple[str, str]],
    indent: str,
) -> str:
    """Render ``pred obj ;`` lines for an already-rendered pair list.

    Pairs arrive pre-rendered and pre-ordered — ordering policy belongs
    to the caller that knows the vocabulary, not here.
    """
    return " ;\n".join(f"{indent}{predicate} {obj}" for predicate, obj in pairs)


def inline_bnode(pairs: list[tuple[str, str]], indent: str) -> str:
    """Render a ``[ ... ]`` blank node containing ``pairs``."""
    if not pairs:
        return "[ ]"
    body = predicate_object_block(pairs, indent + INDENT)
    return "[\n" + body + "\n" + indent + "]"


def statement(subject: str, pairs: list[tuple[str, str]]) -> str:
    """Render one complete ``subject pred obj ; ... .`` statement."""
    if not pairs:
        raise ValueError(f"refusing to emit {subject} with no predicates")
    return f"{subject}\n" + predicate_object_block(pairs, INDENT) + " .\n"


def wrap(text: str, width: int = 62) -> list[str]:
    """Greedy word wrap, so generated prose comments stay readable."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


def banner(title: str, body: str = "") -> str:
    """Render a ``###``-delimited section comment matching catalog style."""
    rule = "#" * 65
    lines = [rule, f"# {title}"]
    if body:
        lines.append("#")
        lines.extend(f"# {line}".rstrip() for line in wrap(body.strip()))
    lines.append(rule)
    return "\n".join(lines)
