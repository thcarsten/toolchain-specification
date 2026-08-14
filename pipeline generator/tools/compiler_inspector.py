"""A local web UI for stepping the compiler fixpoint loop one compiler
at a time and watching what each one does to the build graph.

``PipelineGenerator.compile()`` runs the whole loop and hands back a
finished graph, which is what you want in production and useless when a
compiler misbehaves: by the time you can inspect the result, fourteen
compilers have each had a turn. This tool drives the same loop manually
— pick the next compiler, run it, read its effect as a diff of the
build graph in Turtle, then browse the whole store.

Both views are Turtle rather than a triple table, because a triple table
cannot show shape: ``sh:property [ sh:path … ]`` is one thing you wrote
and six rows in a table. The diff is a git-style unified diff between
the store before and after the step, unchanged runs folded away.

Run it::

    cd "pipeline generator"
    python tools/compiler_inspector.py

    # to exercise the opt-in RdfcImportExpander as well:
    python tools/compiler_inspector.py --rdfc-root ../../demonstrator/RDFC

then open http://127.0.0.1:8765.

Stdlib only — no Flask, no new dependency in ``requirements.txt`` for a
debugging aid.

**It reuses the real generator rather than reimplementing it.**
:class:`SteppableGenerator` subclasses :class:`PipelineGenerator` and
calls its ``_record_creator`` / ``_set_finishing`` / ``_strip_finishing``
helpers, so provenance triples and the ``tcs:isFinishing`` flag behave
exactly as they do in a normal run. A stepper that reimplemented the
loop would eventually drift from it and quietly lie to you.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
import threading
from collections import Counter
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

_TOOLS_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _TOOLS_DIR.parent
sys.path.insert(0, str(_PROJECT_DIR / "src"))

from rdflib import BNode, Graph, Literal, URIRef  # noqa: E402
from rdflib.namespace import RDF  # noqa: E402
from rdflib.term import Node  # noqa: E402

from rdfine import GraphReader  # noqa: E402

import compilers as compilers_pkg  # noqa: E402  (populates Compiler._registry)
from compilers import PipelineGenerator  # noqa: E402
from compilers.base import Compiler  # noqa: E402
from compilers.core.pipeline_extractor import PipelineExtractor  # noqa: E402

DATA_DIR = _PROJECT_DIR / "data"
CATALOG_FILES = (
    "catalog-core.ttl",
    "catalog-ldio.ttl",
    "catalog-rdfc.ttl",
    "catalog-sw.ttl",
)
SHAPES_FILE = "catalog-application-profile-shapes.ttl"
PIPELINE_FILE = "pipeline_definition.ttl"
INFERENCE_RULES = "inference_rules.yaml"
PUBLIC_ID = "file:///workspace/pipeline/"


def load_catalog() -> Graph:
    """Catalog + application-profile shapes + pipeline definition, with
    the inference rules applied — the same entry path
    ``testing_helpers.compile_pipeline`` and ``demo.ipynb`` use."""
    graph = Graph()
    for filename in (*CATALOG_FILES, SHAPES_FILE, PIPELINE_FILE):
        graph.parse(str(DATA_DIR / filename), publicID=PUBLIC_ID)
    return GraphReader(graph).infer(str(DATA_DIR / INFERENCE_RULES)).graph


# ----------------------------------------------------------------------
# Turtle rendering
# ----------------------------------------------------------------------

INDENT = "    "
INLINE_COLLECTION_WIDTH = 68
MAX_DIFF_LINES = 20000


class TurtleWriter:
    """A deterministic, diff-friendly Turtle serializer.

    ``rdflib``'s own Turtle output cannot back the diff view. It picks
    subject order from an internal heuristic that reshuffles as triples
    arrive, so a compiler that touches one shape produces a diff
    spanning the whole document and the real change is lost in it. This
    writer fixes both the order and the line shape:

    * Subjects come out in *reference order* — a depth-first walk from
      the nodes nothing points at — so a resource is followed by the
      resources it mentions rather than by whatever happens to sort
      next. That is the "topological" part: reading top to bottom
      follows the pipeline instead of the alphabet.
    * Every predicate-object pair gets its own line ending in ``;``, and
      the closing ``.`` sits on a line of its own. Adding a property
      therefore adds exactly one line, instead of also re-punctuating
      the line above it — Turtle's usual ``… ;`` → ``… .`` shuffle
      shows up as a spurious ± pair in every diff.
    * A repeated predicate is written out again per object rather than
      using a ``,`` continuation, for the same reason.
    * Blank nodes with a single reference are inlined as ``[ … ]`` and
      well-formed ``rdf:List``s as ``( … )``, which is what makes the
      output read like the shapes that were written by hand. In this
      graph that covers essentially every blank node; the rest fall
      back to explicit ``_:label`` blocks.

    Reference order alone is not stable enough, though. It is derived
    from who points at whom, so a compiler that merely points at an
    existing resource moves that resource's whole block to a new place
    in the document: ``PipelineAssembler`` adds 60 triples and relocates
    87 channel blocks, 800 lines of pure churn around the 60 that
    matter. ``previous`` fixes that. Pass the subject order of the
    preceding snapshot and subjects that already had a place keep it,
    with genuinely new subjects spliced in at their reference-order
    position. The layout then only ever grows, and a diff shows what a
    compiler *did* rather than how it reshuffled the file.

    Blank-node labels are only stable within one process. The build
    graph is mutated in place across steps, so they hold still for the
    diffs here — but they will differ from a fresh parse of the same
    file, as will minted ``:channel_N`` names (see the module docstring
    of the generator).
    """

    def __init__(
        self,
        graph: Graph,
        root: str | None = None,
        previous: list[Node] | None = None,
    ) -> None:
        self.graph = graph
        self.nm = graph.namespace_manager
        self.root = self._resolve(root)
        self.previous = previous
        self.order: list[Node] = []
        self.po: dict[Node, dict[URIRef, list[Node]]] = {}
        self.indeg: Counter = Counter()
        for subject, predicate, obj in graph:
            self.po.setdefault(subject, {}).setdefault(predicate, []).append(obj)
            if isinstance(obj, (URIRef, BNode)):
                self.indeg[obj] += 1
        self.used_prefixes: set[str] = set()
        self.emitted: set[Node] = set()
        self._queued: set[Node] = set()
        self._open: set[Node] = set()  # blank nodes currently being inlined
        self._sig_cache: dict[Node, str] = {}

    def _resolve(self, curie: str | None) -> URIRef | None:
        """The pipeline id as a term, so the walk can start there.

        Cosmetic only — an unresolvable id just means the document opens
        on whichever root sorts first — so a missing prefix binding is
        not worth an exception in a viewer.
        """
        if not curie:
            return None
        try:
            return URIRef(self.nm.expand_curie(curie))
        except (ValueError, TypeError):
            return URIRef(curie)

    # -- terms --------------------------------------------------------

    def _term(self, term: Node) -> str:
        """One term, Turtle-shaped. ``n3`` does the hard parts (literal
        escaping, language tags, datatype compaction); we only record
        which prefixes it actually reached for, so the header can list
        those and not all forty-two the store has bound."""
        if isinstance(term, BNode):
            return f"_:{term}"
        text = term.n3(self.nm)
        if isinstance(term, URIRef):
            self._note_prefix(text)
        elif isinstance(term, Literal) and term.datatype is not None:
            self._note_prefix(text.rsplit("^^", 1)[-1])
        return text

    def _note_prefix(self, qname: str) -> None:
        if not qname.startswith("<"):
            self.used_prefixes.add(qname.split(":", 1)[0])

    def _signature(self, node: Node) -> str:
        """A content-derived sort key.

        Named nodes sort by their qname, but an inlined blank node has
        no name to sort on and sorting by its rdflib label would
        reshuffle the document on every parse. Its contents are the only
        stable thing about it.
        """
        if node in self._sig_cache:
            return self._sig_cache[node]
        if not isinstance(node, BNode) or node not in self.po:
            return self._term(node)
        self._sig_cache[node] = "[]"  # cycle guard, replaced below
        parts = sorted(
            f"{self._term(predicate)} {self._signature(obj)}"
            for predicate, objects in self.po[node].items()
            for obj in objects
        )
        signature = "[" + " ".join(parts) + "]"
        self._sig_cache[node] = signature
        return signature

    def _inlinable(self, node: Node) -> bool:
        return (
            isinstance(node, BNode)
            and self.indeg[node] == 1
            and node not in self._open
        )

    def _as_collection(self, node: Node) -> list[Node] | None:
        """The items of ``node`` if it heads a well-formed ``rdf:List``.

        "Well-formed" is strict on purpose: every cell a blank node with
        exactly one ``rdf:first`` and one ``rdf:rest`` and nothing else
        attached, referenced exactly once, chain terminating at
        ``rdf:nil``. Anything less and the cells are written out as
        ordinary blank nodes, because ``( … )`` cannot express them and
        silently dropping the extra triples would be a lie.
        """
        items: list[Node] = []
        seen: set[Node] = set()
        current = node
        while current != RDF.nil:
            if not isinstance(current, BNode) or current in seen:
                return None
            seen.add(current)
            properties = self.po.get(current)
            if not properties or set(properties) != {RDF.first, RDF.rest}:
                return None
            if len(properties[RDF.first]) != 1 or len(properties[RDF.rest]) != 1:
                return None
            if self.indeg[current] != 1:
                return None
            items.append(properties[RDF.first][0])
            current = properties[RDF.rest][0]
        return items

    # -- blocks -------------------------------------------------------

    def _predicates(self, subject: Node) -> list[URIRef]:
        """``rdf:type`` first — it is what you look for when scanning a
        block — then alphabetically."""
        return sorted(
            self.po[subject],
            key=lambda predicate: (predicate != RDF.type, self._term(predicate)),
        )

    def _object_lines(self, obj: Node, indent: str) -> list[str]:
        """Render one object. The first line is returned bare, for the
        caller to append to the predicate; every later line carries its
        own indentation."""
        if isinstance(obj, BNode) or obj == RDF.nil:
            items = self._as_collection(obj)
            if items is not None:
                return self._collection_lines(obj, items, indent)

        if self._inlinable(obj) and obj in self.po:
            self._open.add(obj)
            self.emitted.add(obj)
            inner = self._property_lines(obj, indent + INDENT)
            self._open.discard(obj)
            return ["[", *inner, indent + "]"]

        if isinstance(obj, BNode) and obj not in self.po:
            return ["[]"]  # referenced but never described
        return [self._term(obj)]

    def _collection_lines(
        self, head: Node, items: list[Node], indent: str
    ) -> list[str]:
        current = head
        while current != RDF.nil:  # the cells are spoken for now
            self.emitted.add(current)
            current = self.po[current][RDF.rest][0]

        if not items:
            return ["()"]
        groups = [self._object_lines(item, indent + INDENT) for item in items]
        flat = [group[0] for group in groups]
        if all(len(group) == 1 for group in groups) and sum(
            len(text) + 1 for text in flat
        ) <= INLINE_COLLECTION_WIDTH:
            return ["( " + " ".join(flat) + " )"]
        lines = ["("]
        for group in groups:
            lines.append(indent + INDENT + group[0])
            lines.extend(group[1:])
        lines.append(indent + ")")
        return lines

    def _property_lines(self, subject: Node, indent: str) -> list[str]:
        lines: list[str] = []
        for predicate in self._predicates(subject):
            text = "a" if predicate == RDF.type else self._term(predicate)
            for obj in sorted(self.po[subject][predicate], key=self._signature):
                first, *rest = self._object_lines(obj, indent)
                lines.append(f"{indent}{text} {first}")
                lines.extend(rest)
                lines[-1] += " ;"
        return lines

    def _block(self, subject: Node) -> str:
        self.emitted.add(subject)
        return "\n".join(
            [
                self._term(subject),
                *self._property_lines(subject, INDENT),
                INDENT + ".",
            ]
        )

    # -- document -----------------------------------------------------

    def _walk(self, subject: Node, order: list[Node]) -> None:
        if subject in self._queued or subject not in self.po:
            return
        if self._inlinable(subject):
            return  # written inside whoever references it
        self._queued.add(subject)
        order.append(subject)
        for predicate in self._predicates(subject):
            for obj in sorted(self.po[subject][predicate], key=self._signature):
                if isinstance(obj, (URIRef, BNode)):
                    self._walk(obj, order)

    def _order(self) -> list[Node]:
        order: list[Node] = []
        subjects = sorted(self.po, key=self._signature)
        if self.root is not None:
            self._walk(self.root, order)
        for subject in subjects:
            if self.indeg[subject] == 0:
                self._walk(subject, order)
        for subject in subjects:  # cycles, and anything only pointed into
            self._walk(subject, order)
        return self._anchor(order)

    def _anchor(self, fresh: list[Node]) -> list[Node]:
        """Splice new subjects into the previous document's order.

        Every new subject is placed after the last subject preceding it
        in reference order that the previous document also had, so it
        lands next to what introduced it. Subjects the previous document
        listed and this graph no longer has simply fall out.
        """
        if self.previous is None:
            return fresh
        current = set(fresh)
        survivors = [s for s in self.previous if s in current]
        kept = set(survivors)
        inserts: dict[Node | None, list[Node]] = {}
        anchor: Node | None = None
        for subject in fresh:
            if subject in kept:
                anchor = subject
            else:
                inserts.setdefault(anchor, []).append(subject)
        order = list(inserts.get(None, []))
        for subject in survivors:
            order.append(subject)
            order.extend(inserts.get(subject, []))
        return order

    def document(self) -> str:
        self.order = self._order()
        blocks = [
            self._block(subject)
            for subject in self.order
            if subject not in self.emitted
        ]
        # Backstop: a blank node in a reference cycle can be skipped by
        # the walk as "inlinable" and then never actually inlined.
        while True:
            remaining = [s for s in self.po if s not in self.emitted]
            if not remaining:
                break
            for subject in sorted(remaining, key=self._signature):
                if subject not in self.emitted:
                    blocks.append(self._block(subject))
                    self.order.append(subject)

        namespaces = dict(self.graph.namespaces())
        header = [
            f"@prefix {prefix}: <{namespaces[prefix]}> ."
            for prefix in sorted(self.used_prefixes)
            if prefix in namespaces
        ]
        return "\n\n".join(["\n".join(header), *blocks]) + "\n"


@dataclass
class Snapshot:
    """A rendered graph plus the subject order it came out in, which is
    what the next snapshot needs to keep its layout still."""

    text: str = ""
    order: list[Node] = field(default_factory=list)


def turtle_snapshot(
    graph: Graph, root: str | None = None, previous: Snapshot | None = None
) -> Snapshot:
    """The graph as Turtle, laid out to keep diffs against ``previous``
    confined to what actually changed."""
    writer = TurtleWriter(graph, root, previous.order if previous else None)
    return Snapshot(writer.document(), writer.order)


def diff_ops(before: str, after: str) -> dict[str, Any]:
    """A unified diff as flat ``[sign, line, old_no, new_no]`` rows.

    The whole diff is shipped, unchanged lines included, and the folding
    happens in the browser: a fold that has its own lines already can
    expand without a round trip, and at these graph sizes the payload is
    a few hundred kilobytes over loopback.

    ``autojunk`` is off — it treats lines recurring in more than 1% of a
    long input as noise, and Turtle is full of legitimately repeated
    lines like ``a sh:PropertyShape ;``.
    """
    old, new = before.splitlines(), after.splitlines()
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    ops: list[list[Any]] = []
    added = removed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset, line in enumerate(old[i1:i2]):
                ops.append(["=", line, i1 + offset + 1, j1 + offset + 1])
            continue
        for offset, line in enumerate(old[i1:i2]):
            ops.append(["-", line, i1 + offset + 1, None])
            removed += 1
        for offset, line in enumerate(new[j1:j2]):
            ops.append(["+", line, None, j1 + offset + 1])
            added += 1
    truncated = len(ops) > MAX_DIFF_LINES
    return {
        "ops": ops[:MAX_DIFF_LINES],
        "added": added,
        "removed": removed,
        "truncated": truncated,
    }


@dataclass
class Step:
    """One recorded compiler run, kept so the UI can page back through
    history instead of only showing the most recent delta."""

    index: int
    name: str
    kind: str  # "bootstrap" | "compile" | "finishing"
    added: list[list[str]] = field(default_factory=list)
    removed: list[list[str]] = field(default_factory=list)
    total_before: int = 0
    total_after: int = 0
    # The whole store as of this step. Kept as text rather than as a
    # graph copy: it is what both the diff and the store pane want, and
    # a rendered snapshot is cheaper than a second Graph per step.
    snapshot: Snapshot = field(default_factory=Snapshot)

    def as_json(self) -> dict[str, Any]:
        """Counts only. The triples themselves live in ``/api/turtle``
        and ``/api/diff``, fetched for the step being looked at — the
        state payload is refetched after every action and shipping every
        step's full delta in it made it grow with the run."""
        return {
            "index": self.index,
            "name": self.name,
            "kind": self.kind,
            "addedCount": len(self.added),
            "removedCount": len(self.removed),
            "totalBefore": self.total_before,
            "totalAfter": self.total_after,
        }


