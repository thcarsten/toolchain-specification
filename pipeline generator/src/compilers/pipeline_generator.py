"""User-facing factories over :class:`CompilationRunner`.

Two named callables — :func:`PipelineGenerator` and
:func:`PipelineValidator` — that each build a preset
:class:`CompilationConfig` and hand it to a shared
:class:`CompilationRunner`. The two are structurally identical; they
differ only in the preset that populates the config.

Both accept a pre-loaded ``catalog_graph``, matching the signature
every existing test and the notebook already use. Callers who prefer
to declare the catalog as a file list instead can bypass these
factories and hand a :class:`CompilationConfig` (built via
:func:`compilers.presets.default_generation_config` or
:func:`compilers.presets.default_validation_config` with
``graph_files=`` / ``inference_files=``) straight to
:class:`CompilationRunner`.
"""

from rdflib import Graph

from .presets import default_generation_config, default_validation_config
from .runner import CompilationRunner


def PipelineGenerator(pipeline_id: str, catalog_graph: Graph) -> CompilationRunner:
    """Build a runner that compiles ``pipeline_id`` into a runnable project."""
    return CompilationRunner(
        default_generation_config(pipeline_id, graph=catalog_graph)
    )


def PipelineValidator(pipeline_id: str, catalog_graph: Graph) -> CompilationRunner:
    """Build a runner that validates ``pipeline_id`` pre-generation."""
    return CompilationRunner(
        default_validation_config(pipeline_id, graph=catalog_graph)
    )
