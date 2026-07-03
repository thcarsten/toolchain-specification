"""Compiler base class.

Every concrete compiler enriches a ``tcs:PipelineBuild`` graph and
returns it. Execution order among compilers is not fixed by any
class-level rank — it emerges from each compiler's :meth:`applies_to`
trigger condition, which is evaluated by :class:`PipelineGenerator`
against the current state of the build graph on every iteration of a
fixpoint loop. A compiler runs as soon as its trigger becomes true and
does not run again once it has (its class is marked as "ran" for the
remainder of the compilation).

Provenance (``dct:creator`` on the build) is written by
:class:`PipelineGenerator` immediately after each compiler runs — the
compiler itself does not need to concern itself with it.

For compilers that must run only after other shaping compilers have
finished (for example :class:`DockerComposeCompiler`), the generator
maintains a temporary triple ``<build> tcs:isFinishing true`` while
the shaping loop is settling; such compilers can gate their
:meth:`applies_to` on the presence of that flag.
"""

import inspect
import re
from abc import ABC, abstractmethod
from typing import ClassVar

import pandas as pd
from rdflib import Graph, Literal, URIRef
from rdfine import GraphReader

from .utils import receive_first


class Compiler(ABC):
    """Abstract base for all pipeline-build compilers.

    Every subclass:

    - Takes a single ``rdflib.Graph`` in ``__init__`` (the pipeline
      build graph). May extend with additional positional arguments via
      ``super().__init__(graph)``.
    - Implements :meth:`compile` returning the enriched graph.
    - Overrides :meth:`applies_to` to declare the graph-state
      condition under which the compiler should run. The default
      returns ``False``, so every concrete compiler must declare its
      trigger explicitly.

    FILE-producing compilers additionally call :meth:`_attach_file`
    from within :meth:`compile` to register their output as a
    ``tcs:File`` on the ``tcs:PipelineBuild`` node.

    All concrete subclasses auto-register on :attr:`_registry` via
    :meth:`__init_subclass__`, so :class:`PipelineGenerator` can
    discover them without an explicit list.
    """

    _registry: ClassVar[list[type["Compiler"]]] = []

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

        Default: never applicable. Every concrete compiler must
        override this to declare the graph-state condition that
        triggers it — typically by looking for the presence (or
        absence) of a specific node type or predicate in the graph.

        Compilers that must run only after other shaping compilers
        have finished may additionally gate on the presence of
        ``<build> tcs:isFinishing true``, which
        :class:`PipelineGenerator` maintains while the shaping loop is
        settling.
        """
        return False

    @classmethod
    def compiler_iri(cls) -> str:
        """IRI used to identify this compiler as a ``tcs:Compiler``.

        Default: ``tcs:<ClassName>``. Override in subclasses if a
        different (e.g. catalog-backed) IRI is preferred.
        """
        return f"tcs:{cls.__name__}"

    @abstractmethod
    def compile(self) -> Graph:
        """Run the compiler's graph transformation and return the enriched graph.

        Heavy lifting belongs here (not in ``__init__``) so the work
        happens predictably at one moment in time and stays composable
        under the registry dispatch.
        """

    # ------------------------------------------------------------------
    # FILE helper
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