def _triples(reader: GraphReader) -> list[list[str]]:
    """Compacted ``[subject, predicate, object]`` rows, prefix-shortened
    via the graph's own prefix store so the UI shows ``tcs:instantiates``
    rather than the full IRI."""
    frame = reader.df
    if frame.empty:
        return []
    return [
        [str(row["sub"]), str(row["pred"]), str(row["obj"])]
        for _, row in frame.iterrows()
    ]


class SteppableGenerator(PipelineGenerator):
    """:class:`PipelineGenerator` with the fixpoint loop turned inside
    out: instead of looping to convergence, expose "what could run next"
    and "run exactly this one"."""

    def __init__(self, pipeline_id: str, catalog_graph: Graph) -> None:
        super().__init__(pipeline_id, catalog_graph)
        self.ran: set[type[Compiler]] = set()
        self.settling = False
        self.history: list[Step] = []
        self._initial: Snapshot | None = None
        self.reset()

    # -- lifecycle ---------------------------------------------------

    def reset(self) -> None:
        """Back to a freshly bootstrapped build."""
        self.compilers = {}
        self.ran = set()
        self.settling = False
        self.history = []
        self.build = Graph()
        self._bootstrap()

    def _bootstrap(self) -> None:
        """Run ``PipelineExtractor`` exactly as ``compile()`` does.

        Recorded as a step like any other, which makes the extraction
        itself inspectable: its ``removed_triples`` is everything the
        traversal-based narrowing dropped from the catalog, usually the
        single largest delta of a whole run.
        """
        extractor = PipelineExtractor(self.pipeline_id, self.catalog_graph)
        total_before = len(self.catalog_graph)
        self.build = extractor.compile()
        self.compilers[PipelineExtractor] = extractor
        self.ran.add(PipelineExtractor)
        self._record_creator(PipelineExtractor)
        self._set_finishing(False)
        self._record(extractor, "PipelineExtractor", "bootstrap", total_before)

    # -- turtle snapshots ---------------------------------------------

    @property
    def initial(self) -> Snapshot:
        """The catalog as it stood before extraction — the "before" side
        of the bootstrap diff. Computed once and kept: the catalog does
        not change, and it survives ``reset``."""
        if self._initial is None:
            self._initial = turtle_snapshot(self.catalog_graph, self.pipeline_id)
        return self._initial

    def _snapshot(self) -> Snapshot:
        """Rendered against the step before it, so a compiler that only
        adds a reference to an existing resource does not drag that
        resource's block across the document and into the diff."""
        previous = self.history[-1].snapshot if self.history else self.initial
        return turtle_snapshot(self.build, self.pipeline_id, previous)

    def turtle_at(self, index: int) -> str:
        return self.history[index].snapshot.text

    def diff_at(self, index: int) -> dict[str, Any]:
        """Step ``index`` as a diff of the whole store.

        Diffed from snapshots rather than from the compiler's own
        ``added_triples`` / ``removed_triples``, so this shows what the
        store actually gained — including the ``dct:creator`` provenance
        the generator writes *after* the compiler returns, which the
        compiler's own delta cannot see. That is also why the line
        counts here can run slightly ahead of the triple counts in the
        step header.
        """
        step = self.history[index]
        before = (
            self.history[index - 1].snapshot if index > 0 else self.initial
        ).text
        return diff_ops(before, step.snapshot.text)

    # -- introspection -----------------------------------------------

    def registry(self) -> list[type[Compiler]]:
        return list(Compiler._registry)

    def eligible(self) -> list[type[Compiler]]:
        """Compilers the real loop would pick on this iteration."""
        reader = GraphReader(self.build)
        return [
            cls
            for cls in Compiler._registry
            if cls not in self.ran and cls.applies_to(reader)
        ]

    # -- actions ------------------------------------------------------

    def step(self, name: str) -> Step:
        """Run the named compiler against the current build graph."""
        matches = [cls for cls in Compiler._registry if cls.__name__ == name]
        if not matches:
            raise KeyError(f"no compiler named {name!r}")
        cls = matches[0]
        if cls in self.ran:
            raise ValueError(f"{name} has already run")

        total_before = len(self.build)
        instance = cls(self.build)
        self.build = instance.compile()
        self.compilers[cls] = instance
        self.ran.add(cls)
        self._record_creator(cls)

        # Mirror the real loop: progress during a finishing pass drops
        # back to regular shaping so newly-eligible compilers still fire.
        if self.settling:
            self._set_finishing(False)
            self.settling = False

        return self._record(instance, name, "compile", total_before)

    def enter_finishing(self) -> Step:
        """Flip ``tcs:isFinishing`` to true — what the real loop does
        when a shaping scan finds nothing eligible, and what unlocks
        finalization-style compilers like ``DockerComposeCompiler``."""
        total_before = len(self.build)
        self._set_finishing(True)
        self.settling = True
        step = Step(
            index=len(self.history),
            name="(finishing pass)",
            kind="finishing",
            total_before=total_before,
            total_after=len(self.build),
            snapshot=self._snapshot(),
        )
        self.history.append(step)
        return step

    def _record(
        self, instance: Compiler, name: str, kind: str, total_before: int
    ) -> Step:
        """Snapshot a compiler's delta into history.

        Captured now rather than lazily: ``added_triples`` /
        ``removed_triples`` recompute a full graph difference on every
        access, which is fine once and wasteful on every page load.

        Note the delta is the *compiler's own* contribution — the
        ``dct:creator`` provenance triples are written by the generator
        afterwards, against ``self.build`` rather than the instance's
        readers, so they deliberately do not show up here.
        """
        step = Step(
            index=len(self.history),
            name=name,
            kind=kind,
            added=_triples(instance.added_triples),
            removed=_triples(instance.removed_triples),
            total_before=total_before,
            total_after=len(self.build),
            snapshot=self._snapshot(),
        )
        self.history.append(step)
        return step

    # -- serialization ------------------------------------------------

    def state(self) -> dict[str, Any]:
        eligible = {cls.__name__ for cls in self.eligible()}
        ran_order = [cls.__name__ for cls in self.compilers]
        return {
            "pipelineId": self.pipeline_id,
            "settling": self.settling,
            "totalTriples": len(self.build),
            "ran": ran_order,
            "compilers": [
                {
                    "name": cls.__name__,
                    "module": cls.__module__.rsplit(".", 1)[-1],
                    "ran": cls in self.ran,
                    "eligible": cls.__name__ in eligible,
                    "order": ran_order.index(cls.__name__)
                    if cls.__name__ in ran_order
                    else None,
                }
                for cls in self.registry()
            ],
            "history": [step.as_json() for step in self.history],
        }


