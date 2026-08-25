"""Orchestrate the compiler registry into a single end-to-end build."""

import pandas as pd
from rdflib import Graph, URIRef
from rdfine import GraphReader

from rdfine import receive_first

from .base import Compiler
from .core.pipeline_seeder import PipelineSeeder
from .core.graph_reducer import GraphReducer
from .core.validation_report_compiler import ValidationReportCompiler
from .core.docker_compose_compiler import DockerComposeCompiler


class PipelineGenerator:
    """Compile a complete ``tcs:PipelineBuild`` from a catalog graph.

    The generator runs one bootstrap step, then a fixpoint loop over
    the registry, then three explicit finalize calls:

    1. **Bootstrap** — :class:`PipelineSeeder` is instantiated
       explicitly because it is the only compiler that needs the
       ``pipeline_id`` in its constructor. It seeds the
       ``tcs:PipelineBuild`` node so that provenance and file
       attachments can find a target from here on, and normalizes
       blank-node identifiers so downstream compilers can reference
       the resulting resources by stable IRI.
    2. **Fixpoint loop** — every iteration, the generator asks every
       not-yet-run compiler in :attr:`Compiler._registry` whether its
       :meth:`Compiler.applies_to` trigger is satisfied by the current
       build graph, and runs those that are. This repeats until an
       iteration finds nothing eligible. The full catalog stays
       visible throughout the loop — narrowing is deferred to
       :class:`GraphReducer` below — so mid-loop compilers such as
       :class:`BridgeTransportCompiler` can reach catalog components
       no step of this pipeline had specialized yet.
    3. **Explicit finalize** — once the loop terminates,
       :class:`GraphReducer` narrows the build to just this pipeline's
       triples, then :class:`ValidationReportCompiler` attaches the
       SHACL validation report, then :class:`DockerComposeCompiler`
       emits ``docker-compose.yml``. All three are excluded from the
       registry via ``is_explicit_call = True`` so their ordering is
       fixed, not emergent.

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

        gen.compilers[LdioConfigCompiler].df_steps          # intermediate DataFrame
        gen.compilers[RdfcConfigCompiler].rdfc_reader       # rdfc pipeline accumulator
        gen.compilers[PipelineAssembler].added_triples.df   # what this compiler added
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

        # Bootstrap: PipelineSeeder needs the pipeline_id in its
        # constructor, so it is instantiated explicitly. It also seeds
        # the ``tcs:PipelineBuild`` node, without which provenance
        # would have nowhere to attach.
        seeder = PipelineSeeder(self.pipeline_id, self.catalog_graph)
        self.build = seeder.compile()
        self.compilers[PipelineSeeder] = seeder
        ran.add(PipelineSeeder)
        self._record_creator(PipelineSeeder)

        # Fixpoint loop over the registry. Explicit-call compilers
        # (ValidationReportCompiler, DockerComposeCompiler) are
        # excluded from ``_registry`` by ``is_explicit_call = True``
        # and are invoked directly below, after the loop.
        while True:
            reader = GraphReader(self.build)
            eligible = [
                cls
                for cls in Compiler._registry
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

        # Explicit finalize calls, in fixed order.
        for cls in (ValidationReportCompiler, DockerComposeCompiler):
            instance = cls(self.build)
            self.build = instance.compile()
            self.compilers[cls] = instance
            self._record_creator(cls)

        return self.build

    # ------------------------------------------------------------------
    # Small graph-side helpers used by ``compile``.
    # ------------------------------------------------------------------

    def _lookup_build_id(self) -> str:
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
