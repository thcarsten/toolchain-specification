"""Drive one compilation run from a :class:`CompilationConfig`.

``CompilationRunner`` is the single, non-abstract driver behind both
:class:`PipelineGenerator` and :class:`PipelineValidator`. Given a
config, it:

1. **Loads the catalog** — parses :attr:`CompilationConfig.graph_files`
   into an rdflib graph, then applies
   :attr:`CompilationConfig.inference_files` on top. Skipped entirely
   if the config supplies a pre-loaded :attr:`CompilationConfig.graph`.
2. **Bootstraps the build** — instantiates
   :attr:`CompilationConfig.bootstrap_compiler` (currently
   :class:`PipelineSeeder`) with ``(pipeline_id, catalog_graph)`` so
   it can seed the ``tcs:PipelineBuild`` node and name blank-node
   subjects.
3. **Runs the fixpoint loop** — on every iteration, scans
   :attr:`CompilationConfig.loop_compilers`, filters out the ones that
   already ran, and evaluates each remaining class's
   :meth:`Compiler.applies_to` against the current build graph. The
   loop terminates when a full scan finds nothing eligible.
4. **Runs the finalize compilers** — instantiates each class in
   :attr:`CompilationConfig.finalize_compilers` with ``(graph,)`` and
   runs it in list order, unconditionally.

After every compiler runs (bootstrap, loop, or finalize), a
``dct:creator`` provenance triple is attached to the build so
subsequent :meth:`applies_to` invocations can inspect which compilers
have already contributed.

Instances that ran are kept on :attr:`compilers`, keyed by class, in
insertion order — so ``list(runner.compilers)`` doubles as the run
order and each compiler's intermediate state stays inspectable::

    runner = PipelineGenerator(pipeline_id, catalog_graph)
    build  = runner.compile()

    runner.compilers[LdioConfigCompiler].df_steps
    runner.compilers[PipelineAssembler].added_triples.df
"""

import pandas as pd
from rdflib import Graph, URIRef
from rdfine import GraphReader, receive_first

from .base import Compiler
from .config import CompilationConfig


class CompilationRunner:
    def __init__(self, config: CompilationConfig) -> None:
        self.config = config
        # Populated by ``compile``.
        self.catalog_graph: Graph = Graph()
        self.compilers: dict[type[Compiler], Compiler] = {}
        self.build: Graph = Graph()

    def compile(self) -> Graph:
        self.compilers = {}
        self._load_catalog()
        self._bootstrap()
        self._run_fixpoint_loop()
        self._finalize()
        return self.build

    # ------------------------------------------------------------------
    # Compilation phases.
    # ------------------------------------------------------------------

    def _load_catalog(self) -> None:
        """Parse ``graph_files`` and apply ``inference_files`` — unless
        the config already supplies a pre-loaded graph, in which case
        both lists are ignored. The pre-loaded graph is the escape hatch
        tests use to skip disk IO on a graph they built in-memory.
        """
        if self.config.graph is not None:
            self.catalog_graph = self.config.graph
            return
        catalog_graph = Graph()
        for path in self.config.graph_files:
            catalog_graph.parse(str(path), publicID=self.config.public_id)
        reader = GraphReader(catalog_graph)
        for path in self.config.inference_files:
            reader = reader.infer(str(path))
        self.catalog_graph = reader.graph

    def _bootstrap(self) -> None:
        """Instantiate the bootstrap compiler with ``(pipeline_id, graph)``.

        The bootstrap compiler is the only one whose constructor needs
        the pipeline id, and it is the one that seeds the
        ``tcs:PipelineBuild`` node — without which provenance would
        have nowhere to attach in the loop below.
        """
        cls = self.config.bootstrap_compiler
        instance = cls(self.config.pipeline_id, self.catalog_graph)
        self.build = instance.compile()
        self.compilers[cls] = instance
        self._record_creator(cls)

    def _run_fixpoint_loop(self) -> None:
        """Scan ``config.loop_compilers`` until nothing is eligible.

        Ordering emerges from each compiler's ``applies_to`` trigger:
        a compiler runs as soon as its trigger becomes true against
        the current build graph, and does not run again once it has.
        """
        ran: set[type[Compiler]] = set(self.compilers)
        while True:
            reader = GraphReader(self.build)
            eligible = [
                cls
                for cls in self.config.loop_compilers
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

    def _finalize(self) -> None:
        """Instantiate and run each class in ``config.finalize_compilers``
        unconditionally, in list order. Their ``applies_to`` is not
        consulted — being listed here is itself the trigger.
        """
        for cls in self.config.finalize_compilers:
            instance = cls(self.build)
            self.build = instance.compile()
            self.compilers[cls] = instance
            self._record_creator(cls)

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

        Called immediately after each compiler finishes, so every
        subsequent ``applies_to`` invocation can observe which
        compilers have already run by querying ``dct:creator`` on the
        build.
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