# ----------------------------------------------------------------------
# HTTP layer
# ----------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    generator: SteppableGenerator  # injected by ``serve``

    # ThreadingHTTPServer hands every request its own thread, and the
    # generator is one mutable object shared between them. Two overlapping
    # resets interleave as clear, clear, append, append and leave two
    # bootstrap steps in the history; a read landing mid-step sees a
    # graph that is neither the before nor the after. Stepping is far too
    # slow to race safely and far too cheap to be worth finer locking, so
    # every request takes the same lock.
    lock = threading.Lock()

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        pass  # keep the console readable; errors still surface in responses

    def _send(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _step_param(self, query: dict[str, list[str]]) -> int | None:
        """The ``?step=`` index, clamped to the recorded history.

        The UI can ask for a step that a concurrent ``reset`` just threw
        away; that is a stale request, not an error worth a traceback.
        """
        history = self.generator.history
        if not history:
            return None
        try:
            index = int(query.get("step", ["-1"])[0])
        except ValueError:
            index = -1
        if index < 0:
            index += len(history)
        return max(0, min(index, len(history) - 1))

    def do_GET(self) -> None:  # noqa: N802
        with self.lock:
            self._get()

    def _get(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path in ("/", "/index.html"):
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/api/state":
            self._send(self.generator.state())
        elif parsed.path == "/api/turtle":
            index = self._step_param(query)
            self._send(
                {
                    "step": index,
                    "turtle": "" if index is None else self.generator.turtle_at(index),
                }
            )
        elif parsed.path == "/api/diff":
            index = self._step_param(query)
            payload = (
                {"ops": [], "added": 0, "removed": 0, "truncated": False}
                if index is None
                else self.generator.diff_at(index)
            )
            self._send({"step": index, **payload})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        with self.lock:
            self._post()

    def _post(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        try:
            if self.path == "/api/step":
                self.generator.step(payload["compiler"])
            elif self.path == "/api/finishing":
                self.generator.enter_finishing()
            elif self.path == "/api/reset":
                self.generator.reset()
            else:
                self._send({"error": "not found"}, 404)
                return
        except Exception as exc:  # surfaced in the UI, not the console
            import traceback

            self._send(
                {
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                    "state": self.generator.state(),
                },
                500,
            )
            return
        self._send(self.generator.state())


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Compiler Inspector</title>
<style>
  :root {
    --bg: #ffffff; --panel: #f6f7f9; --border: #d8dce2; --text: #1c2024;
    --muted: #666f7a; --accent: #b46504; --added: #1a7f37; --added-bg: #e8f5ec;
    --removed: #b8302a; --removed-bg: #fdecea; --chip: #e6e9ed;
    --iri: #0a66a8; --lit: #a3306a; --bnode: #6d4bb0;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #16181c; --panel: #1e2126; --border: #333941; --text: #e6e8eb;
      --muted: #98a1ac; --accent: #e39a4a; --added: #4ec36f; --added-bg: #16301f;
      --removed: #f0736c; --removed-bg: #34191a; --chip: #2a2f36;
      --iri: #79c0ff; --lit: #f2a0c8; --bnode: #c3aaff;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font: 14px/1.5 ui-sans-serif, system-ui, -apple-system, sans-serif; }
  header { padding: 12px 18px; border-bottom: 1px solid var(--border);
           display: flex; gap: 16px; align-items: baseline; flex-wrap: wrap; }
  h1 { font-size: 15px; margin: 0; font-weight: 650; }
  .muted { color: var(--muted); }
  .wrap { display: grid; grid-template-columns: 300px 1fr; gap: 0;
          height: calc(100vh - 50px); }
  aside { border-right: 1px solid var(--border); overflow-y: auto;
          padding: 14px; background: var(--panel); }
  main { overflow-y: auto; padding: 14px 18px; }
  button { font: inherit; padding: 5px 10px; border-radius: 6px;
           border: 1px solid var(--border); background: var(--bg);
           color: var(--text); cursor: pointer; }
  button:hover:not(:disabled) { border-color: var(--accent); }
  button:disabled { opacity: .45; cursor: not-allowed; }
  button.go { border-color: var(--accent); color: var(--accent); font-weight: 600; }
  .comp { display: flex; align-items: center; gap: 8px; padding: 5px 0;
          border-bottom: 1px solid var(--border); }
  .comp:last-child { border-bottom: 0; }
  .comp .nm { flex: 1; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
              font-size: 12px; word-break: break-word; }
  .comp.done .nm { color: var(--muted); text-decoration: line-through; }
  .ord { font-size: 11px; background: var(--chip); border-radius: 10px;
         padding: 1px 7px; color: var(--muted); }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .05em;
       color: var(--muted); margin: 0 0 8px; }
  section { margin-bottom: 22px; }
  .scroll { max-height: 46vh; overflow: auto; border: 1px solid var(--border);
            border-radius: 6px; }
  /* Every line is its own flex row inside a max-content-wide column, so
     the +/− backgrounds still span the full width once a long IRI has
     pushed the box into horizontal scrolling. */
  .code { min-width: max-content; font-size: 12px; line-height: 1.5;
          font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  .ln { display: flex; }
  .ln .no { flex: 0 0 46px; text-align: right; padding-right: 10px;
            color: var(--muted); user-select: none; }
  .ln .sg { flex: 0 0 14px; font-weight: 700; }
  .ln .txt { flex: 1 0 auto; white-space: pre; padding-right: 16px; }
  .ln.add { background: var(--added-bg); }
  .ln.del { background: var(--removed-bg); }
  .ln.add .sg { color: var(--added); }
  .ln.del .sg { color: var(--removed); }
  .ln.sub .txt { font-weight: 650; }
  .ln.pfx .txt { color: var(--muted); }
  .qn { color: var(--text); }
  .iri { color: var(--iri); }
  .lit { color: var(--lit); }
  .bn { color: var(--bnode); }
  .kw { color: var(--accent); font-weight: 600; }
  /* sticky-left so the label stays put when the pane is scrolled right */
  .fold { cursor: pointer; color: var(--muted); background: var(--panel);
          padding: 1px 10px; position: sticky; left: 0;
          border-top: 1px solid var(--border);
          border-bottom: 1px solid var(--border); }
  .fold:hover { color: var(--accent); }
  .empty { padding: 10px; color: var(--muted); }
  body.busy { cursor: progress; }
  body.busy button { opacity: .45; pointer-events: none; }
  input[type=search] { font: inherit; padding: 5px 9px; width: 100%;
      max-width: 380px; border: 1px solid var(--border); border-radius: 6px;
      background: var(--bg); color: var(--text); }
  .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
         margin-bottom: 8px; }
  .pill { font-size: 12px; background: var(--chip); border-radius: 10px;
          padding: 2px 9px; }
  .err { background: var(--removed-bg); color: var(--removed);
         border: 1px solid var(--removed); border-radius: 6px;
         padding: 10px 12px; white-space: pre-wrap; font-size: 12px;
         font-family: ui-monospace, monospace; margin-bottom: 14px; }
  .hist { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
  .hist button { font-size: 12px; }
  .hist button[aria-pressed=true] { border-color: var(--accent);
                                    background: var(--chip); font-weight: 600; }
</style>
</head>
<body>
<header>
  <h1>Compiler Inspector</h1>
  <span class="muted" id="pid"></span>
  <span class="pill" id="total"></span>
  <span class="pill" id="finishing" hidden>tcs:isFinishing true</span>
  <span style="flex:1"></span>
  <button id="reset">Reset to bootstrap</button>
</header>
<div class="wrap">
  <aside>
    <h2>Compilers</h2>
    <div id="list"></div>
    <button id="finish" style="margin-top:12px;width:100%">
      Enter finishing pass
    </button>
  </aside>
  <main>
    <div id="err" class="err" hidden></div>
    <section>
      <h2>Steps</h2>
      <div class="hist" id="hist"></div>
      <div class="row">
        <strong id="stepName"></strong>
        <span class="pill" id="stepCounts"></span>
        <span class="pill" id="diffCounts"></span>
        <button id="unfold">Expand all</button>
      </div>
      <div class="scroll" id="delta"></div>
    </section>
    <section>
      <h2>Store</h2>
      <div class="row">
        <input type="search" id="filter" placeholder="filter blocks (substring of the subject block)">
        <span class="muted" id="shown"></span>
      </div>
      <div class="scroll" id="store"></div>
    </section>
  </main>
</div>
<script>
let state = null, selected = null, expandAll = false, busy = false;
let diffs = {}, turtles = {}, opened = {};
const CONTEXT = 3, FOLD_MIN = 8, MAX_LINES = 8000;

const esc = s => s.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

// Turtle in one pass: long string forms first so a '#' or a ':' inside
// an IRI or a literal is never mistaken for a comment or a prefix.
// The long-quote form is written "{3} — a literal triple quote here
// would close the Python string this page lives in.
const TTL_RE = /("{3}[\s\S]*?"{3}|"(?:[^"\\]|\\.)*"(?:@[\w-]+|\^\^\S+)?|<[^>\s]*>|_:[^\s;,)\]]+|[A-Za-z][\w.-]*:[^\s;,()\[\]"]*|\b(?:a|true|false)\b|\b\d[\d.eE+-]*\b)/g;

function hl(text) {
  let out = '', last = 0, m;
  TTL_RE.lastIndex = 0;
  while ((m = TTL_RE.exec(text)) !== null) {
    const t = m[0], c = t[0];
    out += esc(text.slice(last, m.index));
    out += `<span class="${
      c === '"' ? 'lit' : c === '<' ? 'iri' : c === '_' ? 'bn'
      : (c >= '0' && c <= '9') ? 'lit' : t.includes(':') ? 'qn' : 'kw'
    }">${esc(t)}</span>`;
    last = m.index + t.length;
  }
  return out + esc(text.slice(last));
}

// The writer puts subjects at column 0 and everything else indented, so
// the shape of a line is enough to know what it is.
const lineClass = text =>
  text.startsWith('@prefix') ? 'pfx' : /^[^\s@]/.test(text) ? 'sub' : '';

const diffLine = ([sign, text, oldNo, newNo]) =>
  `<div class="ln ${sign === '+' ? 'add' : sign === '-' ? 'del' : ''} ${lineClass(text)}">` +
  `<span class="no">${oldNo || ''}</span><span class="no">${newNo || ''}</span>` +
  `<span class="sg">${sign === '=' ? '' : sign}</span>` +
  `<span class="txt">${hl(text)}</span></div>`;

const codeLine = (no, text) =>
  `<div class="ln ${lineClass(text)}"><span class="no">${no}</span>` +
  `<span class="txt">${hl(text)}</span></div>`;

const fold = (key, count) =>
  `<div class="fold" data-fold="${key}">⋯ ${count} unchanged lines</div>`;

// Git-style folding: keep CONTEXT lines either side of every change and
// roll the rest up into a stub. The lines are already here, so opening
// one is a re-render rather than a round trip.
function diffHtml(ops) {
  const open = opened[selected] || (opened[selected] = new Set());
  const out = [];
  let i = 0;
  while (i < ops.length) {
    if (ops[i][0] !== '=') { out.push(diffLine(ops[i])); i++; continue; }
    let j = i;
    while (j < ops.length && ops[j][0] === '=') j++;
    const key = i + ':' + j;
    if (j - i <= FOLD_MIN || expandAll || open.has(key)) {
      for (let k = i; k < j; k++) out.push(diffLine(ops[k]));
    } else {
      const head = i === 0 ? 0 : CONTEXT, tail = j === ops.length ? 0 : CONTEXT;
      for (let k = i; k < i + head; k++) out.push(diffLine(ops[k]));
      out.push(fold(key, j - i - head - tail));
      for (let k = j - tail; k < j; k++) out.push(diffLine(ops[k]));
    }
    i = j;
  }
  return out.join('');
}

// Stepping and resetting take a second or two, and a second click in
// that window used to queue a second mutation — two overlapping resets
// leave two bootstrap steps in the history. The server locks as well;
// this is what stops the click being sent at all.
async function call(path, body) {
  if (busy) return;
  busy = true;
  document.body.classList.add('busy');
  try {
    await send(path, body);
  } finally {
    busy = false;
    document.body.classList.remove('busy');
  }
}

async function send(path, body) {
  const r = await fetch(path, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {})
  });
  const data = await r.json();
  const err = document.getElementById('err');
  if (!r.ok) {
    err.hidden = false;
    err.textContent = data.traceback || data.error;
    if (data.state) { state = data.state; selected = null; invalidate(); render(); }
    return;
  }
  err.hidden = true;
  state = data; selected = null; invalidate(); render();
}

