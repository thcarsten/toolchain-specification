"""Compiler base class.

Every concrete compiler enriches a ``tcs:PipelineBuild`` graph and
returns it. Execution order emerges from each compiler's
:meth:`applies_to` trigger condition, which is evaluated by
:class:`compilers.compilation_runner.CompilationRunner` against the
current state of the build graph on every iteration of a fixpoint
loop. A compiler runs as soon as its trigger becomes true and does
not run again once it has.

Which compilers a run considers is declared on a
:class:`compilers.compilation_runner.CompilationConfig`. Two presets
ship with the package \u2014
:data:`compilers.pipeline_generator.PipelineGeneratorConfig` and
:data:`compilers.pipeline_validator.PipelineValidatorConfig`;
:func:`compilers.pipeline_generator.PipelineGenerator` and
:func:`compilers.pipeline_validator.PipelineValidator` wrap them.

Provenance (``dct:creator`` on the build) is written by
:class:`CompilationRunner` immediately after each compiler runs.
"""

from abc import ABC, abstractmethod

from rdflib import Graph
from rdfine import GraphReader


class Compiler(ABC):
    """Abstract base for all pipeline-build compilers.

    Every subclass:

    - Takes a single ``rdflib.Graph`` in ``__init__`` (the pipeline
      build graph).
    - Implements :meth:`compile`, which mutates :attr:`output_reader`
      and returns ``self.output_reader.graph``. ``compile()`` itself must
      stay a thin, ordered list of calls to the compiler's own *public*
      methods \u2014 no inline logic. Each public method is one traceable,
      verb-named step (``lookup_``, ``fold_in_``, ``normalize_``,
      ``attach_``, \u2026); private (``_``-prefixed) methods are helpers
      subsumed by exactly one public step and are never called directly
      from ``compile()``.
    - Overrides :meth:`applies_to` to declare the graph-state
      condition under which the compiler should run. The default
      returns ``False``, so every concrete compiler listed in a
      :class:`CompilationConfig`'s ``compilers`` list must declare
      its trigger explicitly. Finalize-phase compilers additionally
      gate on ``<?> tcs:runPhase tcs:FinalizePhase`` \u2014 the marker
      :class:`CompilationRunner` attaches between its two fixpoint
      passes.

    Every compiler has two readers over the build graph:

    - :attr:`input_reader` \u2014 a snapshot of the graph as it was at
      construction time. Never mutated by the compiler, so it acts as
      the "before" reference for the compilation delta.
    - :attr:`output_reader` \u2014 starts as the same state and is
      re-assigned by :meth:`compile` as the compiler adds or removes
      triples. Its final value is what :meth:`compile` returns.

    Because both readers are available after :meth:`compile` has run,
    the base class exposes :attr:`added_triples` and
    :attr:`removed_triples` so callers (and tests) can inspect exactly
    what each compiler contributed to the build graph.

    FILE-producing compilers additionally call the free helper
    :func:`compilers.utils.attach_file` from within :meth:`compile`
    to register their output as an ``spdx:File`` on the
    ``tcs:PipelineBuild`` node.

    Membership in a run is declared on the
    :class:`compilers.compilation_runner.CompilationConfig` handed to
    :class:`compilers.compilation_runner.CompilationRunner`.
    """

    def __init__(self, graph: Graph) -> None:
        # ``input_reader`` is the immutable "before" snapshot, stored
        # privately and exposed via a read-only property so
        # ``self.input_reader = ...`` in a subclass raises
        # ``AttributeError`` at the moment of the mistake.
        # ``output_reader`` starts as the same state and is replaced
        # in place by ``compile`` as triples are added or removed.
        # ``GraphReader`` transformations (``add`` / ``remove`` /
        # ``rename`` / ``construct``) all return a new instance backed
        # by a new rdflib graph, so mutating ``output_reader`` never
        # leaks back into ``input_reader``.
        self._input_reader = GraphReader(graph)
        self.output_reader = GraphReader(graph)

    @property
    def input_reader(self) -> GraphReader:
        """Immutable ``GraphReader`` snapshot of the graph passed to ``__init__``.

        Exposed read-only: assigning to ``self.input_reader`` in a
        subclass raises :class:`AttributeError`. The underlying
        rdflib graph is technically still mutable — compilers must
        confine themselves to :class:`GraphReader`'s transformation
        API (which returns new instances) and never mutate
        ``input_reader.graph`` directly.
        """
        return self._input_reader

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Whether this compiler should run against the given graph.

        Default: never applicable. Every concrete compiler must
        declare its trigger explicitly. :class:`CompilationRunner`
        consults it on every iteration of a single fixpoint loop —
        a compiler runs as soon as its trigger becomes true, and does
        not run again once it has (its class is marked as "ran" for
        the remainder of the compile call).

        Typical trigger shapes look for the presence (or absence) of
        a specific node type or predicate in the graph.
        :class:`PipelineSeeder` gates on the ``tcs:CompilationRequest``
        the runner posts at the start; finalize-only compilers (e.g.
        :class:`DockerComposeCompiler`,
        :class:`ValidationReportCompiler`) gate on
        ``<?> tcs:runPhase tcs:FinalizePhase``, which the runner
        attaches after the loop settles once and re-runs the fixpoint,
        so those compilers become eligible without ever having fired
        during the shaping loop.
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

        Implementations must update :attr:`output_reader` (typically
        by re-assigning it to the result of ``add`` / ``remove`` /
        ``construct`` calls) and end with
        ``return self.output_reader.graph`` so :attr:`added_triples`
        and :attr:`removed_triples` can be computed against
        :attr:`input_reader` afterwards.

        Heavy lifting belongs here so the work happens predictably at
        one moment in time.
        """

    # ------------------------------------------------------------------
    # Compilation delta — added/removed triples relative to the input
    # ------------------------------------------------------------------

    @property
    def added_triples(self) -> "GraphReader":
        """``GraphReader`` over triples present in the output but not the input.

        i.e. the triples this compiler added. Computed lazily by
        subtracting :attr:`input_reader`'s graph from
        :attr:`output_reader`; safe to call before or after
        :meth:`compile` (empty result before).
        """
        return self.output_reader.remove(self.input_reader.graph)

    @property
    def removed_triples(self) -> "GraphReader":
        """``GraphReader`` over triples present in the input but not the output.

        i.e. the triples this compiler removed. Computed lazily by
        subtracting :attr:`output_reader`'s graph from
        :attr:`input_reader`.
        """
        return self.input_reader.remove(self.output_reader.graph)
