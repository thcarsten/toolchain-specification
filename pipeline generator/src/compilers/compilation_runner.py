"""Drive one compilation run from a :class:`CompilationConfig`.

``CompilationRunner`` is the driver behind both
:func:`PipelineGenerator` and :func:`PipelineValidator`. Given a
pipeline id and a config, it:

1. **Loads the graph** — parses every path in
   :attr:`CompilationConfig.graph_files` into one rdflib graph, then
   applies :attr:`CompilationConfig.inference_files` on top via
   :meth:`GraphReader.infer`. Pipeline definitions live in
   ``graph_files`` alongside the catalog.
2. **Posts a compilation request** — attaches a fresh
   ``tcs:CompilationRequest`` node to the graph, carrying the
   ``tcs:targetPipeline`` triple that the ``pipeline_id`` handed to
   ``__init__`` describes. This is how the runner communicates the
   run-scoped parameter to the compilers, so every compiler shares
   the same one-arg ``__init__(graph)`` signature.
3. **Runs the fixpoint** — on every iteration, scans
   :attr:`CompilationConfig.compilers`, filters out the ones that
   already ran, and evaluates each remaining class's
   :meth:`Compiler.applies_to` against the current graph. The loop
   terminates when a full scan finds nothing eligible. Ordering
   emerges from each compiler's trigger.
4. **Switches to the finalize phase and re-runs the fixpoint** —
   attaches ``<request> tcs:runPhase tcs:FinalizePhase`` so
   finalize-phase compilers (currently
   :class:`DockerComposeCompiler`, :class:`ValidationReportCompiler`)
   become eligible, then loops again until nothing new fires. The
   ``ran`` bookkeeping is shared across both passes, so no compiler
   fires twice.
5. **Detaches the request** — strips every triple whose subject is
   the request node so the returned graph carries no runner-internal
   state.

Instances that ran are kept on :attr:`compilers`, keyed by class, in
insertion order — so ``list(runner.compilers)`` doubles as the run
order and each compiler's intermediate state stays inspectable::

    runner = PipelineGenerator("demo:DishacledPipeline")
    build  = runner.compile()

    runner.compilers[LdioConfigCompiler].df_steps
    runner.compilers[PipelineAssembler].added_triples.df

Provenance (``dct:creator`` on the build) is written by
:meth:`_record_creator` immediately after each compiler finishes, so
subsequent :meth:`Compiler.applies_to` invocations can inspect which
compilers have already contributed.
"""

import pandas as pd
from dataclasses import dataclass, field
from pathlib import Path

from rdflib import Graph, URIRef
from rdfine import GraphReader, receive_first

from .compiler_abc import Compiler

#: IRI of the single ``tcs:CompilationRequest`` node the runner attaches.
#: Fresh per :meth:`CompilationRunner.compile` call.
_REQUEST_IRI = ":compilation_request"


@dataclass(frozen=True)
class CompilationConfig:
    """The mode a :class:`CompilationRunner` runs in.

    Frozen so a config can be safely reused across many runs against
    the same catalog. The per-run parameter (the pipeline id) is
    passed directly to :class:`CompilationRunner`.

    :attr:`graph_files` is the input to the runner: every ttl listed
    is parsed into one rdflib graph, in order. Pipeline definitions
    are ordinary members of that list. :attr:`inference_files` are
    applied on top via ``GraphReader.infer()``.

    :attr:`compilers` is scanned by the runner in a fixpoint loop.
    Each compiler fires as soon as its :meth:`Compiler.applies_to`
    trigger becomes true against the growing graph, and does not run
    again. When the loop settles, the runner switches to the finalize
    phase (see :meth:`CompilationRunner.compile`) and re-runs the
    fixpoint with the phase triple attached, so finalize-phase
    compilers become eligible.
    """

    compilers: list[type[Compiler]]
    graph_files: list[Path] = field(default_factory=list)
    inference_files: list[Path] = field(default_factory=list)
    public_id: str = "file:///workspace/pipeline/"