function invalidate() { diffs = {}; turtles = {}; opened = {}; }

async function load() {
  state = await (await fetch('/api/state')).json();
  render();
}

function render() {
  document.getElementById('pid').textContent = state.pipelineId;
  document.getElementById('total').textContent = state.totalTriples + ' triples';
  document.getElementById('finishing').hidden = !state.settling;

  document.getElementById('list').innerHTML = state.compilers.map(c => `
    <div class="comp ${c.ran ? 'done' : ''}">
      ${c.order !== null ? `<span class="ord">${c.order}</span>` : ''}
      <span class="nm" title="${esc(c.module)}">${esc(c.name)}</span>
      ${c.ran ? '' :
        `<button class="${c.eligible ? 'go' : ''}" data-run="${esc(c.name)}"
           ${c.eligible ? '' : 'disabled'}
           title="${c.eligible ? 'applies_to() is true' : 'applies_to() is false'}"
         >Run</button>`}
    </div>`).join('');
  document.querySelectorAll('[data-run]').forEach(b =>
    b.onclick = () => call('/api/step', {compiler: b.dataset.run}));

  const h = state.history;
  if (selected === null) selected = h.length - 1;
  document.getElementById('hist').innerHTML = h.map(s =>
    `<button data-step="${s.index}" aria-pressed="${s.index === selected}">
       ${s.index}. ${esc(s.name)}</button>`).join('');
  document.querySelectorAll('[data-step]').forEach(b =>
    b.onclick = () => { selected = +b.dataset.step; expandAll = false; render(); });

  const step = h[selected];
  if (step) {
    document.getElementById('stepName').textContent = step.name;
    document.getElementById('stepCounts').textContent =
      `compiler +${step.addedCount} / −${step.removedCount} triples  ·  ` +
      `store ${step.totalBefore} → ${step.totalAfter}`;
  }
  fetchStep();
}

