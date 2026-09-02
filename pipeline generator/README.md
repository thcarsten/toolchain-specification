# Table Of Contents

[1. Introduction](#1-introduction) <br>
[2. Installation](#2-installation) <br>
[3. Workflow](#3-workflow) <br>
[4. Architecture](#4-architecture) <br>
[5. Limitations](#5-limitations) <br>
[6. Future directions](#6-future-directions) <br>
[7. How To](#7-how-to) <br>

## 1. Introduction
In this repo you find the codebase for the tool "pipeline generator". As the name suggests, the pipeline generator automatically generates pipelines based on a semantic description of a pipeline. The pipeline generator accepts pipeline definitions which are written in RDF and follow the [semantic model](https://github.com/thcarsten/toolchain-specification/tree/main/semantic%20model) of the toolchain specification. Based on the pipeline definition, it looks up components and their dependencies in a component catalog, and generates a Docker Compose file wiring together the containers that resolve these dependencies. It also generates the framework-specific configuration files necessary to run the pipelines. The [data folder](https://github.com/thcarsten/toolchain-specification/tree/main/pipeline%20generator/data) is split into `catalog/` (framework catalogs + SHACL shapes + the committed `rdfc_harvest/` snapshot), `pipelines/` (the pipeline definitions used by the demos), and `inference_rules/` (RDFS/channel entailment rules loaded before compilation). Currently four frameworks are supported: RDF-Connect, LDIO, Apache NiFi and semantic.works.

The codebase is found in the [src-folder](https://github.com/thcarsten/toolchain-specification/tree/main/pipeline%20generator/src). It consists of three packages: `rdfine` provides ergonomic graph IO and transformation primitives (see its own [README](src/rdfine/README.md)); `compilers` is the pipeline generator itself, built around a small `Compiler` ABC and a self-registering dispatch system orchestrated by `PipelineGenerator`; `rdfc_catalog_harvest` is a pre-compile step that generates the RDF-Connect section of the component catalog from the packages' own published definitions (see [§4.10](#410-generating-the-rdf-connect-catalog)). Section 4 describes the architecture in detail.

## 2. Installation

The pipeline generator targets **Python 3.11+** and consists of three packages that live under `src/`: `rdfine`, `compilers`, and `rdfc_catalog_harvest`.

The recommended setup is to install `rdfine` from source — this also pulls in every third-party dependency that `compilers` needs:

```
pip install ./src/rdfine
```

Equivalently, from the repo root you can run:

```
pip install -r requirements.txt
```

The top-level `requirements.txt` is a thin shim over `src/rdfine/pyproject.toml` (`-e ./src/rdfine`), so both invocations resolve to the same dependency set — with the `pip install -r` variant additionally installing `rdfine` in editable mode.

`compilers` is not packaged separately; either run from `src/` (as the demo notebook does) or add `src/` to your `PYTHONPATH`.

Third-party dependencies pulled in by `rdfine` (all used somewhere by `compilers` too):

- `rdflib >= 7.0`
- `pyld >= 2.0`
- `pandas >= 2.0`
- `PyYAML >= 6.0`
- `boltons >= 23.0`
- `glom >= 23.0`
- `validators >= 0.22`

A typical setup from a fresh environment:

```
cd "path/to/toolchain-specification/pipeline generator"
pip install ./src/rdfine
jupyter notebook src/demo.ipynb
```

To run the test suite (`pytest` comes from `rdfine`'s `dev` extra):

```
pip install "./src/rdfine[dev]"
PYTHONPATH=src pytest tests/ -q
```

The tests cover the catalog generator (see [§4.10](#410-generating-the-rdf-connect-catalog)) and an end-to-end check that the merged catalog validates and the demonstrator pipeline still compiles. They need no network — catalog generation runs off the committed snapshot in `data/catalog/rdfc_harvest/`.

## 3. Workflow
The `CompilationRunner` class wraps the full compilation flow. It runs against a `CompilationConfig` that declares which graph files to parse, which inference rules to apply on top, and which compilers participate — a single flat list, evaluated by each compiler's `applies_to` trigger in a two-phase fixpoint. The pipeline id being compiled is not part of the config; it is passed to `CompilationRunner` directly, so one config can compile many pipelines. Two named constants + factories ship with the package:

- `PipelineGeneratorConfig` / `PipelineGenerator(pipeline_id)` — compiles into a runnable project.
- `PipelineValidatorConfig` / `PipelineValidator(pipeline_id)` — pre-generation SHACL/throughput validation only.

Both configs bake in absolute paths to the catalog, the shipped pipeline definitions, and the inference-rule files under `data/`, so the factories only need a pipeline id. Callers with their own file set (a different catalog, a synthetic test fixture) construct a `CompilationConfig` directly and hand it to `CompilationRunner(pipeline_id, config)`.

```python
from compilers import PipelineGenerator, PipelineValidator, FileMaterializer, ValidationReportCompiler

# Pre-generation validation
val = PipelineValidator(":DemonstratorPipeline")
val.compile()
assert val.compilers[ValidationReportCompiler].conforms is True

# Generation
gen = PipelineGenerator(":DemonstratorPipeline")
build_graph = gen.compile()
FileMaterializer(build_graph).write("./out/dishacled-full")
```

The returned `build_graph` contains both the semantic description of the pipeline build and the compiled files as `spdx:File` nodes attached to the `tcs:PipelineBuild` via `tcs:compiledFile` (with `tcs:filename`, `tcs:filepath`, and `tcs:literal` carrying the file body). The build graph is therefore self-describing. `FileMaterializer` iterates its `spdx:File` nodes and writes them to disk.

Internally, `CompilationRunner` runs a two-phase fixpoint over its config's flat `compilers` list, with a small handshake in between:

1. **Compilation request posted.** The runner attaches a fresh `tcs:CompilationRequest` node to the graph carrying `tcs:targetPipeline <pipeline_id>`. This is how run-scoped parameters reach the compilers — via the graph rather than a special constructor slot, so every compiler shares the same one-arg `__init__(graph)` signature. `PipelineSeeder`'s `applies_to` gates on the request's presence, so it is the first compiler to become eligible; when it runs, it reads the target pipeline id off the request, seeds the `tcs:PipelineBuild` node (linked to the definition via `prov:hadPlan`), and renames blank-node subjects to stable IRIs so later compilers have named targets to attach to.
2. **Fixpoint loop.** Every iteration, `CompilationRunner` asks every not-yet-run compiler in `config.compilers` whether its `applies_to` trigger is satisfied by the current build graph, and runs those that are. The loop terminates when a full scan finds nothing eligible.
3. **Finalize phase.** The runner attaches `<request> tcs:runPhase tcs:FinalizePhase` to the graph and re-runs the same fixpoint over the same `compilers` list, sharing the "already ran" bookkeeping so no compiler fires twice. Finalize-only compilers (currently `ValidationReportCompiler` and `DockerComposeCompiler`) gate their `applies_to` on that phase triple, so they only become eligible in this pass. Both are listed in the generation preset; the validation preset lists only `ValidationReportCompiler`.
4. **Request detached.** The runner strips every triple whose subject is the request node, so the build graph the user gets back carries no runner-internal state.

The execution order therefore emerges from the trigger conditions inside the loop rather than from any class-level rank. After compilation, `runner.compilers` maps each compiler class to the instance that ran, in insertion order — so it doubles as a record of the compile order. Every executed compiler is also attached to the build via `dct:creator`, giving the same information in the graph itself.


## 4. Architecture

This section is a closer look at the `compilers` package. (`rdfine` has its own [README](src/rdfine/README.md); `rdfc_catalog_harvest` is described in [§4.10](#410-generating-the-rdf-connect-catalog).)

### 4.1. Module overview

| Module | Exported symbols | Purpose |
| --- | --- | --- |
| [compiler_abc.py](src/compilers/compiler_abc.py) | `Compiler` | Abstract base class, `applies_to` contract (default `False`), and the `compile` contract. |
| [compilation_runner.py](src/compilers/compilation_runner.py) | `CompilationConfig`, `CompilationRunner` | `CompilationConfig` is a frozen dataclass declaring the graph/inference files and the flat `compilers` list a run considers. `CompilationRunner` is the driver: takes a pipeline id + config, loads the graph, posts a `tcs:CompilationRequest` node, runs the fixpoint, attaches `tcs:runPhase tcs:FinalizePhase` and re-runs the fixpoint, then detaches the request. Writes `dct:creator` provenance triples after each compiler runs. |
| [pipeline_generator.py](src/compilers/pipeline_generator.py) | `PipelineGenerator`, `PipelineGeneratorConfig`, `DEFAULT_CATALOG_FILES`, `DEFAULT_PIPELINE_FILES`, `DEFAULT_INFERENCE_FILES` | `PipelineGeneratorConfig` is the shipped generation-mode `CompilationConfig` — shaping + per-boundary + file-emitting compilers, plus `ValidationReportCompiler` and `DockerComposeCompiler` gated on the finalize phase; its `graph_files` bakes in absolute paths to the catalog and every pipeline definition under `data/pipelines/`. `PipelineGenerator(pipeline_id)` is a one-line factory: `CompilationRunner(pipeline_id, PipelineGeneratorConfig)`. |
| [pipeline_validator.py](src/compilers/pipeline_validator.py) | `PipelineValidator`, `PipelineValidatorConfig`, `DEFAULT_CATALOG_FILES`, `DEFAULT_PIPELINE_FILES`, `DEFAULT_INFERENCE_FILES` | `PipelineValidatorConfig` is the shipped validation-mode `CompilationConfig` — shared shaping + per-boundary compilers, plus `ValidationReportCompiler` gated on the finalize phase. No file-emitting compilers, no `docker-compose.yml`. `PipelineValidator(pipeline_id)` wraps it the same way. |
| [file_materializer.py](src/compilers/file_materializer.py) | `FileMaterializer` | Write the `spdx:File` nodes of a compiled build graph to disk. Not a `Compiler` subclass — this is the filesystem boundary. |
| [utils.py](src/compilers/utils.py) | (internal) `attach_file`, `extract_config`, `read_literal`, `parse_docker_compose_config`, `lookup_container_service_name` | Compiler-side helpers that encode knowledge of the semantic model — including the `spdx:File` attachment helper called by file-producing compilers. |
| [core/pipeline_seeder.py](src/compilers/core/pipeline_seeder.py) | `PipelineSeeder` | Reads the target pipeline id off the `tcs:CompilationRequest` posted by the runner, seeds the `tcs:PipelineBuild` skeleton (`prov:hadPlan`-linked to the definition), and renames blank-node subjects to stable IRIs. |
| [core/pipeline_assembler.py](src/compilers/core/pipeline_assembler.py) | `PipelineAssembler` | Materialize the `tcs:DockerContainer` / step / config skeleton onto the seeded `tcs:PipelineBuild`. |
| [core/segment_tagger.py](src/compilers/core/segment_tagger.py) | `SegmentTagger` | Tag every `tcs:InstancePipelineComponent` with the `tcs:segment` it belongs to (a maximal chain within one container). Used by segment-scoped SHACL shapes and per-segment framework compilers. |
| [core/graph_reducer.py](src/compilers/core/graph_reducer.py) | `GraphReducer` | Narrow the build graph down to just the triples reachable from the seeded `tcs:PipelineBuild`, after `BridgeTransportCompiler` has finished touching the catalog. |
| [core/bridge_transport_compiler.py](src/compilers/core/bridge_transport_compiler.py) | `BridgeTransportCompiler` | Auto-insert Entry/Exit boundary steps for any cross-container `tcs:Channel` whose author didn't hand-declare a bridge. See [§4.8](#48-boundary-components-and-cross-container-bridges). |
| [core/validation_report_compiler.py](src/compilers/core/validation_report_compiler.py) | `ValidationReportCompiler` | **Finalize-phase compiler.** Runs SHACL over the fully-shaped build + shapes graph and attaches the report at `validation/validation-report.ttl`. Listed in both `PipelineGeneratorConfig` and `PipelineValidatorConfig`. |
| [core/docker_compose_compiler.py](src/compilers/core/docker_compose_compiler.py) | `DockerComposeCompiler` | **Finalize-phase compiler.** Aggregates every `tcs:DockerComposeConfig` on the build into the top-level `docker-compose.yml`. Listed in the generation preset only. |
| [ldio/config_compiler.py](src/compilers/ldio/config_compiler.py) | `LdioConfigCompiler` | Emit one LDIO pipeline YAML per `tcs:segment` under `ldio/pipelines/<segment>.yml`, plus a companion `ldio/application.yml` pointing the orchestrator at that directory (Pattern A2 directory-scan). |
| [ldio/http_in_config_compiler.py](src/compilers/ldio/http_in_config_compiler.py) | `LdioHttpInConfigCompiler` | Per-boundary config compiler: writes `tcs:endpoint` onto the channel a `ldio:HttpIn` step reads from. |
| [ldio/http_out_config_compiler.py](src/compilers/ldio/http_out_config_compiler.py) | `LdioHttpOutConfigCompiler` | Per-boundary config compiler: reads `tcs:endpoint` off the channel a `ldio:HttpOut` step writes to and folds it into the step's config. |
| [nifi/config_compiler.py](src/compilers/nifi/config_compiler.py) | `NifiConfigCompiler` | Produce NiFi `flow.json` and local post-start configuration artifacts. |
| [nifi/dockerfile_compiler.py](src/compilers/nifi/dockerfile_compiler.py) | `NifiDockerfileCompiler` | Produce the local NiFi Dockerfile. |
| [nifi/invoke_http_config_compiler.py](src/compilers/nifi/invoke_http_config_compiler.py) | `NifiInvokeHttpConfigCompiler` | Per-boundary config compiler: reads `tcs:endpoint` (and optional `tcs:contentType`) off the channel a `nifi:InvokeHTTP` step writes to and folds them into the step's config. |
| [nifi/listen_http_config_compiler.py](src/compilers/nifi/listen_http_config_compiler.py) | `NifiListenHttpConfigCompiler` | Per-boundary config compiler: allocates a port for each `nifi:ListenHTTP` step and writes `tcs:port` + `tcs:endpoint` onto the channel it reads from. |
| [nifi/remote_compiler.py](src/compilers/nifi/remote_compiler.py) | `NifiRemoteCompiler` | Replace local deployment with the one-shot remote NiFi deployer. |
| [rdfc/config_compiler.py](src/compilers/rdfc/config_compiler.py) | `RdfcConfigCompiler` | Produce the RDF-Connect `pipeline.ttl` and attach it to the build. |
| [rdfc/dockerfile_compiler.py](src/compilers/rdfc/dockerfile_compiler.py) | `RdfcDockerFileCompiler` | Produce the RDF-Connect `Dockerfile`, `pyproject.toml` and `package.json` under `rdfc/`. The Dockerfile is verbatim from a `tcs:DockerImageConfig`; the two dependency files are synthesised from `spdx:Package` annotations on the components the pipeline actually uses. |
| [rdfc/http_server_config_compiler.py](src/compilers/rdfc/http_server_config_compiler.py) | `RdfcHttpServerConfigCompiler` | Per-boundary config compiler: allocates a port for each `rdfc:HttpServer` step and writes `tcs:port` + `tcs:endpoint` onto the channel it reads from. |
| [rdfc/http_out_config_compiler.py](src/compilers/rdfc/http_out_config_compiler.py) | `RdfcHttpOutConfigCompiler` | Per-boundary config compiler: reads `tcs:endpoint` off the channel a `rdfc:HttpOut` step writes to and folds it into the step's config. |
| [sw/env_var_compiler.py](src/compilers/sw/env_var_compiler.py) | `SemanticWorksEnvVarCompiler` | For semantic.works components: fold step configurations into the Docker Compose env vars of the responsible microservice. |
| [sw/virtuoso_compiler.py](src/compilers/sw/virtuoso_compiler.py) | `VirtuosoCompiler` | Materialize `semantic-works/config/virtuoso/virtuoso.ini` from the `tcs:DefaultConfig` on `sw:triple-store`. |
| [sw/mu_cl_resources_compiler.py](src/compilers/sw/mu_cl_resources_compiler.py) | `MuClResourcesCompiler` | Materialize `semantic-works/config/resources/{domain.json,domain.lisp,repository.lisp}` from `sw:mu-cl-resources`. |
| [sw/mu_dispatcher_compiler.py](src/compilers/sw/mu_dispatcher_compiler.py) | `MuDispatcherCompiler` | Materialize `semantic-works/config/dispatcher/dispatcher.ex` from `sw:mu-dispatcher`. |
| [sw/mu_delta_notifier_compiler.py](src/compilers/sw/mu_delta_notifier_compiler.py) | `MuDeltaNotifierCompiler` | Materialize `semantic-works/config/delta/rules.js` from `sw:mu-delta-notifier`. |
| [sw/mu_authorization_compiler.py](src/compilers/sw/mu_authorization_compiler.py) | `MuAuthorizationCompiler` | Materialize `semantic-works/config/authorization/config.lisp` from `sw:mu-authorization`. |
| [sw/error_alert_compiler.py](src/compilers/sw/error_alert_compiler.py) | `ErrorAlertCompiler` | Materialize `semantic-works/config/error-alert/{config.json,error.hbs}` from `sw:loket-error-alert-service`. |

All public symbols are re-exported from the package root:

```python
from compilers import (
    Compiler, CompilationConfig, CompilationRunner,
    PipelineGenerator, PipelineValidator, FileMaterializer,
    PipelineGeneratorConfig, PipelineValidatorConfig,
    PipelineSeeder, PipelineAssembler, PipelineEnricher,
    BridgeTransportCompiler, SegmentTagger, GraphReducer,
    ValidationReportCompiler, DockerComposeCompiler,
    LdioConfigCompiler, LdioHttpInConfigCompiler, LdioHttpOutConfigCompiler,
    RdfcConfigCompiler, RdfcDockerFileCompiler,
    RdfcHttpServerConfigCompiler, RdfcHttpOutConfigCompiler,
    SemanticWorksEnvVarCompiler,
    NifiConfigCompiler, NifiDockerfileCompiler,
    NifiInvokeHttpConfigCompiler, NifiListenHttpConfigCompiler,
    NifiRemoteCompiler,
    VirtuosoCompiler, MuClResourcesCompiler, MuDispatcherCompiler,
    MuDeltaNotifierCompiler, MuAuthorizationCompiler, ErrorAlertCompiler,
)
```

### 4.2. The `Compiler` ABC

Every concrete compiler inherits from `Compiler` and must satisfy a small contract:

- `__init__(graph: Graph)` — takes the build graph it operates on. The base implementation wraps it in a `GraphReader` stored on `self.graph_reader`. Every concrete compiler shares this one-arg signature — run-scoped parameters (currently only the target pipeline id `PipelineSeeder` reads) travel through the graph via the `tcs:CompilationRequest` node the runner posts (see §4.4), not through the constructor.
- `compile(self) -> Graph` — the compiler's transformation. Runs the graph-shaping work and returns the enriched build graph. Heavy lifting belongs here, not in `__init__` — this way the work happens predictably at one moment in time and stays composable in the fixpoint loop.
- `applies_to(cls, graph_reader: GraphReader) -> bool` (classmethod) — declares the graph-state condition under which the compiler should run. **The default returns `False`**, so every concrete compiler listed in a `CompilationConfig`'s `compilers` list must override it. Typical shapes:
  ```python
  @classmethod
  def applies_to(cls, graph_reader: GraphReader) -> bool:
      return not graph_reader.filter(
          pred="tcs:instantiates",
          obj="ldio:LinkedDataInteractionsOrchestrator",
      ).df.empty
  ```
  A compiler that needs to see which other compilers already ran can inspect the `dct:creator` triples on the build (see §4.6). Finalize-only compilers (`ValidationReportCompiler`, `DockerComposeCompiler`) gate their `applies_to` on the presence of `<?> tcs:runPhase tcs:FinalizePhase` — the marker the runner attaches between the two fixpoint passes — so they only become eligible after every shaping compiler has settled.
- `compiler_iri(cls) -> str` (classmethod) — the IRI used when `CompilationRunner` records this compiler as `dct:creator`. Defaults to `tcs:<ClassName>`; override if a catalog-backed IRI is preferred.

Intermediate state that the compile process produces (e.g. partial DataFrames, accumulated readers) is declared as instance attributes in `__init__` and populated by `compile()`. This makes the inspection surface explicit and lets the user poke at `compiler.df_steps`, `compiler.output_reader`, etc. after `compile()` returns — useful for debugging.

### 4.2.1. Method naming & single-responsibility splitting

`compile()` must be a thin, ordered list of calls to the compiler's own *public* methods and nothing else — no inline SPARQL queries, loops, dict-building, or conditionals of its own. Each public method is one traceable step/concern of the compile process, e.g.:

```python
def compile(self) -> Graph:
    self.normalize_config_shapes()
    self.validate_normal_shapes()
    self.gather_throughput_shapes()
    ...
    return self.output_reader.graph
```

(`ValidationReportCompiler`'s real 8-method shape — see [`core/validation_report_compiler.py`](src/compilers/core/validation_report_compiler.py).) Public methods stay public specifically so a compiled build's intermediate steps are inspectable/steppable after the fact, matching the "intermediate state as instance attributes" convention above.

Private (`_`-prefixed) methods are helpers subsumed by exactly *one* public step and must never be called directly from `compile()` itself — only from the one public method that owns them.

Every method name — public or private — must contain a verb true to what it does: `lookup_`, `fold_in_`, `normalize_`, `validate_`, `gather_`, `fill_`, `list_`, `generate_`, `attach_`, `describe_`, `extract_`, `seed_`, etc. are all in active use across the codebase. A noun-only name (e.g. a method literally called `container_service_names`) must be renamed to include a verb (`lookup_container_service_names`).

### 4.3. Config-driven compiler selection

There is no global registry. Which compilers a run considers is declared on the `CompilationConfig` handed to `CompilationRunner`:

```python
@dataclass(frozen=True)
class CompilationConfig:
    compilers: list[type[Compiler]]
    graph_files: list[Path] = field(default_factory=list)
    inference_files: list[Path] = field(default_factory=list)
    public_id: str = "file:///workspace/pipeline/"
```

The pipeline id being compiled is not part of the config — it is a per-run argument to `CompilationRunner`, so one config is reusable across every pipeline that lives in its `graph_files`.

One flat `compilers` list — no bootstrap / loop / finalize categories in the config itself. Ordering is entirely encoded in each compiler's `applies_to` trigger, evaluated by the runner's two-phase fixpoint (see §4.4).

Two module-level constants ship with the package — `PipelineGeneratorConfig` (in [`compilers/pipeline_generator.py`](src/compilers/pipeline_generator.py)) covers every framework's shaping and file-emitting compilers plus `ValidationReportCompiler` and `DockerComposeCompiler`; `PipelineValidatorConfig` (in [`compilers/pipeline_validator.py`](src/compilers/pipeline_validator.py)) covers just the framework-agnostic shaping + per-boundary config compilers plus `ValidationReportCompiler`. Both are re-exported from `compilers`. The two `compilers` lists side by side are the documentation of "what validation needs" vs "what generation adds on top".

Callers with a different file set (a different catalog, a synthetic test fixture) construct their own `CompilationConfig` and hand it to `CompilationRunner(pipeline_id, config)` directly.

Adding a new compiler is a matter of dropping a new file in the appropriate `compilers/<framework>/` subfolder (or `compilers/core/` for framework-agnostic ones), importing it from `compilers/__init__.py`, and adding it to the config(s) that should run it. The runner itself never needs editing.

### 4.4. CompilationRunner: the two-phase fixpoint

`CompilationRunner` is the entry point that turns a pipeline id + `CompilationConfig` into a fully compiled build graph. Its `compile()` method has four phases: catalog loading, first-pass fixpoint, finalize-phase fixpoint, and request detachment.

**Catalog loading.** `graph_files` are parsed into an rdflib graph and `inference_files` are applied on top via `GraphReader.infer()`. The result is the graph the fixpoint operates over.

**Compilation-request handshake.** Before the first fixpoint pass, the runner attaches a fresh `tcs:CompilationRequest` node carrying the target pipeline id:

```turtle
:compilation_request a tcs:CompilationRequest ;
                    tcs:targetPipeline <pipeline> .
```

This is the runner-to-compiler channel for run-scoped parameters — anything a compiler needs to know about the current run travels through the graph, so every compiler shares the same one-arg `__init__(graph)` signature. `PipelineSeeder`'s `applies_to` gates on the request's presence, which is what makes it the first compiler to become eligible.

**First fixpoint pass.** Every iteration, `CompilationRunner` scans `config.compilers`, filters out compilers that have already run, and evaluates the remaining ones' `applies_to` against the current build graph:

```python
while True:
    eligible = [cls for cls in self.config.compilers
                if cls not in ran and cls.applies_to(GraphReader(self.build))]
    if not eligible:
        break
    for cls in eligible:
        instance = cls(self.build)
        self.build = instance.compile()
        self._record_creator(cls)   # attach dct:creator + type
        ran.add(cls)
```

Because triggers are evaluated against the growing graph, execution order emerges naturally. `PipelineSeeder` fires first because the `tcs:CompilationRequest` node is exactly what its trigger looks for; when it runs, it seeds the `tcs:PipelineBuild`:

```turtle
<pipeline>_build a tcs:PipelineBuild ;
                 prov:hadPlan <pipeline> .
```

and renames any blank-node subjects (typed by RDFS entailment as pipeline components, configs, channels, and shapes) to stable IRIs so downstream compilers always have named targets to attach triples to. Unlike a later `GraphReducer` pass, the seeder does *not* narrow the catalog down — the full catalog stays visible until after `BridgeTransportCompiler` runs, so that compiler can freely draw on components no step has specialized yet (see §4.8). The rest of the shaping compilers then fall into place as each one adds the triples the next one's `applies_to` looks for.

Compilers eligible in the same iteration are treated as commutative and run in list order within that pass; before the next iteration the loop re-scans, so any new eligibility introduced by their combined effect is picked up on the next round.

**Finalize-phase fixpoint.** Once the first pass settles, the runner attaches the finalize marker to the request node and re-enters the same loop:

```python
self.set_phase("tcs:FinalizePhase")      # <request> tcs:runPhase tcs:FinalizePhase
self.run_fixpoint()                      # same `ran` set as pass 1
```

The `ran` bookkeeping is shared across both passes, so no compiler fires twice. Compilers whose `applies_to` gates on `<?> tcs:runPhase tcs:FinalizePhase` become eligible now:

- `PipelineGeneratorConfig`'s finalize-phase eligible set is `[ValidationReportCompiler, DockerComposeCompiler]` — the report is attached first, then every `tcs:DockerComposeConfig` on the build (all finalized by earlier compilers) is aggregated into a single `docker-compose.yml`.
- `PipelineValidatorConfig`'s finalize-phase eligible set is `[ValidationReportCompiler]` — merges the fully shaped build with the untouched shapes graph, runs SHACL, and attaches the report at `validation/validation-report.ttl`.

Adding a new finalize compiler is a matter of appending it to the appropriate config's `compilers` list and gating its `applies_to` on `<?> tcs:runPhase tcs:FinalizePhase`. No runner edit needed.

**Request detachment.** Before returning, the runner strips every triple whose subject is the compilation-request node — the type triple, the `tcs:targetPipeline`, and the `tcs:runPhase` marker — so the build graph the user gets back carries no runner-internal state.

**Inspection after the run.** The instances that ran are kept on `runner.compilers`, keyed by class, in insertion order. So `list(runner.compilers)` doubles as the compile order. The same information is also in the build graph as `dct:creator` triples on the `tcs:PipelineBuild`.

### 4.5. File attachment: the spdx:File vocabulary

Compilers that produce a file attach their output to the build by calling the free helper `attach_file(self.output_reader, filename=..., filepath=..., content=...)` (from `compilers.utils`) from inside `compile()` and re-assigning `self.output_reader` with its return value. The helper adds five triples to the build graph:

```turtle
:build tcs:compiledFile :file_<slug> .

:file_<slug> a spdx:File ;
    tcs:filename "..." ;
    tcs:filepath "..." ;
    tcs:literal  "..." .
```

The slug is derived from `filepath_filename` via `re.sub(r"[^a-zA-Z0-9]+", "_", ...).strip("_")`, so file IRIs are stable within a build and unique for distinct `(filepath, filename)` pairs.

The body of the file is stored verbatim as an rdflib `Literal` — no prefix expansion is applied to it. This is what makes it safe to put arbitrary text bodies (YAML, Turtle, JSON) into `tcs:literal` even when they contain colons or other CURIE-like substrings.

After `CompilationRunner.compile()` returns, the build graph is fully self-describing: `FileMaterializer` iterates over the `spdx:File` nodes and writes each to disk at `tcs:filepath / tcs:filename` with body `tcs:literal`. It collects the file records into a `pandas.DataFrame` on `builder.files` first, so the planned writes can be inspected before touching the filesystem. A path-traversal guard rejects any `tcs:filepath` that would escape the target directory.

### 4.6. Provenance: dct:creator attachment

Every compiler that runs is recorded on the build graph as a `dct:creator`. `CompilationRunner` writes the triples itself, immediately after each compiler's `compile()` returns:

```turtle
:build dct:creator tcs:<ClassName> .
tcs:<ClassName> a tcs:Compiler .
```

Because `PipelineSeeder` fires on the very first iteration of the fixpoint (triggered by the `tcs:CompilationRequest` node the runner posts up front) and creates the `tcs:PipelineBuild` node before returning, the build always exists by the time the runner records its provenance — no buffering or two-step attachment is needed. The compiler IRI defaults to `tcs:<ClassName>` and can be overridden per compiler via the `compiler_iri()` classmethod (e.g. to point at a catalog-backed entry).

A useful side effect: because provenance is attached *while the loop is running*, every subsequent `applies_to` invocation can inspect `dct:creator` on the build to check which compilers have already run — no extra bookkeeping needed.

### 4.7. Compiler responsibilities at a glance

| Compiler | Trigger (`applies_to`) | Reads from the build | Writes to the build |
| --- | --- | --- | --- |
| `PipelineSeeder` | a `tcs:CompilationRequest` node is present in the graph (the runner posts one up front) | the catalog + the request's `tcs:targetPipeline` | `<pipeline>_build a tcs:PipelineBuild ; prov:hadPlan <pipeline>`; blank-node subjects renamed to stable IRIs |
| `PipelineAssembler` | exactly one `tcs:PipelineDefinition` in the graph and it has at least one step (`p-plan:isStepOfPlan`) | the seeded pipeline + catalog | `tcs:DockerContainer`, `dct:hasPart`, `tcs:instantiates`, `tcs:runs` |
| `PipelineEnricher` | `<build> dct:creator tcs:PipelineAssembler` present | steps and channels | synthesized `tcs:Channel`s from `p-plan:isPrecededBy`, and a `tcs:PipelineConfig` slot on every step that lacks one |
| `BridgeTransportCompiler` | `<build> dct:creator tcs:PipelineEnricher` present, and some `tcs:Channel` crosses container boundaries | cross-container channels + the catalog of boundary components | inserted Entry/Exit boundary steps (where neither side is already a boundary). See [§4.8](#48-boundary-components-and-cross-container-bridges). |
| `SegmentTagger` | `<build> dct:creator tcs:BridgeTransportCompiler` present | the final step → channel graph | `tcs:segment` on every `tcs:InstancePipelineComponent` |
| `GraphReducer` | `<build> dct:creator tcs:SegmentTagger` present | the full build graph | narrows the build down to just triples reachable from `<build>` and its shape subgraph |
| `RdfcHttpServerConfigCompiler` | any `rdfc:HttpServer` step present | the step and its channel | `tcs:endpoint`/`tcs:port` on the channel; `rdfc:port`/`rdfc:options` on the step |
| `RdfcHttpOutConfigCompiler` | any `rdfc:HttpOut` step present with an outgoing channel carrying `tcs:endpoint` | the step and its channel | `rdfc:endpoint` on the step |
| `LdioHttpInConfigCompiler` | any `ldio:HttpIn` step present and `<build> dct:creator tcs:SegmentTagger` (so the step's `tcs:segment` is available for path derivation — LDIO serves each pipeline at `/{segment_name}`) | the step and its channel | `tcs:endpoint`/`tcs:port`/`tcs:contentType` on the channel |
| `LdioHttpOutConfigCompiler` | any `ldio:HttpOut` step present with an outgoing channel carrying `tcs:endpoint` | the step and its channel | `ldio:endpoint` and `ldio:rdf-writer` on the step |
| `NifiListenHttpConfigCompiler` | any `nifi:ListenHTTP` step present | the step and its channel | `tcs:endpoint`/`tcs:port` on the channel; `nifi:listeningPort`/`nifi:basePath` on the step |
| `NifiInvokeHttpConfigCompiler` | any `nifi:InvokeHTTP` step present with an outgoing channel carrying `tcs:endpoint` | the step and its channel | `nifi:httpMethod`/`nifi:httpUrl` (and `nifi:contentType` when the channel carries `tcs:contentType`) on the step |
| `SemanticWorksEnvVarCompiler` | any `tcs:PipelineComponent` in the `sw:` namespace, and at least one `tcs:DockerContainer` exists | step configs + docker configs of `sw:` components | updated `tcs:literal` on each affected `tcs:DockerComposeConfig` |
| `VirtuosoCompiler` | a container instantiates `sw:triple-store` | the `:VirtuosoIniDefault` config body | `spdx:File` named `semantic-works/config/virtuoso/virtuoso.ini` |
| `MuClResourcesCompiler` | a container instantiates `sw:mu-cl-resources` | the three `mu-cl-resources` default config bodies | `spdx:File`s named `semantic-works/config/resources/{domain.json,domain.lisp,repository.lisp}` |
| `MuDispatcherCompiler` | a container instantiates `sw:mu-dispatcher` | the `:MuDispatcherExDefault` config body | `spdx:File` named `semantic-works/config/dispatcher/dispatcher.ex` |
| `MuDeltaNotifierCompiler` | a container instantiates `sw:mu-delta-notifier` | the `:MuDeltaNotifierRulesJsDefault` config body | `spdx:File` named `semantic-works/config/delta/rules.js` |
| `MuAuthorizationCompiler` | a container instantiates `sw:mu-authorization` | the `:MuAuthorizationConfigLispDefault` config body | `spdx:File` named `semantic-works/config/authorization/config.lisp` |
| `ErrorAlertCompiler` | a container instantiates `sw:loket-error-alert-service` | the two error-alert default config bodies | `spdx:File`s named `semantic-works/config/error-alert/{config.json,error.hbs}` |
| `LdioConfigCompiler` | a container instantiates `ldio:LinkedDataInteractionsOrchestrator`, every LDIO step carries a `tcs:segment`, and every LDIO step's `p-plan:hasInputVar` has been populated | LDIO components and their configs | `spdx:File`s named `ldio/pipelines/<segment>.yml` (one per segment) and `ldio/application.yml` |
| `RdfcConfigCompiler` | a container instantiates `rdfc:Orchestrator` | RDF-Connect components, runners and step graph | `spdx:File` named `rdfc/pipeline.ttl` |
| `RdfcDockerFileCompiler` | a container instantiates `rdfc:Orchestrator` and a `tcs:DockerImageConfig` is present | the Dockerfile literal + every `spdx:Package` reachable via `dct:requires` from components in the container | `spdx:File`s named `rdfc/Dockerfile`, `rdfc/pyproject.toml`, `rdfc/package.json` |
| `NifiConfigCompiler` | a container instantiates `nifi:Orchestrator`, `<build> dct:creator tcs:BridgeTransportCompiler` (so any Bridge-inserted boundary steps are visible), and every NiFi step whose specialized component is typed `tcs:EntryBoundaryComponent`/`tcs:ExitBoundaryComponent` already has a `p-plan:hasInputVar` (so the paired per-boundary config compilers have finished) | NiFi steps, component metadata, configs and channels | persisted `spdx:File` named `nifi/flow.json`; for local builds, secret references also produce a one-shot `nifi-configure` service and `nifi/configure_local.py` |
| `NifiDockerfileCompiler` | local NiFi deployment and a NiFi `tcs:DockerImageConfig` is present | the NiFi Dockerfile literal | `spdx:File` named `nifi/Dockerfile` |
| `NifiRemoteCompiler` | `nifi:deploymentMode "remote"` and `nifi/flow.json` is present | the persisted flow, deployment config, secret references and NiFi compose config | upload-format `nifi/flow_definition.json`, stdlib-only `nifi/deploy_flow.py`, and a one-shot deployer using native Compose secret mounts |
| `ValidationReportCompiler` | *(finalize phase)* `<?> tcs:runPhase tcs:FinalizePhase` present. Listed in both the generation and the validation preset. | the fully shaped build + shapes graph | `spdx:File` named `validation/validation-report.ttl` |
| `DockerComposeCompiler` | *(finalize phase)* `<?> tcs:runPhase tcs:FinalizePhase` present, and any `tcs:DockerComposeConfig` reachable from the build. Listed in the generation preset only. | every `tcs:DockerComposeConfig` reachable from the build | `spdx:File` named `./docker-compose.yml` |

### 4.8. Boundary components and cross-container bridges

A `tcs:Channel` connects an [InstancePipelineComponent](../../semantic%20model/README.md#instancepipelinecomponent) that writes to it with one or more that read from it. When both live in the same container, nothing special is needed — the framework's own runtime carries the data. When they live in *different* containers, an explicit transport hop is required. The pipeline generator handles this via **boundary components** and an auto-insertion compiler.

**Boundary components.** A catalog component typed either [tcs:EntryBoundaryComponent](../../semantic%20model/README.md#entryboundarycomponent) (a receiver, e.g. `rdfc:HttpServer`, `ldio:HttpIn`, `nifi:ListenHTTP`) or [tcs:ExitBoundaryComponent](../../semantic%20model/README.md#exitboundarycomponent) (a sender, e.g. `rdfc:HttpOut`, `ldio:HttpOut`, `nifi:InvokeHTTP`) declares which transport it speaks via [tcs:channelType](../../semantic%20model/README.md#tcschanneltype), pointing at a subclass of `tcs:Channel` (e.g. `tcs:HttpChannel`, `tcs:SparqlUpdateChannel`). Both flags travel through pure RDFS subclass entailment: any component typed `EntryBoundaryComponent` is also a `PipelineComponent`, and any channel typed `HttpChannel` is also a `Channel`.

**`BridgeTransportCompiler`** examines every `tcs:Channel` whose reader and writer end up in different containers and picks one of four behaviours:

1. **Both sides already boundary steps** (hand-authored bridge, e.g. the demonstrator's `LdioForward → HttpIngest` hop). Leaves the channel alone — the pipeline author has fully described the bridge.
2. **Neither side is a boundary step.** Auto-inserts a matching Entry/Exit pair from the catalog — an ExitBoundary just before the writing side, an EntryBoundary just after the reading side — picked by matching `tcs:channelType` against the compiler's `default_channel_type` (fixed to `tcs:HttpChannel` for the MVP; SPARQL-Update bridges therefore stay hand-authored, see [§5.2](#52-cross-framework-channels)). Onboarding a new HTTP-boundary component is a catalog-only change.
3. **Exactly one side is a boundary step.** Raises: a bridge is half-declared and the author's intent is ambiguous — either declare both sides, or declare neither.
4. **No boundary component in the catalog matches** the required `tcs:channelType`. Flagged pre-compile by `tcs:CatalogMissingBridgeShape`, so the pipeline is rejected at SHACL time rather than crashing the compiler.

**Per-boundary config compilers** (`RdfcHttpServerConfigCompiler`, `LdioHttpInConfigCompiler`, `NifiListenHttpConfigCompiler`, `RdfcHttpOutConfigCompiler`, `LdioHttpOutConfigCompiler`, `NifiInvokeHttpConfigCompiler`) then handle the transport-metadata layer. Each Entry-side compiler allocates its own port + endpoint (framework-fixed default, bumped on collision) and writes them onto the shared channel as [tcs:endpoint](../../semantic%20model/README.md#tcsendpoint) / [tcs:port](../../semantic%20model/README.md#tcsport) (and optionally `tcs:contentType` when the Entry-side framework needs the Exit side to advertise a specific request Content-Type, as LDIO's `RdfAdapter` does); each Exit-side compiler reads those from the channel to configure its own step. Entry and Exit therefore stay fully decoupled — the graph is the message bus.

**`SegmentTagger`** runs after boundary insertion and tags every [InstancePipelineComponent](../../semantic%20model/README.md#instancepipelinecomponent) with the [tcs:segment](../../semantic%20model/README.md#tcssegment) it belongs to (a maximal chain of steps in one container that data can flow through without crossing a container boundary). Framework config compilers can then emit one config artifact per segment (LDIO's Pattern A2 directory-scan, `ldio/pipelines/<segment>.yml`), and segment-scoped SHACL shapes such as `tcs:LdioSingularStepShape` become checkable per-segment rather than per-pipeline. Segments are a purely compile-time bookkeeping notion — they never appear in the deployed pipeline.

Onboarding a new HTTP boundary component for a new framework needs three things in the catalog: (a) type it `tcs:EntryBoundaryComponent` or `tcs:ExitBoundaryComponent`; (b) declare `tcs:channelType tcs:HttpChannel`; (c) write a small per-boundary config compiler that knows where in the component's own config schema its port/endpoint live. No changes to `BridgeTransportCompiler` itself.

### 4.9. NiFi deployment target

NiFi runs locally in the generated Docker stack by default. To deploy the generated process group into an existing NiFi instance instead, add this opt-in triple to the pipeline definition or its separate deployment overlay:

For a local build, catalog-marked sensitive properties remain absent from `flow.json`. When their authored values are `tcs:SecretReference` nodes, the compiler adds a one-shot `nifi-configure` Compose service. It waits for NiFi, mounts the referenced host variables as Compose secrets, temporarily stops or disables each affected component, updates it through the local NiFi API, and restores the state authored in the pipeline definition. The generated `.env.example` lists the required variables; copy it to `.env`, fill the values, and use the normal `docker compose up` command.

```turtle
:DemonstratorPipeline
    nifi:deploymentMode "remote" ;
    nifi:deploymentConfig :NifiRemoteDeployment .

:NifiRemoteDeployment
    a tcs:PipelineConfig ;
    tcs:embedded [
        nifi:dshUsername [
            a tcs:SecretReference ;
            tcs:secretName "DSH_USERNAME"
        ] ;
        nifi:dshPassword [
            a tcs:SecretReference ;
            tcs:secretName "DSH_PASSWORD"
        ] ;
        nifi:dshGatewayUrl "https://gateway.az.kpn-dsh.com/token" ;
        nifi:baseUrl "https://nifi.urban-sense-acc.az.kpn-dsh.com" ;
        nifi:parentProcessGroupId "..."
    ] .
```

The remote build replaces the local NiFi service with a one-shot `nifi-deploy` service. A `tcs:SecretReference` contains only the logical name of a host environment variable; its value is never read by the generator or written into the build graph, flow JSON, notebook, or Compose YAML. Compose resolves each value at deployment and mounts it under `/run/secrets/` for the deployer. `nifi:parentProcessGroupId` is optional and root is used when it is absent or empty.

Populate every referenced variable from the current shell, `.env`, or a CI secret store, then run `docker compose up nifi-deploy`. For the example overlay these are `DSH_USERNAME`, `DSH_PASSWORD`, `AZURE_STORAGE_ACCOUNT_NAME`, and `AZURE_SAS_TOKEN`. Compose automatically loads `.env` beside the generated `docker-compose.yml`; the generated `.env.example` lists every required name and can be copied as a starting point. See [`data/pipelines/pipeline_definition_nifi.deployment.ttl`](data/pipelines/pipeline_definition_nifi.deployment.ttl) for the reference-only deployment overlay.

### 4.10. Generating the RDF-Connect catalog

The `rdfc_catalog_harvest` package is deliberately **not** part of the `Compiler` hierarchy. Compilers are graph-to-graph transformations inside a single pipeline build; catalog generation runs before any build exists and crosses the boundary into the outside world, which puts it in the same category as `FileMaterializer`. Its modules:

| Module | Purpose |
| --- | --- |
| [rdfc_catalog_harvest/requests.py](src/rdfc_catalog_harvest/requests.py) | Parse `tcs:CatalogRequest` blocks — the whole hand-written input. |
| [rdfc_catalog_harvest/semver.py](src/rdfc_catalog_harvest/semver.py) | Resolve a version range to a concrete version (npm caret/tilde/prerelease rules). |
| [rdfc_catalog_harvest/registries.py](src/rdfc_catalog_harvest/registries.py) | Fetch from npm / PyPI / a local checkout. The only module that touches the network. |
| [rdfc_catalog_harvest/harvester.py](src/rdfc_catalog_harvest/harvester.py) | Pick the Turtle file that declares the requested component, freeze it into the snapshot. |
| [rdfc_catalog_harvest/snapshot.py](src/rdfc_catalog_harvest/snapshot.py) | Read/write the committed `data/catalog/rdfc_harvest/` records. |
| [rdfc_catalog_harvest/shapes.py](src/rdfc_catalog_harvest/shapes.py) | Translate an upstream SHACL shape into a toolchain config shape (the four rewrites). |
| [rdfc_catalog_harvest/emitter.py](src/rdfc_catalog_harvest/emitter.py) | Render the catalog file. Pure function of requests + snapshot. |
| [rdfc_catalog_harvest/turtle.py](src/rdfc_catalog_harvest/turtle.py) | Deterministic Turtle text emission, for stable diffs. |
| [rdfc_catalog_harvest/cli.py](src/rdfc_catalog_harvest/cli.py) | `python -m rdfc_catalog_harvest {harvest,generate}`. |

`data/catalog/catalog-rdfc.ttl` is **generated, not hand-written**. An RDF-Connect processor already publishes a `processors.ttl` describing itself, so almost everything its catalog entry needs is a restatement of facts the package ships. Transcribing those by hand is what let the catalog drift out of sync with upstream.

The hand-written input is one block per component in [`data/catalog/catalog-rdfc-requests.ttl`](data/catalog/catalog-rdfc-requests.ttl):

```turtle
rdfc:SPARQLIngest a tcs:CatalogRequest ;
    tcs:package "@rdfc/sparql-ingest-processor-ts" ;
    spdx:versionInfo "^2.1.7" .
```

Everything else is derived:

| Generated triple | Derived from |
| --- | --- |
| `rdfs:label`, `rdfs:description` | upstream `rdfs:label` / `rdfs:comment` \| `rdfs:description` |
| `dcat:landingPage` | registry repository URL |
| `dct:requires <runner>` | upstream `rdfc:jsImplementationOf` vs `rdfc:pyImplementationOf` |
| `dct:requires [ a spdx:Package ]` | request package; `spdx:suppliedBy` follows from the language |
| `owl:imports` | package layout + which file inside the package declares the component |
| `dcat:qualifiedRelation` → config shape | the package's own `sh:NodeShape`, translated |

`spdx:versionInfo` is the only field that cannot be derived — it is a policy choice, not a fact about the package — which is why it is the one thing the request states alongside the package name.

Two commands, deliberately separate:

```
python -m rdfc_catalog_harvest harvest     # network: resolves versions, refreshes data/catalog/rdfc_harvest/
python -m rdfc_catalog_harvest generate    # offline + deterministic: rewrites data/catalog/catalog-rdfc.ttl
```

`harvest` resolves each request's version range against the registry (real caret/tilde/prerelease matching, not `dist-tags.latest`), downloads the package, and freezes the Turtle file that declares the component into `data/rdfc_harvest/` next to a JSON record of the registry facts. Both are committed. `generate` reads only that snapshot, so regeneration needs no network, produces byte-identical output, and a diff shows whether a change came from upstream (the snapshot moved) or from policy (the request file moved) — the same discipline as committing a lockfile. `python -m rdfc_catalog_harvest generate --check` exits non-zero if the committed file is stale.

**Translating the shape.** Upstream shapes cannot be pasted verbatim; five systematic rewrites are applied. The first four share a root cause — a pipeline definition supplies parameter values as bare IRIs and untyped blank nodes inside a `tcs:embedded` block, so any constraint requiring an `rdf:type` on the value is unsatisfiable as written:

1. `sh:class rdfc:Reader` / `rdfc:Writer` → `sh:class tcs:Channel` (the type `inference_rules.yaml` derives from `tcs:readsFrom` / `tcs:writesTo`). Direction preserved on a non-constraining `tcs:upstreamClass`.
2. `sh:class` at a nested config class → `sh:node` against a named shape, which is then targeted by `sh:targetObjectsOf` on the property path instead of by class.
3. `sh:class` at a class from a foreign vocabulary. Two outcomes, depending on whether the toolchain models that class's *value space*:
   - Listed in `EXTERNAL_SHAPES` (`catalog/shapes.py`) → `sh:node` against a hand-written shape. `rdfl:PathLens` on `tm:path` maps to `:ShaclPathShape` (declared in `catalog-rdfc-manual.ttl`), which describes the property-path grammar rdf-lens can actually interpret. This is enforcement, not documentation: the path is consumed at **execution** time to extract a value from every record, so an unusable path is a runtime failure that validation now catches first — including the most likely mistake, a quoted string.
   - Not listed → demoted to a `tcs:upstreamClass` annotation. A constraint the toolchain can never satisfy is worse than a documented one it does not check. Adding a registry entry is how such a constraint gets promoted from documented to enforced.
4. `sh:datatype xsd:iri` → `sh:nodeKind sh:IRI`. **Not a typo upstream** — rdf-lens keys its own extractor off exactly that term (`ShaclPredicatePath = extractLeaf(XSD.terms.custom("iri"))`), so the marker is load-bearing at runtime. It is simply not valid *validation*: there is no `xsd:iri` datatype, so as SHACL it demands a literal no IRI can be. The original is preserved on `tcs:upstreamDatatype` so the runtime convention survives.

5. `sh:minCount` on a channel parameter → moved to `tcs:upstreamMinCount`. Upstream is right that the parameter is required — of the *running pipeline*. It is not required of the author: `RdfcConfigCompiler` fills a step's `rdfc:reader` / `rdfc:writer` in from the framework-neutral `tcs:readsFrom` / `tcs:writesTo`, so a hand-written config leaves it out on purpose and upstream's cardinality would fail every pipeline in this repo. Rule 1's `sh:class` still constrains whatever the author *does* write — a config may still name a channel where the neutral annotation cannot say which slot is meant, as `rdfc:Sdsify` does with its two outputs. `sh:maxCount` is untouched.

Rules 1 and 2 were reverse-engineered from `:SparqlIngestShape`, the one hand-written shape in the old catalog that actually fired. Rules 3 and 4 only surfaced once generation made every component's shape live — before, eleven of twelve components had no reachable shape at all, so neither problem was observable. Rule 5 surfaced when those live shapes first met a pipeline definition that had stopped restating its channels.

`:ShaclPathShape` accepts the six path forms `ShaclPath` implements in rdf-lens (bare IRI, RDF-list sequence, `sh:alternativePath`, `sh:inversePath`, `sh:zeroOrMorePath`, `sh:zeroOrOnePath`) and deliberately rejects `sh:oneOrMorePath`, which SHACL defines but rdf-lens does not implement — see the note in `catalog-rdfc-manual.ttl`. Validation deliberately matches execution rather than the spec. The accept/reject matrix is the executable spec in [`tests/test_shacl_path_shape.py`](tests/test_shacl_path_shape.py).

**What stays hand-written.** [`data/catalog/catalog-rdfc-manual.ttl`](data/catalog/catalog-rdfc-manual.ttl) holds everything with no upstream source: the orchestrator and its two `tcs:Config` literals (the compose fragment and the Dockerfile), the two runners, and components whose source is not resolvable from a registry or from this repo. Each of the two files contributes its own `dcat:resource` entries to `:DishacledCatalog`, so catalog membership sits next to the definitions it refers to and cannot outlive them.

Scope is RDF-Connect only. LDIO and semantic.works components have no machine-readable upstream definition to derive from, so `catalog-ldio.ttl` and `catalog-sw.ttl` remain hand-written.

Adding a new compiler is a matter of dropping a new file in the appropriate `compilers/<framework>/` subfolder (or `compilers/core/` for framework-agnostic ones), importing it from `compilers/__init__.py`, and adding it to the config(s) — `PipelineGeneratorConfig` in [`compilers/pipeline_generator.py`](src/compilers/pipeline_generator.py), `PipelineValidatorConfig` in [`compilers/pipeline_validator.py`](src/compilers/pipeline_validator.py) — that should run it. `CompilationRunner` picks it up automatically once it appears in the config's `compilers` list; the runner never needs editing. The notebook `demo.ipynb` demonstrates the end-to-end workflow.


## 5. Limitations

A handful of design gaps the current generator does not yet close. All are tracked as concrete items in [§6 Future directions](#6-future-directions).

### 5.1. No override mechanism for `tcs:DefaultConfig` bodies

`tcs:DefaultConfig`s attached to catalog components are read verbatim by the per-service compilers and emitted as-is. There is no way for a pipeline definition to provide a pipeline-specific replacement for (or fragment merged into) a catalog-level default.

This is fine for genuinely stock content (e.g. `virtuoso.ini`, mu-cl-resources' `repository.lisp`) but forces a compromise for the semantic.works Tier-3 files whose demonstrator-specific content currently sits in `catalog-sw.ttl` under a `Default` label it does not fully deserve:

- `MuDeltaNotifierRulesJsDefault` ships the demonstrator's full `rules.js`, including the `rdf:type oslc:Error → error-alert` cross-framework rule. That rule ties the RDF-Connect threshold sink to the sw error-alert service and is specific to the DiSHACLed demonstrator; long-term it should be derived from a `tcs:Channel` between the two frameworks.
- `ErrorAlertTemplateHbsDefault` is the demonstrator's *"UFFFFFF!! Water levels is exceeded!!!"* email body — pure demonstrator content, not a stock error template.
- `ErrorAlertConfigJsonDefault` bakes in the demonstrator's `email.folder` / `graph.email` IRIs.
- `MuAuthorizationConfigLispDefault` bakes in the demonstrator's single-`public`-graph, everyone-read/write ACL.
- `MuDispatcherExDefault` bakes in the demonstrator's minimal `/sparql → database` routes.

Any pipeline that reuses these components today gets the demonstrator's content silently applied. Fixing this cleanly needs (a) a whole-file override predicate that lets a pipeline definition shadow a catalog default, and (b) migration of the demonstrator-specific bodies out of the catalog into the pipeline definition. Both are tracked in [§6 Future directions](#6-future-directions).


### 5.2. Cross-framework channels

HTTP-transport cross-framework channels are fully modelled today: any [tcs:Channel](../../semantic%20model/README.md#channel) whose reader and writer live in different containers gets bridged automatically by `BridgeTransportCompiler` (see [§4.8](#48-boundary-components-and-cross-container-bridges)). What is *not* yet automatic:

- **SPARQL Update bridges** (RDF-Connect's `rdfc:SPARQLIngest` → semantic.works' `sw:mu-identifier`). Both sides are catalog-typed with `tcs:channelType tcs:SparqlUpdateChannel` so the boundary vocabulary is correct, but `BridgeTransportCompiler.default_channel_type` is fixed to `tcs:HttpChannel` for the MVP; the SPARQL-Update hop stays hand-authored.
- **Semantic.works event-driven subscriptions** (mu-delta-notifier rule dispatch). These are not push-over-a-channel at all — they are subscription rules fired by the store's delta bus. The `oslc:Error → error-alert` rule that ties the demonstrator's RDF-Connect threshold sink to sw's error-alert service (see [§5.1](#51-no-override-mechanism-for-tcsdefaultconfig-bodies)) is the concrete case; long-term it should be synthesised from the pipeline definition rather than shipped as boilerplate.

### 5.3. For RDFC-Connect, topology is stated twice

A step *can* state its channel wiring in two places: framework-specifically inside its `tcs:embedded` config (`rdfc:output demo:sdsMeasurements`) and framework-neutrally as `tcs:readsFrom` / `tcs:writesTo`. The compilers read the first, the application-profile shapes read the second. Two inference rules in [`data/rdfc_inference_rules.yaml`](data/rdfc_inference_rules.yaml) now derive `tcs:derivedReadsFrom` / `tcs:derivedWritesTo` from the config plus the generated config shapes, and `tcs:RdfcStepChannelWiringShape` requires that every derived edge was also declared — so a step that wires one channel and annotates another is now a validation error instead of a silent inconsistency.

Both the rules and the shape are RDF-Connect-only and say so, selecting on `?component a rdfc:Processor` — the type the emitter carries over from upstream's `rdfc:{js,py}ImplementationOf rdfc:Processor`. That is also why the rules sit in their own file rather than in `inference_rules.yaml`. **Load both**, or the derivation never runs and the shape passes with nothing to check:

```python
catalog_reader = catalog_reader.infer(input_folder + "inference_rules.yaml")       # framework-neutral
catalog_reader = catalog_reader.infer(input_folder + "rdfc_inference_rules.yaml")  # RDF-Connect
```

The check is one-directional (derived must be a **subset** of declared) because most declared edges are legitimately underivable — and since `RdfcConfigCompiler` learned to generate a step's `rdfc:reader` / `rdfc:writer` wiring from `tcs:readsFrom` / `tcs:writesTo`, most of them no longer *need* to be derivable. The duplication this check was written to police has largely been removed at the source: the demonstrator's configs stopped restating their channels, so there is nothing left to disagree with.

Of 24 declared edges, 4 are now derived and all 4 agree. All four are `demo:SdsifyMeasurements` / `demo:SdsifyViolations`, the only steps whose configs still name channels by hand — `rdfc:Sdsify` has *two* output slots (`rdfc:output` and `rdfc:metadataOutput`), so `tcs:writesTo` alone cannot say which stream goes where and the config has to spell it out. That is exactly the shape of case where the two statements can drift apart, so the narrowed check still covers the part that can actually break. The remaining 20 edges are underivable because:

- **LDIO steps** have no channel-valued parameters at all — topology is implicit in the ordering of the `input` / `input.adapter` / `transformers[]` / `outputs[]` slots, which is what `tcs:LdioStepOrderingShape` enforces. For LDIO, the annotation is the *only* record of the topology.
- **The cross-framework edge** `demo:HttpIngest tcs:readsFrom demo:ldioToRdfcBridge`: `rdfc:HttpServer` receives an HTTP POST, not a channel, so there is no config property behind it. Same root cause as [§5.2](#52-cross-framework-channels).
- **Everything else** now states its wiring once, framework-neutrally, and lets the compiler emit the framework-specific form. Nothing to cross-check is the *good* outcome here.

Fully closing the loop therefore needs the LDIO ordering model and the cross-framework channel model, not more inference.

One sharp edge worth knowing: `tcs:upstreamClass` is overloaded. On a channel property it records the *direction* (`rdfc:Reader` / `rdfc:Writer`); on `tm:path` it records a *translated foreign class* (`rdfl:PathLens`). The inference rules therefore require `sh:class tcs:Channel` as well — keying on `tcs:upstreamClass` alone derives `sosa:hasSimpleResult` as a written channel. There is a regression test for exactly that.



### 5.4. LDIO service definition in the catalog

The LDIO workbench service is currently declared with an outdated image tag (`ldes/ldi-orchestrator:2.8.0-SNAPSHOT`) and no volume mounts. Any generated pipeline needs the operator to hand-patch the emitted compose file to use `2.13.0` + the two bind mounts the actual workbench expects — or to refactor to LDIO's Pattern A1 single-file startup model.

### 5.5. Dependencies with local sources

Catalog components are expected to declare their dependencies as `spdx:Package` nodes reachable from `dct:requires`, which `RdfcDockerFileCompiler` resolves into `pyproject.toml` / `package.json` entries. The assumption baked into this today is that every such dependency is resolvable from its `spdx:downloadLocation` **by the package manager** at build time — i.e. the URL points at a registry (npm, PyPI, ...) or a plain HTTP tarball, not at a source tree that ships alongside the pipeline. Concretely: a `file://` `spdx:downloadLocation` is not fully supported. `RdfcDockerFileCompiler` will still emit the package as a `"*"` (npm) / unpinned (pip) entry — which crashes `npm install` / `pip install` with a "not in registry" error — and `FileMaterializer` never copies the referenced source tree into the emitted project folder.

The demonstrator's `proc:JsonLdToNQuads` is the concrete case: source lives in `demonstrator/RDFC/processors/jsonld-to-nquads/`, and the emitted `rdfc/` output has to be hand-patched to make the pipeline actually build (drop the offending `package.json` entry, copy the folder in, add a bind mount to the compose file). Long-term fix needs three things: teach `RdfcDockerFileCompiler` to emit npm's `file:` / pip's local-path syntax when a `file://` download location is present, teach `FileMaterializer` (or a dedicated compiler) to copy the referenced tree into the output, and add a catalog convention for where the source is anchored relative to the catalog file.

### 5.6. Catalog generation covers only RDF-Connect

[§4.10](#410-generating-the-rdf-connect-catalog) derives `catalog-rdfc.ttl` from upstream packages, but `catalog-ldio.ttl` (1136 lines) and `catalog-sw.ttl` (812 lines) are still transcribed by hand, because neither ecosystem publishes a machine-readable component definition. They therefore keep the drift characteristics the RDF-Connect catalog just lost.

Two smaller gaps inside the RDF-Connect path:

- **`owl:imports` values are unvalidated.** The generated IRIs are only correct while `PYTHON_VERSION` and `CONTAINER_WORKDIR` in `catalog/emitter.py` agree with the `FROM python:...` line and `WORKDIR` in the orchestrator's `tcs:DockerImageConfig`. A test asserts the pair matches, but nothing checks that the resulting path exists in the built image — `tcs:RdfcProcessorShape` only requires `sh:minCount 1` on `owl:imports`.
- **The read/write direction of a channel is preserved but unused.** Rewrite 1 collapses `rdfc:Reader` / `rdfc:Writer` into `tcs:Channel` and records the original on `tcs:upstreamClass`. Nothing consumes that annotation yet; it exists so the cross-framework channel work in [§5.2](#52-cross-framework-channels) does not have to re-derive the direction.
- **`EXTERNAL_SHAPES` has one entry.** `rdfl:PathLens` is the only foreign class whose value space is modelled, because it is the only one currently reached by a `sh:class` in a harvested shape. Any future foreign constraint degrades to an annotation until someone writes its shape — safe, but silently unchecked, so it is worth grepping generated output for `tcs:upstreamClass` without an accompanying `sh:node` when adding components.


## 6. Future directions
The pipeline generator is not fully implemented yet, it is a work in progress. We have the following goals for the year 2026:

- [x] Add support for Pipeline Definitions spanning components of both the RDF-Connect and LDIO framework. This warrants automatic generation of interoperable pipelines.
- [x] Add another framework, [semantic.works](https://semantic.works/).
- [x] PipelineAssembler: Assigns segments of a Pipeline Definition to microservices. It does so by following dependency paths via dct:requires and assigning InstancePipelineComponents to the microservices that instantiate them.
- [x] DockerComposeCompiler: Compiles a DockerCompose file based on the description of the different microservices.
- [x] FileMaterializer: Takes the semantic description of the PipelineBuild and writes the attached `spdx:File` nodes to a folder using their `tcs:filepath` / `tcs:filename`. Lives outside the `Compiler` hierarchy because it is the filesystem boundary, not a graph-to-graph transformation.
- [x] CompilerAssigner: It may be necessary at some point to provide a lookup which compilers need to be called depending on information contained in the graph. So that compilers can be called dynamically based on need. Implemented as the flat `CompilationConfig.compilers` list combined with a per-compiler `applies_to(graph_reader) -> bool` classmethod that declares the triggering pattern, evaluated by `CompilationRunner` in a two-phase fixpoint (finalize compilers gate on `<?> tcs:runPhase tcs:FinalizePhase`).
- [ ] SemanticModelVersionMapper: Can map from one version of the semantic model to the internal model that is used by the pipeline generator. Allows decoupling versioning of the official semantic model and the internal model used for implementation.
- [ ] Pipeline-level config override mechanism: let a `tcs:PipelineDefinition` shadow a catalog-level `tcs:DefaultConfig` with a pipeline-specific body. Prerequisite for cleanly moving the demonstrator-specific bodies (see [§5.1](#51-no-override-mechanism-for-tcsdefaultconfig-bodies)) out of `catalog-sw.ttl` and into `pipeline_definition.ttl`.
- [x] Cross-framework `tcs:Channel`: extend the channel model to describe transports that span framework boundaries. HTTP-transport bridges (LDIO ↔ RDFC) are now handled automatically by `BridgeTransportCompiler` + per-boundary config compilers — see [§4.8](#48-boundary-components-and-cross-container-bridges). SPARQL-Update bridges (RDFC → sw) are catalog-typed with `tcs:channelType tcs:SparqlUpdateChannel` but the MVP `BridgeTransportCompiler.default_channel_type` still fixes them to hand-authored. Semantic.works delta-notifier subscriptions (the `oslc:Error` rule in `delta/rules.js`) remain out of scope — they need a different channel model entirely.
- [x] RdfcDockerFileCompiler: Creates an adhoc Dockerfile for RDF-Connect. This allows to include only those dependencies in a docker container which are actually used in the pipeline.
- [x] Catalog generator for RDF-Connect: derive `catalog-rdfc.ttl` from each package's own published `processors.ttl` instead of transcribing it, so a component entry is a three-line request. Implemented as the `rdfc_catalog_harvest` package with a committed harvest snapshot; see [§4.10](#410-generating-the-rdf-connect-catalog).
- [ ] Extend catalog generation beyond RDF-Connect. LDIO and semantic.works publish no machine-readable component definitions, so there is nothing to derive from today. For LDIO the closest source is the generated documentation site; for semantic.works, the per-service READMEs. Both would need a scraper rather than a parser, which is why they are still hand-written.
- [ ] Deterministic `docker-compose.yml` service order. `DockerComposeCompiler` merges compose fragments in graph-iteration order, so the emitted service order varies between runs on identical input (the service bodies are identical — only key order moves). Harmless to Docker, but it makes the committed `out/` diff noisy and blocks byte-comparison in tests.
- [x] NifiCompiler: Compiler for [Nifi 2](https://nifi.apache.org/). Persisted-flow generation and local/remote deployment selection are implemented; live production verification remains.
- [x] PipelineGenerator: Uses all previously described compilers to route the compilation flow for generating a pipeline project folder based on a Pipeline Definition. Routing is done via the registry + `applies_to` mechanism described above; producing the project folder on disk is handled by `FileMaterializer`.


## 7. How To

### 7.1. How to write your own Pipeline Definition
You can find a couple of examples in the catalog.ttl file in the data folder. A Pipeline Definition is as simple as a bunch of InstancePipelineComponents, linked to each other. Each InstancePipelineComponent receives a Config and is carried out by a component of the catalog. So you do not need a lot to write a Pipeline Definition. This part will be made easier in the future by providing a frontend.


### 6.2. How to onboard your own components to the catalog

**For an RDF-Connect processor, add three lines and run two commands.** The catalog entry is generated from the package's own `processors.ttl` — see [§4.10](#410-generating-the-rdf-connect-catalog). Append to `data/catalog/catalog-rdfc-requests.ttl`:

```turtle
rdfc:MyProcessor a tcs:CatalogRequest ;
    tcs:package "@rdfc/my-processor-ts" ;
    spdx:versionInfo "^1.0.0" .
```

then `python -m rdfc_catalog_harvest harvest && python -m rdfc_catalog_harvest generate`. If the package ships several Turtle files and auto-detection picks the wrong one, add `tcs:sourceFile "configs/mine.ttl"`. If the source is not published, vendor it into the repo and point `tcs:fromPath` at it plus `spdx:downloadLocation` at the container path. Harvesting fails loudly if the component is not declared in the package, which is what catches an upstream rename.

**For everything else** (LDIO, semantic.works, or an RDF-Connect component with no resolvable source) the catalog is hand-written. Check the catalog in the data folder for examples. At a minimum, a Pipeline Component needs either a DockerComposeConfig or a dependency to a component with a DockerComposeConfig. The assumption is that each DockerComposeConfig resolves all dependencies of components attached to it, including itself. Depending on the framework, you may also need to include framework-specific properties. For example, the LdioConfigCompiler needs to be able to look up ldio:type and rdf:label, whereas the RdfcConfigCompiler needs to be able to look up owl:imports. This is explicitly mentioned in the sh:NodeShapes a tcs:Compiler points to. Everything else is optional, but it is a good idea to include as many NodeShapes as possible: It serves as a lookup reference for the constraints that need to be fulfilled once a component is included in a pipeline. A typical NodeShape for a tcs:PipelineComponent is for example the schema of its expected Config.


### 7.3. How to onboard new frameworks
- Describe pipeline components with the semantic model and add them to the catalog.
- Write compilers for your new framework that can produce the expected output files. Each compiler must subclass `Compiler` (from `compilers.compiler_abc`), implement `compile(self) -> Graph` returning the enriched build graph, and override `applies_to(cls, graph_reader) -> bool` to declare the graph-state conditions under which the compiler should run — typically the presence of a framework-specific node type or predicate in the build graph. The default `applies_to` returns `False`, so this override is required. If your compiler must run only after other shaping compilers have finished (for example because it consumes their output), the two-step convention is to gate its `applies_to` on a `<build> dct:creator tcs:<UpstreamCompiler>` triple (see §4.6). For a compiler that must run against the *fully* shaped build (e.g. it needs to see every generated file), gate its `applies_to` on `<?> tcs:runPhase tcs:FinalizePhase` — the marker `CompilationRunner` attaches between its two fixpoint passes — so it only becomes eligible after every shaping compiler has settled. That is the same pattern `ValidationReportCompiler` and `DockerComposeCompiler` use (§4.4).
- Compilers that produce a file should end their `compile()` method with a call to `attach_file(self.output_reader, filename=..., filepath=..., content=...)` (from `compilers.utils`), re-assigning `self.output_reader` with the returned reader. The helper adds an `spdx:File` node to the `tcs:PipelineBuild` carrying the produced file body as a literal.
- Import the new compiler from `compilers/__init__.py` and add it to the appropriate config — `PipelineGeneratorConfig` in [`compilers/pipeline_generator.py`](src/compilers/pipeline_generator.py), `PipelineValidatorConfig` in [`compilers/pipeline_validator.py`](src/compilers/pipeline_validator.py) — typically both `compilers` lists, or just one of them if the compiler is emitter-only (generation) or validation-only. `CompilationRunner` then runs it automatically against any pipeline whose build graph matches its `applies_to` condition. New compilers usually belong in a per-framework subfolder (`compilers/<framework>/`); framework-agnostic ones go in `compilers/core/`. No edits to `CompilationRunner` are required.
- Keep anything framework-specific visibly framework-specific, the way RDF-Connect does: inference rules in `data/<framework>_inference_rules.yaml` rather than in `inference_rules.yaml`, application-profile shapes under §2 of `catalog-application-profile-shapes.ttl` with a `tcs:<Framework>…Shape` name and a discriminator in their `sh:target`, and any harvesting code under `src/<framework>_catalog_harvest/`. A rule or shape that reads a framework's own config layout but targets `tcs:InstancePipelineComponent` will be inherited by the next framework by accident.
- Test the new compilers based on a Pipeline Definition that includes components of the new framework.

#### Container / multi-tenancy contract

`PipelineAssembler.describe_docker_container()` mints exactly one `tcs:DockerContainer` per catalog `tcs:PipelineComponent` that owns a `tcs:DockerComposeConfig` (a "microservice"), and folds every component that transitively `dct:requires` it into that same container. There is no mechanism to run N separate containers of the same catalog component to isolate concurrent pipelines/segments from each other, and container assignment never depends on pipeline-definition-specific structure — only on the catalog's own `dct:requires` graph.

Because of this, a component is either genuinely multi-tenant (the underlying runtime the container image starts — LDIO's orchestrator, RDF-Connect's runner — is itself designed to host several independent pipelines/environments side by side, so multiple `InstancePipelineComponent`s safely sharing one container is exactly what the runtime is for), or it must only ever be instanced once. LDIO and RDF-Connect both qualify as multi-tenant; semantic.works components do not — each owns exactly one static per-service config file with zero per-instance aggregation.

**A new framework doesn't need to declare which of the two it is** —
instead, if it does *not* support multi-tenancy, the catalog must carry a SHACL shape that flags `>1 InstancePipelineComponent` specializing the same component of that framework as unsupported, so reuse is caught at validation time instead of silently misbehaving at runtime (the same approach planned for semantic.works — see [`docs/pipeline-segments-plan.md`](docs/pipeline-segments-plan.md)). A multi-tenant framework's compiler instead needs to be able to emit one config artifact *per segment* sharing one container (LDIO's answer: Pattern A2 directory-scan, one file per segment).




