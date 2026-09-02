"""Validate a pipeline definition pre-generation.

:func:`PipelineValidator` is a :class:`CompilationRunner` wrapped
around :data:`PipelineValidatorConfig`, the module-level
:class:`CompilationConfig` constant for validation runs. The config
declares the shipped catalog + pipeline definition files, the two
inference-rule YAMLs, and the shaping / per-boundary compilers, then
:class:`ValidationReportCompiler` gated on the finalize phase. No
file-emitting compilers, no ``docker-compose.yml``.

Usage::

    from compilers import PipelineValidator, ValidationReportCompiler

    val = PipelineValidator("demo:DishacledPipeline")
    val.compile()
    assert val.compilers[ValidationReportCompiler].conforms is True

Advanced callers that need a different file set build their own
:class:`CompilationConfig` and hand it to :class:`CompilationRunner`
directly.

The sibling module :mod:`compilers.pipeline_generator` has the same
shape for full compilation into a runnable project.
"""

from pathlib import Path

from .compilation_runner import CompilationConfig, CompilationRunner
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

# Resolves to ``<repo>/pipeline generator/data``. Baked in at import
# time so the shipped presets are absolute paths.
_DATA_ROOT = Path(__file__).resolve().parents[2] / "data"

#: Catalog turtle files that ship in ``data/catalog/``, in load order.
DEFAULT_CATALOG_FILES: tuple[str, ...] = (
    "catalog/catalog-core.ttl",
    "catalog/catalog-ldio.ttl",
    "catalog/catalog-nifi.ttl",
    "catalog/catalog-rdfc.ttl",
    "catalog/catalog-rdfc-manual.ttl",
    "catalog/catalog-sw.ttl",
    "catalog/catalog-application-profile-shapes.ttl",
)

#: Every pipeline definition that ships in ``data/pipelines/``.
DEFAULT_PIPELINE_FILES: tuple[str, ...] = (
    "pipelines/pipeline_definition.ttl",
    "pipelines/pipeline_definition_ldio_nifi.ttl",
    "pipelines/pipeline_definition_nifi_ldio.ttl",
    "pipelines/pipeline_definition_nifi.ttl",
    "pipelines/pipeline_definition_nifi.deployment.ttl",
)

#: Inference rule YAMLs applied on top of the loaded catalog.
DEFAULT_INFERENCE_FILES: tuple[str, ...] = (
    "inference_rules/inference_rules.yaml",
    "inference_rules/rdfc_inference_rules.yaml",
)


#: Preset :class:`CompilationConfig` for pre-generation validation.
#: Includes the shared preparation compilers plus
#: :class:`ValidationReportCompiler` in a single flat list — the
#: :class:`CompilationRunner` fixpoint handles ordering, and
#: :class:`ValidationReportCompiler`'s ``applies_to`` (which gates on
#: the finalize phase) keeps it from firing before the loop settles.
#: No file-emitting compilers, no ``docker-compose.yml``.
PipelineValidatorConfig = CompilationConfig(
    graph_files=[
        *(_DATA_ROOT / f for f in DEFAULT_CATALOG_FILES),
        *(_DATA_ROOT / f for f in DEFAULT_PIPELINE_FILES),
    ],
    inference_files=[_DATA_ROOT / f for f in DEFAULT_INFERENCE_FILES],
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


def PipelineValidator(pipeline_id: str) -> CompilationRunner:
    """Build a runner that validates ``pipeline_id`` pre-generation."""
    return CompilationRunner(pipeline_id, PipelineValidatorConfig)
