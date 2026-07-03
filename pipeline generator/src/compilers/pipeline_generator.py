"""Orchestrate the compiler registry into a single end-to-end build."""

import pandas as pd
from rdflib import Graph, Literal, URIRef
from rdfine import GraphReader

from .base import Compiler
from .pipeline_extractor import PipelineExtractor
from .utils import receive_first


class PipelineGenerator:
    """Compile a complete ``tcs:PipelineBuild`` from a catalog graph.

    The generator runs one bootstrap step followed by a single
    fixpoint loop:

    1. **Bootstrap** — :class:`PipelineExtractor` is instantiated
       explicitly because it is the only compiler that needs the
       ``pipeline_id`` in its constructor. Its ``applies_to`` returns
       ``True`` anyway, so it would be the first compiler picked by
       the loop; the explicit call is a wiring convenience, not a
       privileged phase. It seeds the ``tcs:PipelineBuild`` node so
       that provenance and file attachments can find a target from
       here on.
    2. **Fixpoint loop** — every iteration, the generator asks every
       not-yet-run compiler in :attr:`Compiler._registry` whether its
       :meth:`Compiler.applies_to` trigger is satisfied by the current
       build graph, and runs those that are. This repeats until an
       iteration finds nothing eligible.

    Finalization-style compilers (currently only
    :class:`DockerComposeCompiler`) rely on a temporary graph flag to
    know when they should run. The generator maintains
    ``<build> tcs:isFinishing <bool>``:

    - Seeded as ``false`` right after the extractor creates the build.
    - Flipped to ``true`` when a normal shaping iteration finds
      nothing eligible; the loop then does one more scan.
    - Flipped back to ``false`` as soon as a compiler runs during the
      finishing pass, so shaping can resume if the finishing pass
      itself triggered further work.
    - Stripped from the graph entirely before :meth:`compile` returns,
      so downstream consumers (e.g. :class:`ProjectBuilder`) never see
      the runtime flag.

    Compilers register themselves on import via
    :meth:`Compiler.__init_subclass__`, so adding a new compiler is a
    matter of dropping a new file in the ``compilers`` package and
    importing it from ``compilers/__init__.py`` — the generator never
    needs editing.

    Provenance (``dct:creator`` on the build) is written by
    :meth:`_record_creator` immediately after each compiler's
    ``compile()`` returns. Compilers themselves do not manage it.

    The instances that ran are kept on :attr:`compilers`, keyed by
    class, so their intermediate state can be inspected after
    compilation::

        gen = PipelineGenerator(":DemonstratorPipeline", catalog_graph)
        build = gen.compile()

        gen.compilers[LdioConfigCompiler].df_steps      # intermediate DataFrame
        gen.compilers[RdfcConfigCompiler].output_reader # accumulated reader
    """

    def __init__(self, pipeline_id: str, catalog_graph: Graph) -> None:
        self.pipeline_id = pipeline_id
        self.catalog_graph = catalog_graph
        # Populated by ``compile``.
        self.compilers: dict[type[Compiler], Compiler] = {}
        self.build: Graph = Graph()

    def compile(self) -> Graph:
        self.compilers = {}
        ran: set[type[Compiler]] = set()

        # Bootstrap: PipelineExtractor needs the pipeline_id in its
        # constructor, so it is instantiated explicitly. It also seeds
        # the ``tcs:PipelineBuild`` node, without which provenance and
        # the ``tcs:isFinishing`` flag would have nowhere to attach.
        extractor = PipelineExtractor(self.pipeline_id, self.catalog_graph)
        self.build = extractor.compile()
        self.compilers[PipelineExtractor] = extractor
        ran.add(PipelineExtractor)
        self._record_creator(PipelineExtractor)
        self._set_finishing(False)

        # Fixpoint loop.
        settling = False
        while True:
            reader = GraphReader(self.build)
            eligible = [
                cls
                for cls in Compiler._registry
                if cls not in ran and cls.applies_to(reader)
            ]

            if not eligible:
                if settling:
                    # True fixpoint — nothing left to do.
                    self._strip_finishing()
                    return self.build
                # Give finalization-style compilers a chance by
                # flipping the flag and looping once more.
                self._set_finishing(True)
                settling = True
                continue

            # Progress was made — if we were in a finishing pass, drop
            # back to a regular shaping pass so newly-eligible normal
            # compilers can still trigger.
            if settling:
                self._set_finishing(False)
                settling = False

            for cls in eligible:
                instance = cls(self.build)
                self.build = instance.compile()
                self.compilers[cls] = instance
                ran.add(cls)
                self._record_creator(cls)

    # ------------------------------------------------------------------
    # Small graph-side helpers used by ``compile``.
    # ------------------------------------------------------------------

    def _build_id(self) -> str:
        """Locate the ``tcs:PipelineBuild`` node currently in the graph."""
        reader = GraphReader(self.build)
        return receive_first(
            reader.filter(pred="rdf:type", obj="tcs:PipelineBuild").df["sub"],
        )

    def _record_creator(self, cls: type[Compiler]) -> None:
        """Attach ``<build> dct:creator <compiler>`` and type the compiler.

        Called by ``compile`` immediately after ``cls`` finishes, so
        every applies_to invocation from that point on can observe
        which compilers have already run by querying ``dct:creator``
        on the build.
        """
        build_id = self._build_id()
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

    def _set_finishing(self, value: bool) -> None:
        """Set ``<build> tcs:isFinishing <value>`` (Python bool).

        Any pre-existing ``tcs:isFinishing`` triple on the build is
        removed first so the flag always has a single value.
        """
        reader = GraphReader(self.build)
        build_id = self._build_id()
        existing = reader.filter(sub=build_id, pred="tcs:isFinishing").graph
        reader = reader.remove(existing)
        rows = [
            {
                "sub": build_id,
                "pred": "tcs:isFinishing",
                "obj": value,
                "sub_type": URIRef,
                "obj_type": Literal,
            }
        ]
        new_graph = GraphReader(
            pd.DataFrame.from_records(rows),
            prefix_store=reader.prefix_store,
        ).graph
        self.build = reader.add(new_graph).graph

    def _strip_finishing(self) -> None:
        """Remove any ``tcs:isFinishing`` triple from the build.

        Called once, right before ``compile`` returns, so the runtime
        flag never leaks into the graph seen by downstream consumers.
        """
        reader = GraphReader(self.build)
        finishing = reader.filter(pred="tcs:isFinishing").graph
        self.build = reader.remove(finishing).graph
