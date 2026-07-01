"""Compiler base class and tier enum.

Each compiler enriches a ``tcs:PipelineBuild`` graph. Compilers fall into
three conceptual tiers:

- ``Tier.SEED`` — seed the build graph from the catalog
  (``PipelineExtractor``, ``PipelineAssembler``). These always run
  first and unconditionally; every other compiler depends on the
  structure they produce.
- ``Tier.BUILD`` — shape the semantic description of the pipeline build
  (``SemanticWorksCompiler``).
- ``Tier.FILE`` — derive a ``tcs:File`` node from that description and
  attach it to the pipeline build (``LdioConfigCompiler``,
  ``RdfcConfigCompiler``, ``DockerComposeCompiler``).

Functionally all subclass the same :class:`Compiler` ABC and return the
enriched graph. The distinction is semantic, not structural; the
``tier`` class attribute lets drivers sequence compilers (SEED
before BUILD before FILE).
"""

import inspect
import re
from abc import ABC, abstractmethod
from enum import IntEnum
from typing import ClassVar

import pandas as pd
from rdflib import Graph, Literal, URIRef
from rdfine import GraphReader

from .utils import receive_first


class Tier(IntEnum):
    """Sequencing tier for a compiler.

    Lower-numbered tiers run first. Compilers in the same tier may run
    in any order — they must therefore be commutative.
    """

    SEED = 0
    BUILD = 1
    FILE = 2


