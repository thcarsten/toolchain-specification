"""Compile a pipeline definition into a runnable project.

:func:`PipelineGenerator` is a :class:`CompilationRunner` wrapped
around this file's own :func:`default_generation_config`. The config
declares which catalog and inference files to load, and which
compilers participate — every shaping compiler in the fixpoint loop,
plus :class:`ValidationReportCompiler` and
:class:`DockerComposeCompiler` as finalize-phase compilers.

Callers who already have a loaded ``catalog_graph`` (tests, the demo
notebook) pass it directly:

    from compilers import PipelineGenerator, ProjectBuilder

    gen = PipelineGenerator(":DemonstratorPipeline", catalog_graph)
    build_graph = gen.compile()
    ProjectBuilder(build_graph).write("./out/dishacled-full")

Callers who prefer to declare the catalog as file paths bypass the
factory and hand the file lists to :func:`default_generation_config`
directly, then build a runner around the returned config.

The sibling module :mod:`compilers.pipeline_validator` has the same
shape, wrapping a different preset config for pre-generation SHACL /
throughput validation.
"""

from pathlib import Path
from typing import Sequence

from rdflib import Graph

from .config import CompilationConfig, resolve_files
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
from .runner import CompilationRunner
from .sw.env_var_compiler import SemanticWorksEnvVarCompiler
from .sw.error_alert_compiler import ErrorAlertCompiler
from .sw.mu_authorization_compiler import MuAuthorizationCompiler
from .sw.mu_cl_resources_compiler import MuClResourcesCompiler
from .sw.mu_delta_notifier_compiler import MuDeltaNotifierCompiler
from .sw.mu_dispatcher_compiler import MuDispatcherCompiler
from .sw.virtuoso_compiler import VirtuosoCompiler

#: Catalog turtle files loaded by the notebook and by this preset, in
#: the order the notebook applies them. Paths are relative to the
#: pipeline generator's ``data/`` directory. Includes the shapes file
#: (last, matching the notebook) so shape lookups still resolve during
#: generation.
DEFAULT_CATALOG_FILES: tuple[str, ...] = (
    "catalog/catalog-core.ttl",
    "catalog/catalog-ldio.ttl",
    "catalog/catalog-nifi.ttl",
    "catalog/catalog-rdfc.ttl",
    "catalog/catalog-rdfc-manual.ttl",
    "catalog/catalog-sw.ttl",
    "catalog/catalog-application-profile-shapes.ttl",
)

#: Inference rule YAMLs applied on top of the loaded catalog, in the
#: order the notebook applies them.
DEFAULT_INFERENCE_FILES: tuple[str, ...] = (
    "inference_rules/inference_rules.yaml",
    "inference_rules/rdfc_inference_rules.yaml",
)


def default_generation_config(
    pipeline_id: str,
    *,
    graph: Graph | None = None,
    graph_files: Sequence[str] | Sequence[Path] | None = None,
    inference_files: Sequence[str] | Sequence[Path] | None = None,
    data_root: Path | None = None,
) -> CompilationConfig:
    """Preset config for compilation into a runnable project.

    Includes every shaping compiler alongside
    :class:`ValidationReportCompiler` and
    :class:`DockerComposeCompiler` in a single flat list — the
    :class:`CompilationRunner` fixpoint handles ordering, and both
    finalize compilers' ``applies_to`` (which gates on the finalize
    phase) keeps them from firing before the loop settles.
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


def PipelineGenerator(pipeline_id: str, catalog_graph: Graph) -> CompilationRunner:
    """Build a runner that compiles ``pipeline_id`` into a runnable project."""
    return CompilationRunner(
        default_generation_config(pipeline_id, graph=catalog_graph)
    )
