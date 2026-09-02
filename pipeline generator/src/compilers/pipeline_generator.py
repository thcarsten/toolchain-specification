"""Compile a pipeline definition into a runnable project.

:func:`PipelineGenerator` is a :class:`CompilationRunner` wrapped
around :data:`PipelineGeneratorConfig`, the module-level
:class:`CompilationConfig` constant for generation runs. The config
declares the shipped catalog + pipeline definition files, the two
inference-rule YAMLs, and every compiler that participates:
shaping compilers in the fixpoint loop, plus
:class:`ValidationReportCompiler` and :class:`DockerComposeCompiler`
gated on the finalize phase.

Usage::

    from compilers import PipelineGenerator, FileMaterializer

    gen = PipelineGenerator("demo:DishacledPipeline")
    build_graph = gen.compile()
    FileMaterializer(build_graph).write("./out/dishacled-full")

Advanced callers that need a different file set (tests with synthetic
pipeline definitions, workflows with catalog extensions) build their
own :class:`CompilationConfig` and hand it to :class:`CompilationRunner`
directly.

The sibling module :mod:`compilers.pipeline_validator` has the same
shape for pre-generation SHACL / throughput validation.
"""

from pathlib import Path

from .compilation_runner import CompilationConfig, CompilationRunner
from .core.bridge_transport_compiler import BridgeTransportCompiler
from .core.docker_compose_compiler import DockerComposeCompiler
from .core.graph_reducer import GraphReducer
from .core.pipeline_assembler import PipelineAssembler
from .core.pipeline_enricher import PipelineEnricher
from .core.pipeline_seeder import PipelineSeeder
from .core.segment_tagger import SegmentTagger
from .core.validation_report_compiler import ValidationReportCompiler
from .ldio.config_compiler import LdioConfigCompiler
from .ldio.http_in_config_compiler import LdioHttpInConfigCompiler
from .ldio.http_out_config_compiler import LdioHttpOutConfigCompiler
from .nifi.config_compiler import NifiConfigCompiler
from .nifi.dockerfile_compiler import NifiDockerfileCompiler
from .nifi.invoke_http_config_compiler import NifiInvokeHttpConfigCompiler
from .nifi.listen_http_config_compiler import NifiListenHttpConfigCompiler
from .nifi.remote_compiler import NifiRemoteCompiler
from .rdfc.config_compiler import RdfcConfigCompiler
from .rdfc.dockerfile_compiler import RdfcDockerFileCompiler
from .rdfc.http_out_config_compiler import RdfcHttpOutConfigCompiler
from .rdfc.http_server_config_compiler import RdfcHttpServerConfigCompiler
from .sw.env_var_compiler import SemanticWorksEnvVarCompiler
from .sw.error_alert_compiler import ErrorAlertCompiler
from .sw.mu_authorization_compiler import MuAuthorizationCompiler
from .sw.mu_cl_resources_compiler import MuClResourcesCompiler
from .sw.mu_delta_notifier_compiler import MuDeltaNotifierCompiler
from .sw.mu_dispatcher_compiler import MuDispatcherCompiler
from .sw.virtuoso_compiler import VirtuosoCompiler

# Resolves to ``<repo>/pipeline generator/data``. Baked in at import
# time so the shipped presets are absolute paths — callers don't have
# to know where the module lives on disk.
_DATA_ROOT = Path(__file__).resolve().parents[2] / "data"

#: Catalog turtle files that ship in ``data/catalog/``, in load order.
#: The shapes file is last so shape lookups see everything below it.
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
#: Definitions use disjoint pipeline-id IRIs, so loading them all is
#: safe; ``PipelineGenerator(pipeline_id)`` selects one via the id.
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


#: Preset :class:`CompilationConfig` for compilation into a runnable
#: project. Includes every shaping compiler alongside
#: :class:`ValidationReportCompiler` and :class:`DockerComposeCompiler`
#: in a single flat list — the :class:`CompilationRunner` fixpoint
#: handles ordering, and both finalize compilers' ``applies_to`` (which
#: gates on the finalize phase) keeps them from firing before the loop
#: settles.
PipelineGeneratorConfig = CompilationConfig(
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
        # Per-boundary config compilers: write ``tcs:endpoint`` /
        # ``tcs:port`` onto the shared cross-container channels.
        LdioHttpInConfigCompiler,
        LdioHttpOutConfigCompiler,
        RdfcHttpServerConfigCompiler,
        RdfcHttpOutConfigCompiler,
        NifiListenHttpConfigCompiler,
        NifiInvokeHttpConfigCompiler,
        # File-emitting compilers, generation-only.
        LdioConfigCompiler,
        RdfcConfigCompiler,
        RdfcDockerFileCompiler,
        NifiConfigCompiler,
        NifiDockerfileCompiler,
        NifiRemoteCompiler,
        SemanticWorksEnvVarCompiler,
        VirtuosoCompiler,
        MuClResourcesCompiler,
        MuDispatcherCompiler,
        MuDeltaNotifierCompiler,
        MuAuthorizationCompiler,
        ErrorAlertCompiler,
        # Fire only in the finalize phase, gated by their own
        # ``applies_to`` on ``tcs:runPhase tcs:FinalizePhase``.
        ValidationReportCompiler,
        DockerComposeCompiler,
    ],
)


def PipelineGenerator(pipeline_id: str) -> CompilationRunner:
    """Build a runner that compiles ``pipeline_id`` into a runnable project."""
    return CompilationRunner(pipeline_id, PipelineGeneratorConfig)
