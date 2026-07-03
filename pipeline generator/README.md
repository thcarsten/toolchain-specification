# Table Of Contents

[1. Introduction](#1-introduction) <br>
[2. Installation](#2-installation) <br>
[3. Workflow](#3-workflow) <br>
[4. Architecture](#4-architecture) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.1. Module overview](#41-module-overview) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.2. The Compiler ABC](#42-the-compiler-abc) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.3. Auto-registration](#43-auto-registration) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.4. PipelineGenerator: the fixpoint loop](#44-pipelinegenerator-the-fixpoint-loop) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.5. File attachment: the tcs:File vocabulary](#45-file-attachment-the-tcsfile-vocabulary) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.6. Provenance: dct:creator attachment](#46-provenance-dctcreator-attachment) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.7. Compiler responsibilities at a glance](#47-compiler-responsibilities-at-a-glance) <br>
[5. Future directions](#5-future-directions) <br>
[6. How To](#6-how-to) <br>

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

The returned `build_graph` contains both the semantic description of the pipeline build and the compiled files as `tcs:File` nodes attached to the `tcs:PipelineBuild` via `tcs:compiledFile` (with `tcs:filename`, `tcs:filepath`, and `tcs:literal` carrying the file body). The build graph is therefore self-describing. To materialize it to disk, hand it to `ProjectBuilder`:

```python
from compilers import ProjectBuilder

ProjectBuilder(build_graph).write("./out/demonstrator")
```

Internally, `PipelineGenerator` runs one bootstrap step followed by a fixpoint loop:

1. **Bootstrap** — `PipelineExtractor` is instantiated explicitly because it is the only compiler that takes the `pipeline_id` in its constructor. It extracts the triples for the requested pipeline and seeds the `tcs:PipelineBuild` node (linked to the definition via `prov:hadPlan`). Its `applies_to` returns `True` anyway, so it would be the first eligible compiler in the loop; instantiating it up front is a wiring convenience, not a privileged phase.
2. **Fixpoint loop** — every iteration, `PipelineGenerator` asks every not-yet-run `Compiler` subclass whether its `applies_to` trigger is satisfied by the current build graph, and runs those that are. The loop terminates when a full scan finds nothing eligible. Compilers whose trigger depends on the graph having settled (currently only `DockerComposeCompiler`) can gate on the temporary flag `<build> tcs:isFinishing true`, which the generator sets whenever a shaping pass finds no eligible compiler; see §4.4 for details.

The execution order therefore emerges from the trigger conditions rather than from any class-level rank. After compilation, `gen.compilers` maps each compiler class to the instance that ran, in insertion order — so it doubles as a record of the compile order. Every executed compiler is also attached to the build via `dct:creator`, giving the same information in the graph itself.

Adding a new compiler is a matter of dropping a new file in `compilers/` and importing it from `compilers/__init__.py`. `PipelineGenerator` discovers it via `Compiler._registry` and runs it whenever its `applies_to` returns `True`; the generator never needs editing. The notebook `demo.ipynb` demonstrates the end-to-end workflow.

## 4. Architecture

This section is a closer look at the `compilers` package. (`rdfine` has its own [README](src/rdfine/README.md).)

### 4.1. Module overview

| Module | Exported symbols | Purpose |
| --- | --- | --- |
| [base.py](src/compilers/base.py) | `Compiler` | Abstract base class, auto-registry, `applies_to` contract (default `False`), `compile` contract, and `_attach_file` helper. |
| [pipeline_extractor.py](src/compilers/pipeline_extractor.py) | `PipelineExtractor` | Extract the triples concerning one specific pipeline definition out of the catalog and seed the `tcs:PipelineBuild` skeleton (`prov:hadPlan`-linked to the definition). |
| [pipeline_assembler.py](src/compilers/pipeline_assembler.py) | `PipelineAssembler` | Materialize the `tcs:DockerContainer` / step / config skeleton onto the seeded `tcs:PipelineBuild`. |
| [semantic_works_compiler.py](src/compilers/semantic_works_compiler.py) | `SemanticWorksCompiler` | For semantic.works components: fold step configurations into the Docker Compose env vars of the responsible microservice. |
| [ldio_config_compiler.py](src/compilers/ldio_config_compiler.py) | `LdioConfigCompiler` | Produce the LDIO `config.yml` and attach it to the build. |
| [rdfc_config_compiler.py](src/compilers/rdfc_config_compiler.py) | `RdfcConfigCompiler` | Produce the RDF-Connect `pipeline.ttl` and attach it to the build. |
| [docker_compose_compiler.py](src/compilers/docker_compose_compiler.py) | `DockerComposeCompiler` | Produce the top-level `docker-compose.yml` and attach it to the build. Runs during the finishing pass so any shaping compilers can finish editing their configs first. |
| [pipeline_generator.py](src/compilers/pipeline_generator.py) | `PipelineGenerator` | End-to-end driver: bootstrap + fixpoint loop. Also manages the `tcs:isFinishing` runtime flag and writes `dct:creator` provenance triples. |
| [project_builder.py](src/compilers/project_builder.py) | `ProjectBuilder` | Write the `tcs:File` nodes of a compiled build graph to disk. Not a `Compiler` subclass — this is the filesystem boundary. |
| [utils.py](src/compilers/utils.py) | (internal) `receive_first` | Defensive list-head extraction with informative `LookupError`s. |

All public symbols are re-exported from the package root:

```python
from compilers import (
    Compiler, PipelineGenerator, ProjectBuilder,
    PipelineExtractor, PipelineAssembler,
    SemanticWorksCompiler,
    LdioConfigCompiler, RdfcConfigCompiler, DockerComposeCompiler,
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

Adding a new compiler is therefore a matter of dropping a new file in `compilers/` and importing it from `compilers/__init__.py`. `PipelineGenerator` discovers it via the registry and runs it as soon as its `applies_to` returns `True`. No edits to `PipelineGenerator` are needed.

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

### 4.5. File attachment: the tcs:File vocabulary

Compilers that produce a file attach their output to the build by calling `self._attach_file(filename=..., filepath=..., content=...)` from inside `compile()`. The helper adds five triples to the build graph:

```turtle
:build tcs:compiledFile :file_<slug> .

:file_<slug> a tcs:File ;
    tcs:filename "..." ;
    tcs:filepath "..." ;
    tcs:literal  "..." .
```

The slug is derived from `filepath_filename` via `re.sub(r"[^a-zA-Z0-9]+", "_", ...).strip("_")`, so file IRIs are stable within a build and unique for distinct `(filepath, filename)` pairs.

The body of the file is stored verbatim as an rdflib `Literal` — no prefix expansion is applied to it. This is what makes it safe to put arbitrary text bodies (YAML, Turtle, JSON) into `tcs:literal` even when they contain colons or other CURIE-like substrings.

After `PipelineGenerator.compile()` returns, the build graph is fully self-describing: `ProjectBuilder` iterates over the `tcs:File` nodes and writes each to disk at `tcs:filepath / tcs:filename` with body `tcs:literal`. It collects the file records into a `pandas.DataFrame` on `builder.files` first, so the planned writes can be inspected before touching the filesystem. A path-traversal guard rejects any `tcs:filepath` that would escape the target directory.

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
| `PipelineAssembler` | exactly one `tcs:PipelineDefinition` in the graph and it has at least one `:hasStep` | the extracted pipeline | `tcs:DockerContainer`, `dct:hasPart`, `tcs:instantiates`, `tcs:runs`, `:isAssigned` |
| `SemanticWorksCompiler` | any `tcs:PipelineComponent` in the `sw:` namespace, and at least one `tcs:DockerContainer` exists | step configs + docker configs of `sw:` components | updated `tcs:literal` on each affected `tcs:DockerComposeConfig` |
| `LdioConfigCompiler` | a container instantiates `ldio:LinkedDataInteractionsOrchestrator` | LDIO components and their configs | `tcs:File` named `ldio/config.yml` |
| `RdfcConfigCompiler` | a container instantiates `rdfc:Orchestrator` | RDF-Connect components, runners and step graph | `tcs:File` named `rdfc/pipeline.ttl` |
| `DockerComposeCompiler` | `<build> tcs:isFinishing true` is set (i.e. the loop has settled) and any `tcs:DockerComposeConfig` node exists | every `tcs:DockerComposeConfig` | `tcs:File` named `./docker-compose.yml` |

## 5. Future directions
The pipeline generator is not fully implemented yet, it is a work in progress. We have the following goals for the year 2026:

- [x] Add support for Pipeline Definitions spanning components of both the RDF-Connect and LDIO framework. This warrants automatic generation of interoperable pipelines.
- [x] Add another framework, [semantic.works](https://semantic.works/).
- [x] PipelineAssembler: Assigns segments of a Pipeline Definition to microservices. It does so by following dependency paths via dct:requires and assigning InstancePipelineComponents to the microservices that instantiate them.
- [x] DockerComposeCompiler: Compiles a DockerCompose file based on the description of the different microservices.
- [x] ProjectBuilder: Takes the semantic description of the PipelineBuild and writes the attached `tcs:File` nodes to a folder using their `tcs:filepath` / `tcs:filename`. Lives outside the `Compiler` hierarchy because it is the filesystem boundary, not a graph-to-graph transformation.
- [x] CompilerAssigner: It may be necessary at some point to provide a lookup which compilers need to be called depending on information contained in the graph. So that compilers can be called dynamically based on need. Implemented as a registry on `Compiler._registry` (auto-populated via `__init_subclass__`) combined with a per-compiler `applies_to(graph_reader) -> bool` classmethod that declares the triggering pattern.
- [ ] SemanticModelVersionMapper: Can map from one version of the semantic model to the internal model that is used by the pipeline generator. Allows decoupling versioning of the official semantic model and the internal model used for implementation.
- [ ] RdfcDockerFileCompiler: Creates an adhoc Dockerfile for RDF-Connect. This allows to include only those dependencies in a docker container which are actually used in the pipeline (currently I use a generic RDF-Connect Docker container that includes most RDF-Connect components).
- [ ] NifiCompiler: Compiler for [Nifi 2](https://nifi.apache.org/). 
- [x] PipelineGenerator: Uses all previously described compilers to route the compilation flow for generating a pipeline project folder based on a Pipeline Definition. Routing is done via the registry + `applies_to` mechanism described above; producing the project folder on disk is handled by `ProjectBuilder`.


## 6. How To

### 6.1. How to write your own Pipeline Definition
You can find a couple of examples in the catalog.ttl file in the data folder. A Pipeline Definition is as simple as a bunch of InstancePipelineComponents, linked to each other. Each InstancePipelineComponent receives a Config and is carried out by a component of the catalog. So you do not need a lot to write a Pipeline Definition. This part will be made easier in the future by providing a frontend.


### 6.2. How to onboard your own components to the catalog
Check the catalog in the data folder for examples. At a minimum, a Pipeline Component needs either a DockerComposeConfig or a dependency to a component with a DockerComposeConfig. The assumption is that each DockerComposeConfig resolves all dependencies of components attached to it, including itself. Depending on the framework, you may also need to include framework-specific properties. For example, the LdioConfigCompiler needs to be able to look up ldio:type and rdf:label, whereas the RdfcConfigCompiler needs to be able to look up owl:imports. This is explicitly mentioned in the sh:NodeShapes a tcs:Compiler points to. Everything else is optional, but it is a good idea to include as many NodeShapes as possible: It serves as a lookup reference for the constraints that need to be fulfilled once a component is included in a pipeline. A typical NodeShape for a tcs:PipelineComponent is for example the schema of its expected Config.


### 6.3. How to onboard new frameworks
- Describe pipeline components with the semantic model and add them to the catalog.
- Write compilers for your new framework that can produce the expected output files. Each compiler must subclass `Compiler` (from `compilers.base`), implement `compile(self) -> Graph` returning the enriched build graph, and override `applies_to(cls, graph_reader) -> bool` to declare the graph-state conditions under which the compiler should run — typically the presence of a framework-specific node type or predicate in the build graph. The default `applies_to` returns `False`, so this override is required. If your compiler must run only after other shaping compilers have finished (for example because it consumes their output), gate its `applies_to` on `<build> tcs:isFinishing true`, which `PipelineGenerator` sets whenever a shaping pass has settled (see §4.4).
- Compilers that produce a file should end their `compile()` method with a call to `self._attach_file(filename=..., filepath=..., content=...)`, which adds a `tcs:File` node to the `tcs:PipelineBuild` carrying the produced file body as a literal.
- Import the new compiler from `compilers/__init__.py`. The auto-registration mechanism (`Compiler.__init_subclass__`) then makes it visible to `PipelineGenerator`, which will run it automatically against any pipeline whose build graph matches its `applies_to` condition. No edits to `PipelineGenerator` are required.
- Test the new compilers based on a Pipeline Definition that includes components of the new framework.


