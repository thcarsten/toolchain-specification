# Table Of Contents

[1. Introduction](#1-introduction) <br>
[2. Installation](#2-installation) <br>
[3. Workflow](#3-workflow) <br>
[4. Architecture](#4-architecture) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.1. Module overview](#41-module-overview) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.2. The Compiler ABC](#42-the-compiler-abc) <br>
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[4.2.1. Method naming & single-responsibility splitting](#421-method-naming--single-responsibility-splitting) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.3. Auto-registration](#43-auto-registration) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.4. PipelineGenerator: the fixpoint loop](#44-pipelinegenerator-the-fixpoint-loop) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.5. File attachment: the spdx:File vocabulary](#45-file-attachment-the-spdxfile-vocabulary) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.6. Provenance: dct:creator attachment](#46-provenance-dctcreator-attachment) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.7. Compiler responsibilities at a glance](#47-compiler-responsibilities-at-a-glance) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.8. Boundary components and cross-container bridges](#48-boundary-components-and-cross-container-bridges) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.9. NiFi deployment target](#49-nifi-deployment-target) <br>
[5. Limitations](#5-limitations) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[5.1. No override mechanism for tcs:DefaultConfig bodies](#51-no-override-mechanism-for-tcsdefaultconfig-bodies) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[5.2. Cross-framework channels](#52-cross-framework-channels) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[5.3. LDIO service definition in the catalog](#53-ldio-service-definition-in-the-catalog) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[5.4. Test-suite runner](#54-test-suite-runner) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[5.5. Dependencies with local sources](#55-dependencies-with-local-sources) <br>
[6. Future directions](#6-future-directions) <br>
[7. How To](#7-how-to) <br>

## 1. Introduction
In this repo you find the codebase for the tool "pipeline generator". As the name suggests, the pipeline generator automatically generates pipelines based on a semantic description of a pipeline. The pipeline generator accepts pipeline definitions which are written in RDF and follow the [semantic model](https://github.com/thcarsten/toolchain-specification/tree/main/semantic%20model) of the toolchain specification. Based on the pipeline definition, it looks up components and their dependencies in a component catalog, and generates a Docker Compose file wiring together the containers that resolve these dependencies. It also generates the framework-specific configuration files necessary to run the pipelines. In the [data-folder](https://github.com/thcarsten/toolchain-specification/tree/main/pipeline%20generator/data), you can find the pipeline definitions and the catalog used for [the demo](https://github.com/thcarsten/toolchain-specification/blob/main/pipeline%20generator/src/demo.ipynb). Currently three frameworks are supported: RDF-Connect, LDIO and semantic.works.

The codebase is found in the [src-folder](https://github.com/thcarsten/toolchain-specification/tree/main/pipeline%20generator/src). It consists of two packages: `rdfine` provides ergonomic graph IO and transformation primitives (see its own [README](src/rdfine/README.md)); `compilers` is the pipeline generator itself, built around a small `Compiler` ABC and a self-registering dispatch system orchestrated by `PipelineGenerator`. Section 4 describes the architecture in detail.

## 2. Installation

The pipeline generator targets **Python 3.11+** and consists of two packages that live under `src/`: `rdfine` and `compilers`.

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

## 3. Workflow
The `PipelineGenerator` class wraps the full compilation flow. After loading the catalog into a `GraphReader` and enriching it with inference rules, instantiate `PipelineGenerator` with the id of the pipeline to compile and the catalog graph, then call `compile()`:

```python
from compilers import PipelineGenerator

gen = PipelineGenerator(":DemonstratorPipeline", catalog_graph)
build_graph = gen.compile()
```

The returned `build_graph` contains both the semantic description of the pipeline build and the compiled files as `spdx:File` nodes attached to the `tcs:PipelineBuild` via `tcs:compiledFile` (with `tcs:filename`, `tcs:filepath`, and `tcs:literal` carrying the file body). The build graph is therefore self-describing. To materialize it to disk, hand it to `ProjectBuilder`:

```python
from compilers import ProjectBuilder

ProjectBuilder(build_graph).write("./out/dishacled-full")
```

Internally, `PipelineGenerator` runs one bootstrap step, then a fixpoint loop over the compiler registry, then two explicit finalize calls:

1. **Bootstrap** — `PipelineSeeder` is instantiated explicitly because it is the only compiler that takes the `pipeline_id` in its constructor. It seeds the `tcs:PipelineBuild` node (linked to the definition via `prov:hadPlan`) and renames any blank-node subjects to stable IRIs so later compilers have named targets to attach to.
2. **Fixpoint loop** — every iteration, `PipelineGenerator` asks every not-yet-run registered `Compiler` subclass whether its `applies_to` trigger is satisfied by the current build graph, and runs those that are. The loop terminates when a full scan finds nothing eligible.
3. **Explicit finalize calls** — `ValidationReportCompiler` and `DockerComposeCompiler` are removed from the registry via a `is_explicit_call = True` class attribute and invoked directly by `PipelineGenerator` after the loop terminates. They run against a fully-shaped build graph so ordering is fixed and unambiguous. See §4.4 for the mechanics.

The execution order therefore emerges from the trigger conditions rather than from any class-level rank. After compilation, `gen.compilers` maps each compiler class to the instance that ran, in insertion order — so it doubles as a record of the compile order. Every executed compiler is also attached to the build via `dct:creator`, giving the same information in the graph itself.

Adding a new compiler is a matter of dropping a new file in the appropriate `compilers/<framework>/` subfolder (or `compilers/core/` for framework-agnostic ones) and importing it from `compilers/__init__.py`. `PipelineGenerator` discovers it via `Compiler._registry` and runs it whenever its `applies_to` returns `True`; the generator never needs editing. The notebook `demo.ipynb` demonstrates the end-to-end workflow.

## 4. Architecture

This section is a closer look at the `compilers` package. (`rdfine` has its own [README](src/rdfine/README.md).)

### 4.1. Module overview

| Module | Exported symbols | Purpose |
| --- | --- | --- |
| [base.py](src/compilers/base.py) | `Compiler` | Abstract base class, auto-registry, `applies_to` contract (default `False`), `compile` contract, and the `is_explicit_call` opt-out flag for compilers that should be invoked directly by `PipelineGenerator` instead of picked by the loop. |
| [pipeline_generator.py](src/compilers/pipeline_generator.py) | `PipelineGenerator` | End-to-end driver: bootstrap + fixpoint loop + explicit finalize calls. Writes `dct:creator` provenance triples after each compiler runs. |
| [project_builder.py](src/compilers/project_builder.py) | `ProjectBuilder` | Write the `spdx:File` nodes of a compiled build graph to disk. Not a `Compiler` subclass — this is the filesystem boundary. |
| [utils.py](src/compilers/utils.py) | (internal) `attach_file`, `extract_config`, `read_literal`, `parse_docker_compose_config`, `lookup_container_service_name` | Compiler-side helpers that encode knowledge of the semantic model — including the `spdx:File` attachment helper called by file-producing compilers. |
| [core/pipeline_seeder.py](src/compilers/core/pipeline_seeder.py) | `PipelineSeeder` | Bootstrap: seed the `tcs:PipelineBuild` skeleton (`prov:hadPlan`-linked to the definition) and rename blank-node subjects to stable IRIs. |
| [core/pipeline_assembler.py](src/compilers/core/pipeline_assembler.py) | `PipelineAssembler` | Materialize the `tcs:DockerContainer` / step / config skeleton onto the seeded `tcs:PipelineBuild`. |
| [core/segment_tagger.py](src/compilers/core/segment_tagger.py) | `SegmentTagger` | Tag every `tcs:InstancePipelineComponent` with the `tcs:segment` it belongs to (a maximal chain within one container). Used by segment-scoped SHACL shapes and per-segment framework compilers. |
| [core/graph_reducer.py](src/compilers/core/graph_reducer.py) | `GraphReducer` | Narrow the build graph down to just the triples reachable from the seeded `tcs:PipelineBuild`, after `BridgeTransportCompiler` has finished touching the catalog. |
| [core/bridge_transport_compiler.py](src/compilers/core/bridge_transport_compiler.py) | `BridgeTransportCompiler` | Auto-insert Entry/Exit boundary steps for any cross-container `tcs:Channel` whose author didn't hand-declare a bridge. See [§4.8](#48-boundary-components-and-cross-container-bridges). |
| [core/validation_report_compiler.py](src/compilers/core/validation_report_compiler.py) | `ValidationReportCompiler` | **Finalize call.** Run SHACL over the fully-shaped build + shapes graph and attach the report at `validation/validation-report.ttl`. |
| [core/docker_compose_compiler.py](src/compilers/core/docker_compose_compiler.py) | `DockerComposeCompiler` | **Finalize call.** Aggregate every `tcs:DockerComposeConfig` on the build into the top-level `docker-compose.yml`. |
| [ldio/config_compiler.py](src/compilers/ldio/config_compiler.py) | `LdioConfigCompiler` | Emit one LDIO pipeline YAML per `tcs:segment` under `ldio/pipelines/<segment>.yml`, plus a companion `ldio/application.yml` pointing the orchestrator at that directory (Pattern A2 directory-scan). |
| [ldio/http_in_config_compiler.py](src/compilers/ldio/http_in_config_compiler.py) | `LdioHttpInConfigCompiler` | Per-boundary config compiler: writes `tcs:endpoint` onto the channel a `ldio:HttpIn` step reads from. |
| [ldio/http_out_config_compiler.py](src/compilers/ldio/http_out_config_compiler.py) | `LdioHttpOutConfigCompiler` | Per-boundary config compiler: reads `tcs:endpoint` off the channel a `ldio:HttpOut` step writes to and folds it into the step's config. |
| [nifi/config_compiler.py](src/compilers/nifi/config_compiler.py) | `NifiConfigCompiler` | Produce NiFi `flow.json` and local post-start configuration artifacts. |
| [nifi/dockerfile_compiler.py](src/compilers/nifi/dockerfile_compiler.py) | `NifiDockerfileCompiler` | Produce the local NiFi Dockerfile. |
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
    Compiler, PipelineGenerator, ProjectBuilder,
    PipelineSeeder, PipelineAssembler, PipelineEnricher,
    BridgeTransportCompiler, SegmentTagger, GraphReducer,
    ValidationReportCompiler, DockerComposeCompiler,
    LdioConfigCompiler, LdioHttpInConfigCompiler, LdioHttpOutConfigCompiler,
    RdfcConfigCompiler, RdfcDockerFileCompiler,
    RdfcHttpServerConfigCompiler, RdfcHttpOutConfigCompiler,
    SemanticWorksEnvVarCompiler,
    NifiConfigCompiler, NifiDockerfileCompiler, NifiRemoteCompiler,
    VirtuosoCompiler, MuClResourcesCompiler, MuDispatcherCompiler,
    MuDeltaNotifierCompiler, MuAuthorizationCompiler, ErrorAlertCompiler,
)
```

### 4.2. The `Compiler` ABC

Every concrete compiler inherits from `Compiler` and must satisfy a small contract:

- `__init__(graph: Graph)` — takes the build graph it operates on. The base implementation wraps it in a `GraphReader` stored on `self.graph_reader`. Subclasses that need extra arguments (currently only `PipelineSeeder`, which takes a `pipeline_id`) extend the signature and call `super().__init__(graph)`.
- `compile(self) -> Graph` — the compiler's transformation. Runs the graph-shaping work and returns the enriched build graph. Heavy lifting belongs here, not in `__init__` — this way the work happens predictably at one moment in time and stays composable in the fixpoint loop.
- `applies_to(cls, graph_reader: GraphReader) -> bool` (classmethod) — declares the graph-state condition under which the compiler should run. **The default returns `False`**, so every concrete compiler must override it. Typical shapes:
  ```python
  @classmethod
  def applies_to(cls, graph_reader: GraphReader) -> bool:
      return not graph_reader.filter(
          pred="tcs:instantiates",
          obj="ldio:LinkedDataInteractionsOrchestrator",
      ).df.empty
  ```
  A compiler that needs to see which other compilers already ran can inspect the `dct:creator` triples on the build (see §4.6).
- `is_explicit_call` (classmethod class attribute, default `False`) — setting this to `True` opts the compiler out of registry auto-discovery. `PipelineGenerator` then must invoke it directly at a fixed point in `compile()` — currently used only for `ValidationReportCompiler` and `DockerComposeCompiler`, which run against the fully-shaped build after the fixpoint loop terminates. See §4.4.
- `compiler_iri(cls) -> str` (classmethod) — the IRI used when `PipelineGenerator` records this compiler as `dct:creator`. Defaults to `tcs:<ClassName>`; override if a catalog-backed IRI is preferred.

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

### 4.3. Auto-registration

`Compiler` keeps a class-level `_registry: list[type[Compiler]]`. On every subclass definition `__init_subclass__` appends the new class (skipping abstract intermediates):

```python
class Compiler(ABC):
    _registry: ClassVar[list[type["Compiler"]]] = []

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        if not inspect.isabstract(cls):
            Compiler._registry.append(cls)
```

The side effect runs the first time the subclass's module is imported. [`compilers/__init__.py`](src/compilers/__init__.py) imports every compiler module precisely so the registry is fully populated by the time any user-facing code asks for it.

### 4.4. PipelineGenerator: the fixpoint loop

`PipelineGenerator` is the entry point that turns a `(pipeline_id, catalog_graph)` pair into a fully compiled build graph. Its `compile()` method has three phases: bootstrap, fixpoint loop, and explicit finalize calls.

**Bootstrap.** `PipelineSeeder` is instantiated explicitly because it is the only compiler whose constructor needs the `pipeline_id`:

```python
seeder = PipelineSeeder(self.pipeline_id, self.catalog_graph)
self.build = seeder.compile()
```

`PipelineSeeder` seeds the `tcs:PipelineBuild` node itself:

```turtle
<pipeline>_build a tcs:PipelineBuild ;
                 prov:hadPlan <pipeline> .
```

and renames any blank-node subjects (typed by RDFS entailment as pipeline components, configs, channels, and shapes) to stable IRIs so downstream compilers always have named targets to attach triples to. Unlike a later `GraphReducer` pass, the seeder does *not* narrow the catalog down — the full catalog stays visible until after `BridgeTransportCompiler` runs, so that compiler can freely draw on components no step has specialized yet (see §4.8).

**Fixpoint loop.** Every iteration, `PipelineGenerator` scans `Compiler._registry`, filters out compilers that have already run, and evaluates the remaining ones' `applies_to` against the current build graph:

```python
while True:
    eligible = [cls for cls in Compiler._registry
                if cls not in ran and cls.applies_to(GraphReader(self.build))]
    if not eligible:
        break
    for cls in eligible:
        instance = cls(self.build)
        self.build = instance.compile()
        self._record_creator(cls)   # attach dct:creator + type
        ran.add(cls)
```

Because triggers are evaluated against the growing graph, execution order emerges naturally: a compiler runs as soon as its trigger becomes true. Compilers eligible in the same iteration are treated as commutative and run in registry order within that pass; before the next iteration the loop re-scans, so any new eligibility introduced by their combined effect is picked up on the next round.

Adding a new compiler is therefore a matter of dropping a new file in the appropriate `compilers/<framework>/` subfolder (or `compilers/core/` for framework-agnostic ones) and importing it from `compilers/__init__.py`. `PipelineGenerator` discovers it via the registry and runs it as soon as its `applies_to` returns `True`. No edits to `PipelineGenerator` are needed.

**Explicit finalize calls.** Two compilers need to see a *fully shaped* build graph, so they are removed from the registry loop entirely and invoked directly by `PipelineGenerator` after the loop terminates:

```python
for finalize_cls in (ValidationReportCompiler, DockerComposeCompiler):
    instance = finalize_cls(self.build)
    self.build = instance.compile()
    self._record_creator(finalize_cls)
```

- `ValidationReportCompiler` runs first — merges the fully shaped build with the untouched shapes graph, runs SHACL, and attaches the report at `validation/validation-report.ttl`.
- `DockerComposeCompiler` runs last — every `tcs:DockerComposeConfig` on the build has been finalized by earlier compilers, so it just aggregates them into a single `docker-compose.yml`.

A compiler opts out of the registry loop by setting `is_explicit_call = True` on the class body; `Compiler.__init_subclass__` skips it during registry population. Adding a new finalize compiler is a matter of that flag plus a direct call from `PipelineGenerator.compile()`.

**Inspection after the run.** The instances that ran are kept on `gen.compilers`, keyed by class, in insertion order. So `list(gen.compilers)` doubles as the compile order. The same information is also in the build graph as `dct:creator` triples on the `tcs:PipelineBuild`.

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

After `PipelineGenerator.compile()` returns, the build graph is fully self-describing: `ProjectBuilder` iterates over the `spdx:File` nodes and writes each to disk at `tcs:filepath / tcs:filename` with body `tcs:literal`. It collects the file records into a `pandas.DataFrame` on `builder.files` first, so the planned writes can be inspected before touching the filesystem. A path-traversal guard rejects any `tcs:filepath` that would escape the target directory.

### 4.6. Provenance: dct:creator attachment

Every compiler that runs is recorded on the build graph as a `dct:creator`. `PipelineGenerator` writes the triples itself, immediately after each compiler's `compile()` returns:

```turtle
:build dct:creator tcs:<ClassName> .
tcs:<ClassName> a tcs:Compiler .
```

Because the seeder creates the `tcs:PipelineBuild` node at the very start, the build always exists by the time provenance is written — no buffering or two-step attachment is needed. The compiler IRI defaults to `tcs:<ClassName>` and can be overridden per compiler via the `compiler_iri()` classmethod (e.g. to point at a catalog-backed entry).

A useful side effect: because provenance is attached *while the loop is running*, every subsequent `applies_to` invocation can inspect `dct:creator` on the build to check which compilers have already run — no extra bookkeeping needed.

### 4.7. Compiler responsibilities at a glance

| Compiler | Trigger (`applies_to`) | Reads from the build | Writes to the build |
| --- | --- | --- | --- |
| `PipelineSeeder` | *(bootstrap — explicit call, always runs first)* | the catalog | `<pipeline>_build a tcs:PipelineBuild ; prov:hadPlan <pipeline>`; blank-node subjects renamed to stable IRIs |
| `PipelineAssembler` | exactly one `tcs:PipelineDefinition` in the graph and it has at least one step (`p-plan:isStepOfPlan`) | the seeded pipeline + catalog | `tcs:DockerContainer`, `dct:hasPart`, `tcs:instantiates`, `tcs:runs` |
| `PipelineEnricher` | `<build> dct:creator tcs:PipelineAssembler` present | steps and channels | synthesized `tcs:Channel`s from `p-plan:isPrecededBy`, and a `tcs:PipelineConfig` slot on every step that lacks one |
| `BridgeTransportCompiler` | `<build> dct:creator tcs:PipelineEnricher` present, and some `tcs:Channel` crosses container boundaries | cross-container channels + the catalog of boundary components | inserted Entry/Exit boundary steps (where neither side is already a boundary). See [§4.8](#48-boundary-components-and-cross-container-bridges). |
| `SegmentTagger` | `<build> dct:creator tcs:BridgeTransportCompiler` present | the final step → channel graph | `tcs:segment` on every `tcs:InstancePipelineComponent` |
| `GraphReducer` | `<build> dct:creator tcs:SegmentTagger` present | the full build graph | narrows the build down to just triples reachable from `<build>` and its shape subgraph |
| `RdfcHttpServerConfigCompiler` | any `rdfc:HttpServer` step present | the step and its channel | `tcs:endpoint`/`tcs:port` on the channel; `rdfc:port`/`rdfc:options` on the step |
| `RdfcHttpOutConfigCompiler` | any `rdfc:HttpOut` step present with an outgoing channel carrying `tcs:endpoint` | the step and its channel | `rdfc:endpoint` on the step |
| `LdioHttpInConfigCompiler` | any `ldio:HttpIn` step present | the step and its channel | `tcs:endpoint` on the channel |
| `LdioHttpOutConfigCompiler` | any `ldio:HttpOut` step present with an outgoing channel carrying `tcs:endpoint` | the step and its channel | `ldio:endpoint` and `ldio:rdf-writer` on the step |
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
| `NifiConfigCompiler` | a container instantiates `nifi:Orchestrator` | NiFi steps, component metadata, configs and channels | persisted `spdx:File` named `nifi/flow.json`; for local builds, secret references also produce a one-shot `nifi-configure` service and `nifi/configure_local.py` |
| `NifiDockerfileCompiler` | local NiFi deployment and a NiFi `tcs:DockerImageConfig` is present | the NiFi Dockerfile literal | `spdx:File` named `nifi/Dockerfile` |
| `NifiRemoteCompiler` | `nifi:deploymentMode "remote"` and `nifi/flow.json` is present | the persisted flow, deployment config, secret references and NiFi compose config | upload-format `nifi/flow_definition.json`, stdlib-only `nifi/deploy_flow.py`, and a one-shot deployer using native Compose secret mounts |
| `ValidationReportCompiler` | *(finalize — explicit call, `is_explicit_call = True`)* | the fully shaped build + shapes graph | `spdx:File` named `validation/validation-report.ttl` |
| `DockerComposeCompiler` | *(finalize — explicit call, `is_explicit_call = True`)* | every `tcs:DockerComposeConfig` reachable from the build | `spdx:File` named `./docker-compose.yml` |

### 4.8. Boundary components and cross-container bridges

A `tcs:Channel` connects an [InstancePipelineComponent](../../semantic%20model/README.md#instancepipelinecomponent) that writes to it with one or more that read from it. When both live in the same container, nothing special is needed — the framework's own runtime carries the data. When they live in *different* containers, an explicit transport hop is required. The pipeline generator handles this via **boundary components** and an auto-insertion compiler.

**Boundary components.** A catalog component typed either [tcs:EntryBoundaryComponent](../../semantic%20model/README.md#entryboundarycomponent) (a receiver, e.g. `rdfc:HttpServer`, `ldio:HttpIn`) or [tcs:ExitBoundaryComponent](../../semantic%20model/README.md#exitboundarycomponent) (a sender, e.g. `rdfc:HttpOut`, `ldio:HttpOut`) declares which transport it speaks via [tcs:channelType](../../semantic%20model/README.md#tcschanneltype), pointing at a subclass of `tcs:Channel` (e.g. `tcs:HttpChannel`, `tcs:SparqlUpdateChannel`). Both flags travel through pure RDFS subclass entailment: any component typed `EntryBoundaryComponent` is also a `PipelineComponent`, and any channel typed `HttpChannel` is also a `Channel`.

**`BridgeTransportCompiler`** examines every `tcs:Channel` whose reader and writer end up in different containers and picks one of four behaviours:

1. **Both sides already boundary steps** (hand-authored bridge, e.g. the demonstrator's `LdioForward → HttpIngest` hop). Leaves the channel alone — the pipeline author has fully described the bridge.
2. **Neither side is a boundary step.** Auto-inserts a matching Entry/Exit pair from the catalog — an ExitBoundary just before the writing side, an EntryBoundary just after the reading side — picked by matching `tcs:channelType` against the compiler's `default_channel_type` (fixed to `tcs:HttpChannel` for the MVP; SPARQL-Update bridges therefore stay hand-authored, see [§5.2](#52-cross-framework-channels)). Onboarding a new HTTP-boundary component is a catalog-only change.
3. **Exactly one side is a boundary step.** Raises: a bridge is half-declared and the author's intent is ambiguous — either declare both sides, or declare neither.
4. **No boundary component in the catalog matches** the required `tcs:channelType`. Flagged pre-compile by `tcs:CatalogMissingBridgeShape`, so the pipeline is rejected at SHACL time rather than crashing the compiler.

**Per-boundary config compilers** (`RdfcHttpServerConfigCompiler`, `LdioHttpInConfigCompiler`, `RdfcHttpOutConfigCompiler`, `LdioHttpOutConfigCompiler`) then handle the transport-metadata layer. Each Entry-side compiler allocates its own port + endpoint (framework-fixed default, bumped on collision) and writes them onto the shared channel as [tcs:endpoint](../../semantic%20model/README.md#tcsendpoint) / [tcs:port](../../semantic%20model/README.md#tcsport); each Exit-side compiler reads those from the channel to configure its own step. Entry and Exit therefore stay fully decoupled — the graph is the message bus.

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

Populate every referenced variable from the current shell, `.env`, or a CI secret store, then run `docker compose up nifi-deploy`. For the example overlay these are `DSH_USERNAME`, `DSH_PASSWORD`, `AZURE_STORAGE_ACCOUNT_NAME`, and `AZURE_SAS_TOKEN`. Compose automatically loads `.env` beside the generated `docker-compose.yml`; the generated `.env.example` lists every required name and can be copied as a starting point. See [`data/pipeline_definition_nifi.deployment.ttl`](data/pipeline_definition_nifi.deployment.ttl) for the reference-only deployment overlay.

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

### 5.3. LDIO service definition in the catalog

The LDIO workbench service is currently declared with an outdated image tag (`ldes/ldi-orchestrator:2.8.0-SNAPSHOT`) and no volume mounts. Any generated pipeline needs the operator to hand-patch the emitted compose file to use `2.13.0` + the two bind mounts the actual workbench expects — or to refactor to LDIO's Pattern A1 single-file startup model.

### 5.4. Test-suite runner

Static SHACL validation of a pipeline definition against the tcs application profile can be run today via `GraphReader.validate()` on the merged catalog + pipeline graph — the demo notebook does exactly this, and `ValidationReportCompiler` runs the same check in-process as an explicit finalize step of every generator run, attaching the report to the build at `validation/validation-report.ttl`. What is not yet built is the pre-generator runner design captured in [`test suite/README.md`](../../test%20suite/README.md), which additionally invokes the native Python shape-matching library (in development by a colleague) for input/output shape validation.

### 5.5. Dependencies with local sources

Catalog components are expected to declare their dependencies as `spdx:Package` nodes reachable from `dct:requires`, which `RdfcDockerFileCompiler` resolves into `pyproject.toml` / `package.json` entries. The assumption baked into this today is that every such dependency is resolvable from its `spdx:downloadLocation` **by the package manager** at build time — i.e. the URL points at a registry (npm, PyPI, ...) or a plain HTTP tarball, not at a source tree that ships alongside the pipeline. Concretely: a `file://` `spdx:downloadLocation` is not fully supported. `RdfcDockerFileCompiler` will still emit the package as a `"*"` (npm) / unpinned (pip) entry — which crashes `npm install` / `pip install` with a "not in registry" error — and `ProjectBuilder` never copies the referenced source tree into the emitted project folder.

The demonstrator's `proc:JsonLdToNQuads` is the concrete case: source lives in `demonstrator/RDFC/processors/jsonld-to-nquads/`, and the emitted `rdfc/` output has to be hand-patched to make the pipeline actually build (drop the offending `package.json` entry, copy the folder in, add a bind mount to the compose file). Long-term fix needs three things: teach `RdfcDockerFileCompiler` to emit npm's `file:` / pip's local-path syntax when a `file://` download location is present, teach `ProjectBuilder` (or a dedicated compiler) to copy the referenced tree into the output, and add a catalog convention for where the source is anchored relative to the catalog file.

## 6. Future directions
The pipeline generator is not fully implemented yet, it is a work in progress. We have the following goals for the year 2026:

- [x] Add support for Pipeline Definitions spanning components of both the RDF-Connect and LDIO framework. This warrants automatic generation of interoperable pipelines.
- [x] Add another framework, [semantic.works](https://semantic.works/).
- [x] PipelineAssembler: Assigns segments of a Pipeline Definition to microservices. It does so by following dependency paths via dct:requires and assigning InstancePipelineComponents to the microservices that instantiate them.
- [x] DockerComposeCompiler: Compiles a DockerCompose file based on the description of the different microservices.
- [x] ProjectBuilder: Takes the semantic description of the PipelineBuild and writes the attached `spdx:File` nodes to a folder using their `tcs:filepath` / `tcs:filename`. Lives outside the `Compiler` hierarchy because it is the filesystem boundary, not a graph-to-graph transformation.
- [x] CompilerAssigner: It may be necessary at some point to provide a lookup which compilers need to be called depending on information contained in the graph. So that compilers can be called dynamically based on need. Implemented as a registry on `Compiler._registry` (auto-populated via `__init_subclass__`) combined with a per-compiler `applies_to(graph_reader) -> bool` classmethod that declares the triggering pattern.
- [ ] SemanticModelVersionMapper: Can map from one version of the semantic model to the internal model that is used by the pipeline generator. Allows decoupling versioning of the official semantic model and the internal model used for implementation.
- [ ] Pipeline-level config override mechanism: let a `tcs:PipelineDefinition` shadow a catalog-level `tcs:DefaultConfig` with a pipeline-specific body. Prerequisite for cleanly moving the demonstrator-specific bodies (see [§5.1](#51-no-override-mechanism-for-tcsdefaultconfig-bodies)) out of `catalog-sw.ttl` and into `pipeline_definition.ttl`.
- [x] Cross-framework `tcs:Channel`: extend the channel model to describe transports that span framework boundaries. HTTP-transport bridges (LDIO ↔ RDFC) are now handled automatically by `BridgeTransportCompiler` + per-boundary config compilers — see [§4.8](#48-boundary-components-and-cross-container-bridges). SPARQL-Update bridges (RDFC → sw) are catalog-typed with `tcs:channelType tcs:SparqlUpdateChannel` but the MVP `BridgeTransportCompiler.default_channel_type` still fixes them to hand-authored. Semantic.works delta-notifier subscriptions (the `oslc:Error` rule in `delta/rules.js`) remain out of scope — they need a different channel model entirely.
- [x] RdfcDockerFileCompiler: Creates an adhoc Dockerfile for RDF-Connect. This allows to include only those dependencies in a docker container which are actually used in the pipeline.
- [ ] NifiCompiler: Compiler for [Nifi 2](https://nifi.apache.org/). Persisted-flow generation and local/remote deployment selection are implemented; live production verification remains.
- [x] PipelineGenerator: Uses all previously described compilers to route the compilation flow for generating a pipeline project folder based on a Pipeline Definition. Routing is done via the registry + `applies_to` mechanism described above; producing the project folder on disk is handled by `ProjectBuilder`.


## 7. How To

### 7.1. How to write your own Pipeline Definition
You can find a couple of examples in the catalog.ttl file in the data folder. A Pipeline Definition is as simple as a bunch of InstancePipelineComponents, linked to each other. Each InstancePipelineComponent receives a Config and is carried out by a component of the catalog. So you do not need a lot to write a Pipeline Definition. This part will be made easier in the future by providing a frontend.


### 7.2. How to onboard your own components to the catalog
Check the catalog in the data folder for examples. At a minimum, a Pipeline Component needs either a DockerComposeConfig or a dependency to a component with a DockerComposeConfig. The assumption is that each DockerComposeConfig resolves all dependencies of components attached to it, including itself. Depending on the framework, you may also need to include framework-specific properties. For example, the LdioConfigCompiler needs to be able to look up ldio:type and rdf:label, whereas the RdfcConfigCompiler needs to be able to look up owl:imports. This is explicitly mentioned in the sh:NodeShapes a tcs:Compiler points to. Everything else is optional, but it is a good idea to include as many NodeShapes as possible: It serves as a lookup reference for the constraints that need to be fulfilled once a component is included in a pipeline. A typical NodeShape for a tcs:PipelineComponent is for example the schema of its expected Config.


### 7.3. How to onboard new frameworks
- Describe pipeline components with the semantic model and add them to the catalog.
- Write compilers for your new framework that can produce the expected output files. Each compiler must subclass `Compiler` (from `compilers.base`), implement `compile(self) -> Graph` returning the enriched build graph, and override `applies_to(cls, graph_reader) -> bool` to declare the graph-state conditions under which the compiler should run — typically the presence of a framework-specific node type or predicate in the build graph. The default `applies_to` returns `False`, so this override is required. If your compiler must run only after other shaping compilers have finished (for example because it consumes their output), the two-step convention is to gate its `applies_to` on a `<build> dct:creator tcs:<UpstreamCompiler>` triple (see §4.6). For a compiler that must run against the *fully* shaped build (e.g. it needs to see every generated file), set `is_explicit_call = True` on the class body and add a direct invocation from `PipelineGenerator.compile()` — the same pattern `ValidationReportCompiler` and `DockerComposeCompiler` use (§4.4).
- Compilers that produce a file should end their `compile()` method with a call to `attach_file(self.output_reader, filename=..., filepath=..., content=...)` (from `compilers.utils`), re-assigning `self.output_reader` with the returned reader. The helper adds an `spdx:File` node to the `tcs:PipelineBuild` carrying the produced file body as a literal.
- Import the new compiler from `compilers/__init__.py`. The auto-registration mechanism (`Compiler.__init_subclass__`) then makes it visible to `PipelineGenerator`, which will run it automatically against any pipeline whose build graph matches its `applies_to` condition. New compilers usually belong in a per-framework subfolder (`compilers/<framework>/`); framework-agnostic ones go in `compilers/core/`. No edits to `PipelineGenerator` are required.
- Test the new compilers based on a Pipeline Definition that includes components of the new framework.

#### Container / multi-tenancy contract

`PipelineAssembler.describe_docker_container()` mints exactly one
`tcs:DockerContainer` per catalog `tcs:PipelineComponent` that owns a
`tcs:DockerComposeConfig` (a "microservice"), and folds every component
that transitively `dct:requires` it into that same container. There is
no mechanism to run N separate containers of the same catalog component
to isolate concurrent pipelines/segments from each other, and container
assignment never depends on pipeline-definition-specific structure —
only on the catalog's own `dct:requires` graph.

Because of this, a component is either genuinely multi-tenant (the
underlying runtime the container image starts — LDIO's orchestrator,
RDF-Connect's runner — is itself designed to host several independent
pipelines/environments side by side, so multiple
`InstancePipelineComponent`s safely sharing one container is exactly
what the runtime is for), or it must only ever be instanced once.
LDIO and RDF-Connect both qualify as multi-tenant; semantic.works
components do not — each owns exactly one static per-service config
file with zero per-instance aggregation.

**A new framework doesn't need to declare which of the two it is** —
instead, if it does *not* support multi-tenancy, the catalog must carry
a SHACL shape that flags `>1 InstancePipelineComponent` specializing the
same component of that framework as unsupported, so reuse is caught at
validation time instead of silently misbehaving at runtime (the same
approach planned for semantic.works — see
[`docs/pipeline-segments-plan.md`](docs/pipeline-segments-plan.md)). A
multi-tenant framework's compiler instead needs to be able to emit one
config artifact *per segment* sharing one container (LDIO's answer:
Pattern A2 directory-scan, one file per segment).