class CompilationRunner:
    def __init__(self, pipeline_id: str, config: CompilationConfig) -> None:
        self.pipeline_id = pipeline_id
        self.config = config
        # Populated by ``compile``.
        self.catalog_graph: Graph = Graph()
        self.compilers: dict[type[Compiler], Compiler] = {}
        self.build: Graph = Graph()

    def compile(self) -> Graph:
        self.compilers = {}
        self.load_graph()
        self.attach_compilation_request()
        self.run_fixpoint()
        self.set_phase("tcs:FinalizePhase")
        self.run_fixpoint()
        self.detach_compilation_request()
        return self.build

    # ------------------------------------------------------------------
    # Compilation phases.
    # ------------------------------------------------------------------

    def load_graph(self) -> None:
        """Parse ``graph_files`` into one graph, apply ``inference_files``
        on top, and seed :attr:`build` with the result."""
        catalog_graph = Graph()
        for path in self.config.graph_files:
            catalog_graph.parse(str(path), publicID=self.config.public_id)
        reader = GraphReader(catalog_graph)
        for path in self.config.inference_files:
            reader = reader.infer(str(path))
        self.catalog_graph = reader.graph
        self.build = self.catalog_graph

    def run_fixpoint(self) -> None:
        """Scan ``config.compilers`` until nothing is eligible.

        Ordering emerges from each compiler's ``applies_to`` trigger:
        a compiler runs as soon as its trigger becomes true against
        the current graph, and does not run again once it has. Called
        twice per :meth:`compile` — once before the finalize phase is
        set, once after — with a shared ``ran`` set (via
        ``self.compilers``) so no compiler fires twice.
        """
        ran: set[type[Compiler]] = set(self.compilers)
        while True:
            reader = GraphReader(self.build)
            eligible = [
                cls
                for cls in self.config.compilers
                if cls not in ran and cls.applies_to(reader)
            ]
            if not eligible:
                break
            for cls in eligible:
                instance = cls(self.build)
                self.build = instance.compile()
                self.compilers[cls] = instance
                ran.add(cls)
                self._record_creator(cls)

    # ------------------------------------------------------------------
    # Compilation-request scaffolding.
    # ------------------------------------------------------------------

    def attach_compilation_request(self) -> None:
        """Attach ``<request> a tcs:CompilationRequest ; tcs:targetPipeline <pid>``.

        This is the runner-to-compiler handshake: any compiler that
        needs a run-scoped parameter (currently only
        :class:`PipelineSeeder`, which needs to know which pipeline
        definition to seed a build from) reads it off this node
        instead of taking a special constructor argument. Stripped by
        :meth:`detach_compilation_request` before :meth:`compile`
        returns.
        """
        reader = GraphReader(self.build)
        rows = [
            {
                "sub": _REQUEST_IRI,
                "pred": "rdf:type",
                "obj": "tcs:CompilationRequest",
                "sub_type": URIRef,
                "obj_type": URIRef,
            },
            {
                "sub": _REQUEST_IRI,
                "pred": "tcs:targetPipeline",
                "obj": self.pipeline_id,
                "sub_type": URIRef,
                "obj_type": URIRef,
            },
        ]
        new_graph = GraphReader(
            pd.DataFrame.from_records(rows),
            prefix_store=reader.prefix_store,
        ).graph
        self.build = reader.add(new_graph).graph

    def detach_compilation_request(self) -> None:
        """Strip every triple whose subject is the request node.

        Covers the type + targetPipeline the runner attaches at the
        start, plus any ``tcs:runPhase`` still on it. The build graph
        the user gets back carries no runner-internal state.
        """
        reader = GraphReader(self.build)
        request_uri = URIRef(reader.prefix_store.expand_string(_REQUEST_IRI))
        to_remove = Graph()
        for triple in self.build:
            if triple[0] == request_uri:
                to_remove.add(triple)
        if len(to_remove) == 0:
            return
        self.build = reader.remove(to_remove).graph

    def set_phase(self, phase_iri: str | None) -> None:
        """Telegraph the runner's current phase on the request node.

        Attaches (or clears, if ``phase_iri`` is None) a single
        ``<request> tcs:runPhase <phase_iri>`` triple. Any prior phase
        triple is removed first, so at most one is on the request at
        a time. Finalize-only compilers gate their ``applies_to`` on
        ``<?> tcs:runPhase tcs:FinalizePhase``.
        """
        reader = GraphReader(self.build)
        # Strip whatever runPhase is currently on the request. Cheaper
        # than tracking the previous value on the runner and avoids a
        # stale-value bug if a compiler ever writes its own phase.
        request_uri = URIRef(reader.prefix_store.expand_string(_REQUEST_IRI))
        phase_pred = URIRef(reader.prefix_store.expand_string("tcs:runPhase"))
        to_remove = Graph()
        for triple in self.build:
            if triple[0] == request_uri and triple[1] == phase_pred:
                to_remove.add(triple)
        if len(to_remove) > 0:
            self.build = reader.remove(to_remove).graph
            reader = GraphReader(self.build)
        if phase_iri is None:
            return
        rows = [
            {
                "sub": _REQUEST_IRI,
                "pred": "tcs:runPhase",
                "obj": phase_iri,
                "sub_type": URIRef,
                "obj_type": URIRef,
            }
        ]
        new_graph = GraphReader(
            pd.DataFrame.from_records(rows),
            prefix_store=reader.prefix_store,
        ).graph
        self.build = reader.add(new_graph).graph

    # ------------------------------------------------------------------
    # Provenance & internal helpers.
    # ------------------------------------------------------------------

    def _lookup_build_id(self) -> str:
        """Locate the ``tcs:PipelineBuild`` node currently in the graph."""
        reader = GraphReader(self.build)
        return receive_first(
            reader.filter(pred="rdf:type", obj="tcs:PipelineBuild").df["sub"],
        )

    def _record_creator(self, cls: type[Compiler]) -> None:
        """Attach ``<build> dct:creator <compiler>`` and type the compiler.

        Called immediately after each compiler finishes inside
        :meth:`run_fixpoint`, so every subsequent ``applies_to``
        invocation can observe which compilers have already run by
        querying ``dct:creator`` on the build.
        """
        build_id = self._lookup_build_id()
        iri = cls.compiler_iri()
        reader = GraphReader(self.build)
        rows = [
            {
                "sub": build_id,
                "pred": "dct:creator",
                "obj": iri,
                "sub_type": URIRef,
                "obj_type": URIRef,
            },
            {
                "sub": iri,
                "pred": "rdf:type",
                "obj": "tcs:Compiler",
                "sub_type": URIRef,
                "obj_type": URIRef,
            },
        ]
        new_graph = GraphReader(
            pd.DataFrame.from_records(rows),
            prefix_store=reader.prefix_store,
        ).graph
        self.build = reader.add(new_graph).graph
