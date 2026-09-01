"""Validate a pipeline definition pre-generation.

:func:`PipelineValidator` is a :class:`CompilationRunner` wrapped
around this file's own :func:`default_validation_config`. The config
declares which catalog and inference files to load, and which
compilers participate — the same shared preparation compilers as
generation (assemble, enrich, bridge, tag, reduce, per-boundary
config), then :class:`ValidationReportCompiler` as an explicit
finalize call. No file-emitting compilers, no ``docker-compose.yml``.

Callers who already have a loaded ``catalog_graph`` (tests, the demo
notebook) pass it directly:

    from compilers import PipelineValidator, ValidationReportCompiler

    val = PipelineValidator(":DemonstratorPipeline", catalog_graph)
    val.compile()
    assert val.compilers[ValidationReportCompiler].conforms is True

Callers who prefer to declare the catalog as file paths bypass the
factory and hand the file lists to :func:`default_validation_config`
directly, then build a runner around the returned config.

The sibling module :mod:`compilers.pipeline_generator` has the same
shape, wrapping a different preset config for full compilation into a
runnable project.
"""

from pathlib import Path
from typing import Sequence

from rdflib import Graph

from .config import CompilationConfig, resolve_files
from .core.bridge_transport_compiler import BridgeTransportCompiler
from .core.graph_reducer import GraphReducer
from .core.pipeline_assembler import PipelineAssembler
from .core.pipeline_enricher import PipelineEnricher
from .core.pipeline_seeder import PipelineSeeder
from .core.segment_tagger import SegmentTagger
from .core.validation_report_compiler import ValidationReportCompiler
from .ldio.http_in_config_compiler import LdioHttpInConfigCompiler
from .ldio.http_out_config_compiler import LdioHttpOutConfigCompiler
from .nifi.invoke_http_config_compiler import NifiInvokeHttpConfigCompiler
from .nifi.listen_http_config_compiler import NifiListenHttpConfigCompiler
from .rdfc.http_out_config_compiler import RdfcHttpOutConfigCompiler
from .rdfc.http_server_config_compiler import RdfcHttpServerConfigCompiler
from .runner import CompilationRunner

#: Catalog turtle files loaded by this preset, matching the generation
#: preset. Kept as an independent constant per the two-file convention
#: (each mode's config is a standalone declaration) — validation and
#: generation may diverge over time.
DEFAULT_CATALOG_FILES: tuple[str, ...] = (
    "catalog/catalog-core.ttl",
    "catalog/catalog-ldio.ttl",
    "catalog/catalog-nifi.ttl",
    "catalog/catalog-rdfc.ttl",
    "catalog/catalog-rdfc-manual.ttl",
    "catalog/catalog-sw.ttl",
    "catalog/catalog-application-profile-shapes.ttl",
)

#: Inference rule YAMLs applied on top of the loaded catalog.
DEFAULT_INFERENCE_FILES: tuple[str, ...] = (
    "inference_rules/inference_rules.yaml",
    "inference_rules/rdfc_inference_rules.yaml",
)


def default_validation_config(
    pipeline_id: str,
    *,
    graph: Graph | None = None,
    graph_files: Sequence[str] | Sequence[Path] | None = None,
    inference_files: Sequence[str] | Sequence[Path] | None = None,
    data_root: Path | None = None,
) -> CompilationConfig:
    """Preset config for pre-generation validation.

    Includes the shared preparation compilers plus
    :class:`ValidationReportCompiler` in a single flat list — the
    :class:`CompilationRunner` fixpoint handles ordering, and
    :class:`ValidationReportCompiler`'s ``applies_to`` (which gates on
    the finalize phase) keeps it from firing before the loop settles.
    No file-emitting compilers, no ``docker-compose.yml``.
    """
    return CompilationConfig(
        pipeline_id=pipeline_id,
        graph=graph,
        graph_files=resolve_files(graph_files, DEFAULT_CATALOG_FILES, data_root),
        inference_files=resolve_files(
            inference_files, DEFAULT_INFERENCE_FILES, data_root
        ),
        compilers=[
            PipelineSeeder,
            PipelineAssembler,
            PipelineEnricher,
            BridgeTransportCompiler,
            SegmentTagger,
            GraphReducer,
            LdioHttpInConfigCompiler,
            LdioHttpOutConfigCompiler,
            RdfcHttpServerConfigCompiler,
            RdfcHttpOutConfigCompiler,
            NifiListenHttpConfigCompiler,
            NifiInvokeHttpConfigCompiler,
            # Fires only in the finalize phase, gated by its own
            # ``applies_to`` on ``tcs:runPhase tcs:FinalizePhase``.
            ValidationReportCompiler,
        ],
    )


def PipelineValidator(pipeline_id: str, catalog_graph: Graph) -> CompilationRunner:
    """Build a runner that validates ``pipeline_id`` pre-generation."""
    return CompilationRunner(
        default_validation_config(pipeline_id, graph=catalog_graph)
    )
