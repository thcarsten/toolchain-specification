"""Declarative configuration for a :class:`CompilationRunner`.

A :class:`CompilationConfig` fully describes one run of the runner:
which files to load into the input graph, which inference rules to
apply on top, which pipeline inside the loaded graph to compile, and
which compilers participate — both in the fixpoint loop and as
explicit finalize calls after it settles.

Two named preset configs live in :mod:`compilers.presets`:
:func:`default_generation_config` (emit a runnable project) and
:func:`default_validation_config` (attach a SHACL/throughput report).
Both instantiate the same :class:`CompilationRunner`, so
:class:`PipelineGenerator` and :class:`PipelineValidator` are
structurally identical — they only differ in the config passed to the
shared runner.
"""

from dataclasses import dataclass, field
from pathlib import Path

from rdflib import Graph

from .base import Compiler


@dataclass(frozen=True)
class CompilationConfig:
    """Everything a :class:`CompilationRunner` needs to run.

    Frozen so a config can be safely reused across runs (multiple
    pipelines against the same catalog) — vary a field per run via
    :func:`dataclasses.replace`.

    Loading vs. injecting a graph
    -----------------------------
    Two mutually-exclusive input modes:

    - **File-driven** (the notebook path): populate
      :attr:`graph_files` and :attr:`inference_files`; the runner
      parses and infers at :meth:`CompilationRunner.compile` time.
    - **Graph-driven** (the test path, or any caller that already has
      a loaded graph): populate :attr:`graph` directly; the file lists
      are then ignored.

    :attr:`graph` wins if both are set — that is the escape hatch tests
    use to skip disk IO on a graph they built in-memory.

    Compiler lists
    --------------
    - :attr:`bootstrap_compiler` is instantiated once, with
      ``(pipeline_id, catalog_graph)`` — it is the only compiler whose
      constructor needs the pipeline id (currently
      :class:`PipelineSeeder`).
    - :attr:`loop_compilers` are the classes the fixpoint loop
      scans; ordering emerges from each compiler's
      :meth:`Compiler.applies_to` trigger.
    - :attr:`finalize_compilers` are instantiated with ``(graph,)``
      and run unconditionally in list order after the loop terminates.
      Their :meth:`applies_to` is not consulted.
    """

    pipeline_id: str
    bootstrap_compiler: type[Compiler]
    loop_compilers: list[type[Compiler]]
    finalize_compilers: list[type[Compiler]]
    graph_files: list[Path] = field(default_factory=list)
    inference_files: list[Path] = field(default_factory=list)
    graph: Graph | None = None
    public_id: str = "file:///workspace/pipeline/"