// Diff and store are per step and much larger than the state payload,
// so they are fetched on demand and cached until an action invalidates
// them. `selected` is rechecked after the awaits: clicking through
// history faster than the fetches resolve must not paint a stale step.
async function fetchStep() {
  const step = selected;
  if (step === null || step < 0) {
    document.getElementById('delta').innerHTML = '';
    document.getElementById('store').innerHTML = '';
    return;
  }
  if (!(step in diffs)) {
    const d = await (await fetch('/api/diff?step=' + step)).json();
    diffs[step] = d;
  }
  if (!(step in turtles)) {
    const t = await (await fetch('/api/turtle?step=' + step)).json();
    turtles[step] = t.turtle;
  }
  if (step !== selected) return;
  renderDiff();
  renderStore();
}

function renderDiff() {
  const d = diffs[selected];
  if (!d) return;
  // The bootstrap step is diffed against the catalog, not against an
  // earlier build, so it opens on a wall of deletions: everything the
  // extractor's traversal did not reach. Say so, or it reads as damage.
  const step = state.history[selected];
  document.getElementById('diffCounts').textContent =
    `+${d.added} / −${d.removed} lines` +
    (step && step.kind === 'bootstrap' ? ' — vs. the catalog' : '') +
    (d.truncated ? ' (truncated)' : '');
  document.getElementById('unfold').textContent =
    expandAll ? 'Collapse unchanged' : 'Expand all';
  document.getElementById('delta').innerHTML = (d.added + d.removed)
    ? `<div class="code">${diffHtml(d.ops)}</div>`
    : '<p class="empty">No triples changed.</p>';
  document.querySelectorAll('[data-fold]').forEach(el =>
    el.onclick = () => {
      opened[selected].add(el.dataset.fold);
      renderDiff();
    });
}

