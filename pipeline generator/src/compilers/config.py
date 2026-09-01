"""Declarative configuration for a :class:`CompilationRunner`.

A :class:`CompilationConfig` fully describes one run of the runner:
which files to load into the input graph, which inference rules to
apply on top, which pipeline inside the loaded graph to compile, and
which compilers participate.

Two named files provide preset configs — :mod:`compilers.pipeline_generator`
(generation) and :mod:`compilers.pipeline_validator` (validation). Both
instantiate the same :class:`CompilationRunner`, so
:func:`PipelineGenerator` and :func:`PipelineValidator` are structurally
identical — they only differ in the config passed to the shared runner.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

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

    Compilers
    ---------
    :attr:`compilers` is a single flat list — no bootstrap / loop /
    finalize distinction. The runner scans it in a fixpoint loop; each
    compiler fires as soon as its :meth:`Compiler.applies_to` trigger
    becomes true against the growing graph, and does not run again.
    When the loop settles, the runner switches to the finalize phase
    (see :meth:`CompilationRunner.compile`) and re-runs the fixpoint
    with the phase triple attached, so finalize-only compilers become
    eligible.

    The :attr:`pipeline_id` is posted to the graph as a
    ``tcs:CompilationRequest`` at the start of the run, so
    :class:`PipelineSeeder` (and any other compiler that needs a
    run-scoped parameter) can read it from the graph instead of a
    special constructor slot.
    """

    pipeline_id: str
    compilers: list[type[Compiler]]
    graph_files: list[Path] = field(default_factory=list)
    inference_files: list[Path] = field(default_factory=list)
    graph: Graph | None = None
    public_id: str = "file:///workspace/pipeline/"


def resolve_files(
    files: Sequence[str] | Sequence[Path] | None,
    default: Sequence[str],
    root: Path | None,
) -> list[Path]:
    """Turn a preset's string defaults into concrete ``Path``s.

    Callers may pass their own list (of strings or paths, absolute or
    relative), or omit it to accept the default. In the default case a
    ``root`` is required — the pipeline generator has no fixed
    filesystem location, so it must be supplied per-call (the notebook
    passes ``Path("../data")``; tests pass the resolved data dir).
    """
    if files is None:
        if root is None:
            return []
        return [root / name for name in default]
    return [Path(f) for f in files]
