"""Orchestrate the compiler registry into a single end-to-end build."""

from rdflib import Graph
from rdfine import GraphReader

from .base import Compiler
from .pipeline_assembler import PipelineAssembler
from .pipeline_extractor import PipelineExtractor


class PipelineGenerator:
    """Compile a complete ``tcs:PipelineBuild`` from a catalog graph.

    The generator runs in two phases:

    1. **Seed** — :class:`PipelineExtractor` walks the catalog
       using the supplied ``pipeline_id`` to produce the initial build
       graph; :class:`PipelineAssembler` then materializes the
       ``tcs:PipelineBuild`` node and its container/step/config
       skeleton. Both run unconditionally and in this fixed order
       because every other compiler depends on the structure they
       produce. They are not part of the registry dispatch.
    2. **Dispatch** — every other concrete :class:`Compiler` in the
       registry is consulted via :meth:`Compiler.applies_to`. Those
       that apply are run in :class:`Tier` order against the (now
       growing) build graph.

    Compilers register themselves on import via
    :meth:`Compiler.__init_subclass__`, so adding a new compiler is a
    matter of dropping a new file in the ``compilers`` package and
    importing it from ``compilers/__init__.py`` \u2014 the generator never
    needs editing.

    The instances that ran are kept on :attr:`compilers`, keyed by
    class, so their intermediate state can be inspected after
    compilation::

        gen = PipelineGenerator(":DemonstratorPipeline", catalog_graph)
        build = gen.compile()

        gen.compilers[LdioConfigCompiler].df_steps      # intermediate DataFrame
        gen.compilers[RdfcConfigCompiler].output_reader # accumulated reader
    """

    # Compilers that always run, in the order they must run. Excluded
    # from registry dispatch.
    SEED: tuple[type[Compiler], ...] = (PipelineExtractor, PipelineAssembler)

    def __init__(self, pipeline_id: str, catalog_graph: Graph) -> None:
        self.pipeline_id = pipeline_id
        self.catalog_graph = catalog_graph
        # Populated by ``compile``.
        self.compilers: dict[type[Compiler], Compiler] = {}
        self.build: Graph = Graph()

    def compile(self) -> Graph:
        # Reset the shared creator buffer so provenance from an earlier
        # run does not bleed into this one.
        Compiler._pending_creators = []

        # Phase 1 — seed.
        extractor = PipelineExtractor(self.pipeline_id, self.catalog_graph)
        self.build = extractor.compile()
        self.compilers = {PipelineExtractor: extractor}

        assembler = PipelineAssembler(self.build)
        self.build = assembler.compile()
        self.compilers[PipelineAssembler] = assembler

        # Phase 2 — dispatch to every registered compiler that applies.
        seed = set(self.SEED)
        candidates = [cls for cls in Compiler._registry if cls not in seed]
        for cls in sorted(candidates, key=lambda c: c.tier):
            if cls.applies_to(GraphReader(self.build)):
                instance = cls(self.build)
                self.build = instance.compile()
                self.compilers[cls] = instance
        return self.build
