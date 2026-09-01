"""Preset :class:`CompilationConfig`s for the two supported modes.

Each preset returns a fully-populated :class:`CompilationConfig`.
``pipeline_id`` and the input graph are supplied by the caller — the
same catalog + inference set backs both generation and validation, so
the presets vary only in their compiler lists.

Two callers of these presets:

- The :class:`PipelineGenerator` / :class:`PipelineValidator` factories
  in :mod:`compilers.pipeline_generator` — take an already-loaded
  graph, hand it to the preset via ``graph=``.
- The demo notebook — hands file lists via ``graph_files=`` and
  ``inference_files=``, letting the runner do the parsing.

Advanced callers bypass the presets and construct a
:class:`CompilationConfig` directly.
"""

from pathlib import Path
from typing import Sequence

from rdflib import Graph

from .base import Compiler
from .config import CompilationConfig
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

#: Catalog turtle files loaded by the notebook and by both presets, in
#: the order the notebook applies them. Paths are relative to the
#: pipeline generator's ``data/`` directory. Includes the shapes file
#: (last, matching the notebook) so pre-generation validation sees
#: application-profile shapes.
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

#: Fixpoint-loop compilers shared by both modes. Everything up to and
#: including :class:`GraphReducer` is preparation the two modes agree on.
_SHARED_LOOP_COMPILERS: tuple[type[Compiler], ...] = (
    PipelineAssembler,
    PipelineEnricher,
    BridgeTransportCompiler,
    SegmentTagger,
    GraphReducer,
    # Per-boundary config compilers write ``tcs:endpoint`` / ``tcs:port``
    # onto shared channels; validation needs those to check that every
    # cross-container hop is fully resolved.
    LdioHttpInConfigCompiler,
    LdioHttpOutConfigCompiler,
    RdfcHttpServerConfigCompiler,
    RdfcHttpOutConfigCompiler,
    NifiListenHttpConfigCompiler,
    NifiInvokeHttpConfigCompiler,
)

#: File-emitting compilers, only used in generation mode.
_GENERATION_ONLY_LOOP_COMPILERS: tuple[type[Compiler], ...] = (
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
)


def _resolve_files(
    files: Sequence[str] | Sequence[Path] | None,
    default: Sequence[str],
    root: Path | None,
) -> list[Path]:
    """Turn the presets' string defaults into concrete ``Path``s.

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


def default_generation_config(
    pipeline_id: str,
    *,
    graph: Graph | None = None,
    graph_files: Sequence[str] | Sequence[Path] | None = None,
    inference_files: Sequence[str] | Sequence[Path] | None = None,
    data_root: Path | None = None,
) -> CompilationConfig:
    """Preset config for compilation into a runnable project.

    Runs every shaping compiler in the fixpoint loop, then finalizes
    with :class:`DockerComposeCompiler` to aggregate every
    ``tcs:DockerComposeConfig`` into the top-level ``docker-compose.yml``.
    """
    return CompilationConfig(
        pipeline_id=pipeline_id,
        graph=graph,
        graph_files=_resolve_files(graph_files, DEFAULT_CATALOG_FILES, data_root),
        inference_files=_resolve_files(
            inference_files, DEFAULT_INFERENCE_FILES, data_root
        ),
        bootstrap_compiler=PipelineSeeder,
        loop_compilers=[
            *_SHARED_LOOP_COMPILERS,
            *_GENERATION_ONLY_LOOP_COMPILERS,
        ],
        finalize_compilers=[DockerComposeCompiler],
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

    Runs the same preparation (assemble, enrich, bridge, tag, reduce,
    per-boundary config) as generation, then finalizes with
    :class:`ValidationReportCompiler` — no file-emitting compilers, no
    ``docker-compose.yml``.
    """
    return CompilationConfig(
        pipeline_id=pipeline_id,
        graph=graph,
        graph_files=_resolve_files(graph_files, DEFAULT_CATALOG_FILES, data_root),
        inference_files=_resolve_files(
            inference_files, DEFAULT_INFERENCE_FILES, data_root
        ),
        bootstrap_compiler=PipelineSeeder,
        loop_compilers=list(_SHARED_LOOP_COMPILERS),
        finalize_compilers=[ValidationReportCompiler],
    )