// Filtering keeps whole subject blocks rather than matching lines: a
// lone `sh:path tcs:x ;` out of context says nothing about who it
// belongs to, and dropping lines would stop the pane being valid Turtle.
function renderStore() {
  const q = document.getElementById('filter').value.toLowerCase();
  const doc = turtles[selected] || '';
  const blocks = doc.split('\n\n');
  const out = [];
  let no = 1, kept = 0, clipped = false;
  blocks.forEach((block, index) => {
    const lines = block.split('\n');
    const start = no;
    no += lines.length + 1;  // +1 for the blank line between blocks
    if (index > 0 && q && !block.toLowerCase().includes(q)) return;
    if (index > 0) kept++;
    if (out.length > MAX_LINES) { clipped = true; return; }
    lines.forEach((line, offset) => out.push(codeLine(start + offset, line)));
    out.push(codeLine('', ''));
  });
  document.getElementById('shown').textContent =
    `${kept} of ${blocks.length - 1} subject blocks` +
    (clipped ? ' — output clipped, narrow the filter' : '') +
    `  ·  store after step ${selected}`;
  document.getElementById('store').innerHTML = `<div class="code">${out.join('')}</div>`;
}

document.getElementById('filter').oninput = renderStore;
document.getElementById('reset').onclick = () => call('/api/reset');
document.getElementById('finish').onclick = () => call('/api/finishing');
document.getElementById('unfold').onclick = () => {
  expandAll = !expandAll;
  renderDiff();
};
load();
</script>
</body>
</html>
"""


def serve(pipeline_id: str, port: int, rdfc_root: str | None) -> None:
    if rdfc_root:
        # Opt in to RdfcImportExpander, which is inert until its roots
        # are configured. The prefix is the container-side path the
        # catalog's owl:imports are written against.
        #
        # Looked up dynamically rather than imported: that compiler is
        # not on every branch, and a debugging tool should still start
        # without it rather than fail at import time.
        expander = getattr(compilers_pkg, "RdfcImportExpander", None)
        if expander is None:
            print(
                "warning: --rdfc-root given but this checkout has no "
                "RdfcImportExpander; owl:imports will not be expanded."
            )
        else:
            expander.import_roots = {PUBLIC_ID: str(Path(rdfc_root).resolve())}
            print(
                f"RdfcImportExpander roots: {PUBLIC_ID} -> "
                f"{Path(rdfc_root).resolve()}"
            )

    print("Loading catalog and applying inference rules...")
    Handler.generator = SteppableGenerator(pipeline_id, load_catalog())
    print(f"Bootstrapped {pipeline_id}: {len(Handler.generator.build)} triples")
    print(f"  http://127.0.0.1:{port}\n")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--pipeline", default="demo:DishacledPipeline")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--rdfc-root",
        default=None,
        help="local directory standing in for the container's "
        "/workspace/pipeline (enables RdfcImportExpander)",
    )
    args = parser.parse_args()
    try:
        serve(args.pipeline, args.port, args.rdfc_root)
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
