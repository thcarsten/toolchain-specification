# Table Of Contents

[1. Introduction](#1-introduction) <br>
[2. Installation](#2-installation) <br>
[3. Workflow](#3-workflow) <br>
[4. Architecture](#4-architecture) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.1. Module overview](#41-module-overview) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.2. The Compiler ABC](#42-the-compiler-abc) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.3. Auto-registration](#43-auto-registration) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.4. PipelineGenerator: the fixpoint loop](#44-pipelinegenerator-the-fixpoint-loop) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.5. File attachment: the spdx:File vocabulary](#45-file-attachment-the-spdxfile-vocabulary) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.6. Provenance: dct:creator attachment](#46-provenance-dctcreator-attachment) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.7. Compiler responsibilities at a glance](#47-compiler-responsibilities-at-a-glance) <br>
[5. Limitations](#5-limitations) <br>
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

Internally, `PipelineGenerator` runs one bootstrap step followed by a fixpoint loop:

1. **Bootstrap** — `PipelineExtractor` is instantiated explicitly because it is the only compiler that takes the `pipeline_id` in its constructor. It extracts the triples for the requested pipeline and seeds the `tcs:PipelineBuild` node (linked to the definition via `prov:hadPlan`). Its `applies_to` returns `True` anyway, so it would be the first eligible compiler in the loop; instantiating it up front is a wiring convenience, not a privileged phase.
2. **Fixpoint loop** — every iteration, `PipelineGenerator` asks every not-yet-run `Compiler` subclass whether its `applies_to` trigger is satisfied by the current build graph, and runs those that are. The loop terminates when a full scan finds nothing eligible. Compilers whose trigger depends on the graph having settled (currently only `DockerComposeCompiler`) can gate on the temporary flag `<build> tcs:isFinishing true`, which the generator sets whenever a shaping pass finds no eligible compiler; see §4.4 for details.

The execution order therefore emerges from the trigger conditions rather than from any class-level rank. After compilation, `gen.compilers` maps each compiler class to the instance that ran, in insertion order — so it doubles as a record of the compile order. Every executed compiler is also attached to the build via `dct:creator`, giving the same information in the graph itself.

Adding a new compiler is a matter of dropping a new file in the appropriate `compilers/<framework>/` subfolder (or `compilers/core/` for framework-agnostic ones) and importing it from `compilers/__init__.py`. `PipelineGenerator` discovers it via `Compiler._registry` and runs it whenever its `applies_to` returns `True`; the generator never needs editing. The notebook `demo.ipynb` demonstrates the end-to-end workflow.

## 4. Architecture

This section is a closer look at the `compilers` package. (`rdfine` has its own [README](src/rdfine/README.md).)

### 4.1. Module overview

| Module | Exported symbols | Purpose |
| --- | --- | --- |
| [base.py](src/compilers/base.py) | `Compiler` | Abstract base class, auto-registry, `applies_to` contract (default `False`), and `compile` contract. |
| [pipeline_generator.py](src/compilers/pipeline_generator.py) | `PipelineGenerator` | End-to-end driver: bootstrap + fixpoint loop. Also manages the `tcs:isFinishing` runtime flag and writes `dct:creator` provenance triples. |
| [project_builder.py](src/compilers/project_builder.py) | `ProjectBuilder` | Write the `spdx:File` nodes of a compiled build graph to disk. Not a `Compiler` subclass — this is the filesystem boundary. |
| [utils.py](src/compilers/utils.py) | (internal) `attach_file`, `extract_config`, `read_literal`, `parse_docker_compose_config` | Compiler-side helpers that encode knowledge of the semantic model — including the `spdx:File` attachment helper called by FILE-producing compilers. |
| [core/pipeline_extractor.py](src/compilers/core/pipeline_extractor.py) | `PipelineExtractor` | Extract the triples concerning one specific pipeline definition out of the catalog and seed the `tcs:PipelineBuild` skeleton (`prov:hadPlan`-linked to the definition). |
| [core/pipeline_assembler.py](src/compilers/core/pipeline_assembler.py) | `PipelineAssembler` | Materialize the `tcs:DockerContainer` / step / config skeleton onto the seeded `tcs:PipelineBuild`. |
| [core/docker_compose_compiler.py](src/compilers/core/docker_compose_compiler.py) | `DockerComposeCompiler` | Produce the top-level `docker-compose.yml` and attach it to the build. Runs during the finishing pass so any shaping compilers can finish editing their configs first. |
| [ldio/config_compiler.py](src/compilers/ldio/config_compiler.py) | `LdioConfigCompiler` | Produce the LDIO `config.yml` and attach it to the build. |
| [rdfc/config_compiler.py](src/compilers/rdfc/config_compiler.py) | `RdfcConfigCompiler` | Produce the RDF-Connect `pipeline.ttl` and attach it to the build. |
| [rdfc/dockerfile_compiler.py](src/compilers/rdfc/dockerfile_compiler.py) | `RdfcDockerFileCompiler` | Produce the RDF-Connect `Dockerfile`, `pyproject.toml` and `package.json` under `rdfc/`. The Dockerfile is verbatim from a `tcs:DockerImageConfig`; the two dependency files are synthesised from `spdx:Package` annotations on the components the pipeline actually uses. |
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
    PipelineExtractor, PipelineAssembler,
    SemanticWorksEnvVarCompiler,
    LdioConfigCompiler, RdfcConfigCompiler, RdfcDockerFileCompiler,
    DockerComposeCompiler,
    VirtuosoCompiler, MuClResourcesCompiler, MuDispatcherCompiler,
    MuDeltaNotifierCompiler, MuAuthorizationCompiler, ErrorAlertCompiler,
)
```

### 4.2. The `Compiler` ABC

Every concrete compiler inherits from `Compiler` and must satisfy a small contract:

- `__init__(graph: Graph)` — takes the build graph it operates on. The base implementation wraps it in a `GraphReader` stored on `self.graph_reader`. Subclasses that need extra arguments (currently only `PipelineExtractor`, which takes a `pipeline_id`) extend the signature and call `super().__init__(graph)`.
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
  A compiler that needs to see which other compilers already ran can inspect the `dct:creator` triples on the build (see §4.6). A compiler that needs to run only after the shaping loop has settled can gate on `<build> tcs:isFinishing true`, described in §4.4.
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

`PipelineGenerator` is the entry point that turns a `(pipeline_id, catalog_graph)` pair into a fully compiled build graph. It runs one bootstrap step and then a single fixpoint loop.

**Bootstrap.** `PipelineExtractor` is instantiated explicitly because it is the only compiler whose constructor needs the `pipeline_id`:

```python
extractor = PipelineExtractor(self.pipeline_id, self.catalog_graph)
self.build = extractor.compile()
```

In addition to extracting the triples for the requested pipeline, `PipelineExtractor` seeds the `tcs:PipelineBuild` node itself:

```turtle
<pipeline>_build a tcs:PipelineBuild ;
                 prov:hadPlan <pipeline> .
```

The empty build node exists from this point on, so downstream compilers and the generator's own provenance / finishing-flag triples always have a target to attach to. `PipelineExtractor.applies_to` returns `True` unconditionally, so it would be the first compiler picked by the loop anyway; the explicit call is a wiring convenience.

**Fixpoint loop.** Every iteration, `PipelineGenerator` scans `Compiler._registry`, filters out compilers that have already run, and evaluates the remaining ones' `applies_to` against the current build graph:

```python
while True:
    eligible = [cls for cls in Compiler._registry
                if cls not in ran and cls.applies_to(GraphReader(self.build))]
    if not eligible:
        ...  # see finishing pass below
    for cls in eligible:
        instance = cls(self.build)
        self.build = instance.compile()
        self._record_creator(cls)   # attach dct:creator + type
        ran.add(cls)
```

Because triggers are evaluated against the growing graph, execution order emerges naturally: a compiler runs as soon as its trigger becomes true. Compilers eligible in the same iteration are treated as commutative and run in registry order within that pass; before the next iteration the loop re-scans, so any new eligibility introduced by their combined effect is picked up on the next round.

Adding a new compiler is therefore a matter of dropping a new file in the appropriate `compilers/<framework>/` subfolder (or `compilers/core/` for framework-agnostic ones) and importing it from `compilers/__init__.py`. `PipelineGenerator` discovers it via the registry and runs it as soon as its `applies_to` returns `True`. No edits to `PipelineGenerator` are needed.

**The `tcs:isFinishing` runtime flag.** Right after the bootstrap, `PipelineGenerator` writes `<build> tcs:isFinishing false` onto the build. This triple exists throughout the compilation run; the generator only flips its value:

- When a scan finds no eligible compiler, the flag is flipped to `true` and the loop does one more scan. This gives finalization-style compilers a chance to trigger on the settled graph. `DockerComposeCompiler`'s `applies_to` uses exactly this hook — it returns `False` unless `<build> tcs:isFinishing true` is present.
- If a compiler runs during that finishing pass, the flag is flipped back to `false` and shaping resumes normally (the graph may have changed in a way that makes previously-ineligible compilers eligible now).
- If the finishing pass itself finds nothing eligible, the loop terminates.
- Just before `compile()` returns, the flag is stripped from the graph entirely, so downstream consumers such as `ProjectBuilder` never see the runtime flag.

Compiler authors who need "run me only after everyone else has had their turn" express that by checking for `<build> tcs:isFinishing true` in their `applies_to`:

```python
@classmethod
def applies_to(cls, graph_reader: GraphReader) -> bool:
    if graph_reader.filter(pred="tcs:isFinishing", obj=True).df.empty:
        return False
    # ... the compiler's actual graph-state trigger ...
```

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

Because the extractor seeds the `tcs:PipelineBuild` node at the very start, the build always exists by the time provenance is written — no buffering or two-step attachment is needed. The compiler IRI defaults to `tcs:<ClassName>` and can be overridden per compiler via the `compiler_iri()` classmethod (e.g. to point at a catalog-backed entry).

A useful side effect: because provenance is attached *while the loop is running*, every subsequent `applies_to` invocation can inspect `dct:creator` on the build to check which compilers have already run — no extra bookkeeping needed.

### 4.7. Compiler responsibilities at a glance

| Compiler | Trigger (`applies_to`) | Reads from the build | Writes to the build |
| --- | --- | --- | --- |
| `PipelineExtractor` | always | the catalog | triples for the requested pipeline definition; seeds `<pipeline>_build a tcs:PipelineBuild ; prov:hadPlan <pipeline>` |
| `PipelineAssembler` | exactly one `tcs:PipelineDefinition` in the graph and it has at least one step (`p-plan:isStepOfPlan`) | the extracted pipeline | `tcs:DockerContainer`, `dct:hasPart`, `tcs:instantiates`, `tcs:runs` |
| `SemanticWorksEnvVarCompiler` | any `tcs:PipelineComponent` in the `sw:` namespace, and at least one `tcs:DockerContainer` exists | step configs + docker configs of `sw:` components | updated `tcs:literal` on each affected `tcs:DockerComposeConfig` |
| `VirtuosoCompiler` | a container instantiates `sw:triple-store` | the `:VirtuosoIniDefault` config body | `spdx:File` named `semantic-works/config/virtuoso/virtuoso.ini` |
| `MuClResourcesCompiler` | a container instantiates `sw:mu-cl-resources` | the three `mu-cl-resources` default config bodies | `spdx:File`s named `semantic-works/config/resources/{domain.json,domain.lisp,repository.lisp}` |
| `MuDispatcherCompiler` | a container instantiates `sw:mu-dispatcher` | the `:MuDispatcherExDefault` config body | `spdx:File` named `semantic-works/config/dispatcher/dispatcher.ex` |
| `MuDeltaNotifierCompiler` | a container instantiates `sw:mu-delta-notifier` | the `:MuDeltaNotifierRulesJsDefault` config body | `spdx:File` named `semantic-works/config/delta/rules.js` |
| `MuAuthorizationCompiler` | a container instantiates `sw:mu-authorization` | the `:MuAuthorizationConfigLispDefault` config body | `spdx:File` named `semantic-works/config/authorization/config.lisp` |
| `ErrorAlertCompiler` | a container instantiates `sw:loket-error-alert-service` | the two error-alert default config bodies | `spdx:File`s named `semantic-works/config/error-alert/{config.json,error.hbs}` |
| `LdioConfigCompiler` | a container instantiates `ldio:LinkedDataInteractionsOrchestrator` | LDIO components and their configs | `spdx:File` named `ldio/config.yml` |
| `RdfcConfigCompiler` | a container instantiates `rdfc:Orchestrator` | RDF-Connect components, runners and step graph | `spdx:File` named `rdfc/pipeline.ttl` |
| `RdfcDockerFileCompiler` | a container instantiates `rdfc:Orchestrator` and a `tcs:DockerImageConfig` is present | the Dockerfile literal + every `spdx:Package` reachable via `dct:requires` from components in the container | `spdx:File`s named `rdfc/Dockerfile`, `rdfc/pyproject.toml`, `rdfc/package.json` |
| `DockerComposeCompiler` | `<build> tcs:isFinishing true` is set (i.e. the loop has settled) and any `tcs:DockerComposeConfig` node exists | every `tcs:DockerComposeConfig` | `spdx:File` named `./docker-compose.yml` |

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

`tcs:Channel` is a first-class connector inside a framework (LDIO steps, RDF-Connect processors), but does not yet describe transports that cross framework boundaries. The LDIO → RDF-Connect HTTP hop and the RDF-Connect → semantic.works SPARQL Update push are modelled as opaque endpoints, with each side independently configured. The `oslc:Error` rule in §5.1 is the concrete downstream consequence.

### 5.3. Runtime `depends_on` in the emitted `docker-compose.yml`

`dct:requires` links between components already describe the runtime dependency chain (LDIO orchestrator → RDFC ingest, error-alert → delta-notifier → triple-store, …), but `DockerComposeCompiler` drops that information — the emitted compose file boots services in arbitrary order. Downstream this manifests as first-boot races that a `docker compose restart` typically clears.

### 5.4. LDIO service definition in the catalog

The LDIO workbench service is currently declared with an outdated image tag (`ldes/ldi-orchestrator:2.8.0-SNAPSHOT`) and no volume mounts. Any generated pipeline needs the operator to hand-patch the emitted compose file to use `2.13.0` + the two bind mounts the actual workbench expects — or to refactor to LDIO's Pattern A1 single-file startup model.

### 5.5. Test-suite runner

Static SHACL validation of a pipeline definition against the tcs application profile can be run today via `GraphReader.validate()` on the merged catalog + pipeline graph — the demo notebook does exactly this. What is not yet built is the pre-generator runner design captured in [`test suite/README.md`](../../test%20suite/README.md), including the native Python shape-matching library (in development by a colleague) for input/output shape validation.

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
- [ ] Cross-framework `tcs:Channel`: extend the channel model to describe transports that span framework boundaries (LDIO → RDFC HTTP hop, RDFC → sw SPARQL push, sw delta-notifier subscriptions). Prerequisite for synthesising the `oslc:Error` rule in `delta/rules.js` from the pipeline definition rather than shipping it as boilerplate. See [`docs/pipeline-segments-plan.md`](docs/pipeline-segments-plan.md)'s "Framework / compiler contract" and "Compiling cross-container bridges" sections for the concrete design (container/multi-tenancy contract every framework's compiler must satisfy, and a `BridgeTransportCompiler` proposal for the LDIO → RDFC hop specifically).
- [x] RdfcDockerFileCompiler: Creates an adhoc Dockerfile for RDF-Connect. This allows to include only those dependencies in a docker container which are actually used in the pipeline.
- [ ] NifiCompiler: Compiler for [Nifi 2](https://nifi.apache.org/). 
- [x] PipelineGenerator: Uses all previously described compilers to route the compilation flow for generating a pipeline project folder based on a Pipeline Definition. Routing is done via the registry + `applies_to` mechanism described above; producing the project folder on disk is handled by `ProjectBuilder`.


## 7. How To

### 6.1. How to write your own Pipeline Definition
You can find a couple of examples in the catalog.ttl file in the data folder. A Pipeline Definition is as simple as a bunch of InstancePipelineComponents, linked to each other. Each InstancePipelineComponent receives a Config and is carried out by a component of the catalog. So you do not need a lot to write a Pipeline Definition. This part will be made easier in the future by providing a frontend.


### 6.2. How to onboard your own components to the catalog
Check the catalog in the data folder for examples. At a minimum, a Pipeline Component needs either a DockerComposeConfig or a dependency to a component with a DockerComposeConfig. The assumption is that each DockerComposeConfig resolves all dependencies of components attached to it, including itself. Depending on the framework, you may also need to include framework-specific properties. For example, the LdioConfigCompiler needs to be able to look up ldio:type and rdf:label, whereas the RdfcConfigCompiler needs to be able to look up owl:imports. This is explicitly mentioned in the sh:NodeShapes a tcs:Compiler points to. Everything else is optional, but it is a good idea to include as many NodeShapes as possible: It serves as a lookup reference for the constraints that need to be fulfilled once a component is included in a pipeline. A typical NodeShape for a tcs:PipelineComponent is for example the schema of its expected Config.


### 6.3. How to onboard new frameworks
- Describe pipeline components with the semantic model and add them to the catalog.
- Write compilers for your new framework that can produce the expected output files. Each compiler must subclass `Compiler` (from `compilers.base`), implement `compile(self) -> Graph` returning the enriched build graph, and override `applies_to(cls, graph_reader) -> bool` to declare the graph-state conditions under which the compiler should run — typically the presence of a framework-specific node type or predicate in the build graph. The default `applies_to` returns `False`, so this override is required. If your compiler must run only after other shaping compilers have finished (for example because it consumes their output), gate its `applies_to` on `<build> tcs:isFinishing true`, which `PipelineGenerator` sets whenever a shaping pass has settled (see §4.4).
- Compilers that produce a file should end their `compile()` method with a call to `attach_file(self.output_reader, filename=..., filepath=..., content=...)` (from `compilers.utils`), re-assigning `self.output_reader` with the returned reader. The helper adds an `spdx:File` node to the `tcs:PipelineBuild` carrying the produced file body as a literal.
- Import the new compiler from `compilers/__init__.py`. The auto-registration mechanism (`Compiler.__init_subclass__`) then makes it visible to `PipelineGenerator`, which will run it automatically against any pipeline whose build graph matches its `applies_to` condition. New compilers usually belong in a per-framework subfolder (`compilers/<framework>/`); framework-agnostic ones go in `compilers/core/`. No edits to `PipelineGenerator` are required.
- Test the new compilers based on a Pipeline Definition that includes components of the new framework.