class Compiler(ABC):
    """Abstract base for all pipeline-build compilers.

    Every subclass:

    - Takes a single ``rdflib.Graph`` in ``__init__`` (the pipeline
      build graph). May extend with additional positional arguments via
      ``super().__init__(graph)``.
    - Sets the class attribute :attr:`tier` to a :class:`Tier` value.
    - Implements :meth:`_compile` returning the enriched graph.
    - Optionally overrides :meth:`applies_to` to declare the SPARQL
      pattern that triggers it. Compilers that don't override are
      treated as always-applicable.

    Subclasses do **not** override :meth:`compile`. The public
    :meth:`compile` is a template method that runs :meth:`_compile`
    and then attaches this compiler as ``dct:creator`` on the
    ``tcs:PipelineBuild`` — so provenance is recorded automatically
    and cannot be forgotten when a new compiler is added.

    FILE-tier compilers additionally call :meth:`_attach_file` from
    within :meth:`_compile` to register their output as a ``tcs:File``
    on the ``tcs:PipelineBuild`` node.

    All concrete subclasses auto-register on :attr:`_registry` via
    :meth:`__init_subclass__`, so a driver (such as
    ``PipelineGenerator``) can discover them without an explicit list.
    """

    tier: ClassVar[Tier]
    _registry: ClassVar[list[type["Compiler"]]] = []
    # Creator IRIs of compilers that ran before a ``tcs:PipelineBuild``
    # node existed (currently only ``PipelineExtractor``). Flushed onto
    # the build by the next compiler whose ``_attach_creator`` finds it.
    # ``PipelineGenerator.compile`` clears this at the start of a run.
    _pending_creators: ClassVar[list[str]] = []

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        # Skip abstract intermediates; only concrete compilers register.
        if not inspect.isabstract(cls):
            Compiler._registry.append(cls)

    def __init__(self, graph: Graph) -> None:
        self.graph_reader = GraphReader(graph)

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Whether this compiler should run against the given build graph.

        Default: always applicable. Override in subclasses to be
        selective — typically by looking for the presence of a specific
        node type or predicate in the graph.
        """
        return True

    @classmethod
    def compiler_iri(cls) -> str:
        """IRI used to identify this compiler as a ``tcs:Compiler``.

        Default: ``tcs:<ClassName>``. Override in subclasses if a
        different (e.g. catalog-backed) IRI is preferred.
        """
        return f"tcs:{cls.__name__}"

    def compile(self) -> Graph:
        """Run the compiler and return the enriched build graph.

        Template method — do **not** override in subclasses. It
        delegates the actual transformation to :meth:`_compile` and
        then attaches this compiler as ``dct:creator`` on the
        ``tcs:PipelineBuild`` via :meth:`_attach_creator`.
        """
        graph = self._compile()
        self._attach_creator()
        return graph

    @abstractmethod
    def _compile(self) -> Graph:
        """Run the compiler's graph transformation and return the enriched graph."""

    # ------------------------------------------------------------------
    # Provenance helper (called automatically by ``compile``)
    # ------------------------------------------------------------------

    def _attach_creator(self) -> None:
        """Attach this compiler as ``dct:creator`` on the ``tcs:PipelineBuild``.

        Adds the triples::

            :build dct:creator tcs:<ClassName> .
            tcs:<ClassName> a tcs:Compiler .

        If no ``tcs:PipelineBuild`` yet exists in the graph (as is the
        case for the SEED-tier :class:`PipelineExtractor`, which runs
        before :class:`PipelineAssembler` materializes the build
        node), the compiler IRI is buffered on :attr:`_pending_creators`
        and attached the next time :meth:`_attach_creator` finds an
        existing build. This keeps the provenance chain complete
        without requiring the extractor to know about the assembler.
        """
        iri = self.compiler_iri()
        build_series = self.graph_reader.filter(
            pred="rdf:type", obj="tcs:PipelineBuild"
        ).df["sub"]

        if build_series.empty:
            Compiler._pending_creators.append(iri)
            return

        build_id = receive_first(build_series)
        creators = [*Compiler._pending_creators, iri]
        Compiler._pending_creators = []

        rows: list[dict] = []
        for creator in creators:
            rows.append(
                {
                    "sub": build_id,
                    "pred": "dct:creator",
                    "obj": creator,
                    "sub_type": URIRef,
                    "obj_type": URIRef,
                }
            )
            rows.append(
                {
                    "sub": creator,
                    "pred": "rdf:type",
                    "obj": "tcs:Compiler",
                    "sub_type": URIRef,
                    "obj_type": URIRef,
                }
            )

        new_graph = GraphReader(
            pd.DataFrame.from_records(rows),
            prefix_store=self.graph_reader.prefix_store,
        ).graph
        self.graph_reader = self.graph_reader.add(new_graph)

    # ------------------------------------------------------------------
    # FILE-tier helper
    # ------------------------------------------------------------------

    def _attach_file(
        self,
        *,
        filename: str,
        filepath: str,
        content: str,
    ) -> None:
        """Attach a ``tcs:File`` to the ``tcs:PipelineBuild``.

        Adds the triples::

            :build tcs:compiledFile :file_<slug>
            :file_<slug> a tcs:File ;
                tcs:filename "<filename>" ;
                tcs:filepath "<filepath>" ;
                tcs:literal "<content>" .

        The file IRI is derived from ``filepath`` + ``filename`` to be
        stable within the build. ``content`` is stored verbatim as an
        rdflib ``Literal`` — no prefix expansion is applied to it, so
        it is safe to pass arbitrary string bodies (yaml / ttl / json).
        """
        build_id = receive_first(
            self.graph_reader.filter(pred="rdf:type", obj="tcs:PipelineBuild").df[
                "sub"
            ],
        )

        # Stable, IRI-safe local name derived from path + filename.
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", f"{filepath}_{filename}").strip("_")
        file_id = f":file_{slug}"

        rows = [
            {
                "sub": build_id,
                "pred": "tcs:compiledFile",
                "obj": file_id,
                "sub_type": URIRef,
                "obj_type": URIRef,
            },
            {
                "sub": file_id,
                "pred": "rdf:type",
                "obj": "tcs:File",
                "sub_type": URIRef,
                "obj_type": URIRef,
            },
            {
                "sub": file_id,
                "pred": "tcs:filename",
                "obj": filename,
                "sub_type": URIRef,
                "obj_type": Literal,
            },
            {
                "sub": file_id,
                "pred": "tcs:filepath",
                "obj": filepath,
                "sub_type": URIRef,
                "obj_type": Literal,
            },
            {
                "sub": file_id,
                "pred": "tcs:literal",
                "obj": content,
                "sub_type": URIRef,
                "obj_type": Literal,
            },
        ]

        new_graph = GraphReader(
            pd.DataFrame.from_records(rows),
            prefix_store=self.graph_reader.prefix_store,
        ).graph
        self.graph_reader = self.graph_reader.add(new_graph)
