# Table Of Contents

[1. Introduction](#1-introduction) <br>
[2. Installation](#2-installation) <br>
[3. Workflow](#3-workflow) <br>
[4. Architecture](#4-architecture) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.1. Module overview](#41-module-overview) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.2. The Compiler ABC](#42-the-compiler-abc) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.3. Auto-registration](#43-auto-registration) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.4. PipelineGenerator: bootstrap + dispatch](#44-pipelinegenerator-bootstrap--dispatch) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.5. File attachment: the tcs:File vocabulary](#45-file-attachment-the-tcsfile-vocabulary) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[4.6. Compiler responsibilities at a glance](#46-compiler-responsibilities-at-a-glance) <br>
[5. Future directions](#5-future-directions) <br>
[6. How To](#6-how-to) <br>

## 1. Introduction
In this repo you find the codebase for the tool “pipeline generator”. As the name suggests, the pipeline generator automatically generates pipelines based on a semantic description of a pipeline. The pipeline generator accepts pipeline definitions which are written in RDF and follow the [semantic model](https://github.com/thcarsten/toolchain-specification/tree/main/semantic%20model) of the toolchain specification. Based on the pipeline definition, it looks up components and their dependencies in a component catalogue, and builds docker containers to resolve these dependencies. It also generates the configuration files necessary to run the pipelines. In the [data-folder](https://github.com/thcarsten/toolchain-specification/tree/main/pipeline%20generator/data), you can find the pipeline definitions and the catalogue used for [the demo](https://github.com/thcarsten/toolchain-specification/blob/main/pipeline%20generator/src/demo.ipynb). Currently three frameworks are supported, RDF Connect, LDIO and semantic.works.

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

Internally, `PipelineGenerator` runs in two phases:

1. **Bootstrap** — runs unconditionally and in a fixed order:
   - `PipelineExtractor` extracts the data concerning the requested pipeline definition.
   - `PipelineAssembler` assigns Docker Containers, Steps and Configs to materialize the `tcs:PipelineBuild` skeleton on which every other compiler depends.
2. **Dispatch** — every other concrete `Compiler` in the registry is consulted via its `applies_to` classmethod and run in `Tier` order against the growing build graph. Currently implemented:
   - `SemanticWorksCompiler` (BUILD tier) integrates step configurations into the env vars of semantic.works microservices.
   - `LdioConfigCompiler` (FILE tier) generates the LDIO config file.
   - `RdfcConfigCompiler` (FILE tier) generates the RDF-Connect pipeline definition file.
   - `DockerComposeCompiler` (FILE tier) generates the docker-compose file.

Compilers register themselves on import via `Compiler.__init_subclass__`, so the generator never needs editing when new compilers are added. The compiler instances that ran are kept on `gen.compilers`, keyed by class, so their intermediate state can be inspected after compilation — useful for debugging. The notebook `demo.ipynb` demonstrates the end-to-end workflow.

## 4. Architecture

This section is a closer look at the `compilers` package. (`rdfine` has its own [README](src/rdfine/README.md).)

### 4.1. Module overview

| Module | Exported symbols | Tier | Purpose |
| --- | --- | --- | --- |
| [base.py](src/compilers/base.py) | `Compiler`, `Tier` | — | Abstract base class, tier enum, auto-registry, `applies_to` contract, and `_attach_file` helper. |
| [pipeline_extractor.py](src/compilers/pipeline_extractor.py) | `PipelineExtractor` | BOOTSTRAP | Extract the triples concerning one specific pipeline definition out of the catalog. Bootstrap-only. |
| [pipeline_assembler.py](src/compilers/pipeline_assembler.py) | `PipelineAssembler` | BOOTSTRAP | Materialize the `tcs:PipelineBuild` node plus its `tcs:DockerContainer` / step / config skeleton. Bootstrap-only. |
| [semantic_works_compiler.py](src/compilers/semantic_works_compiler.py) | `SemanticWorksCompiler` | BUILD | For semantic.works components: fold step configurations into the Docker Compose env vars of the responsible microservice. |
| [ldio_config_compiler.py](src/compilers/ldio_config_compiler.py) | `LdioConfigCompiler` | FILE | Produce the LDIO `config.yml` and attach it to the build. |
| [rdfc_config_compiler.py](src/compilers/rdfc_config_compiler.py) | `RdfcConfigCompiler` | FILE | Produce the RDF-Connect `pipeline.ttl` and attach it to the build. |
| [docker_compose_compiler.py](src/compilers/docker_compose_compiler.py) | `DockerComposeCompiler` | FILE | Produce the top-level `docker-compose.yml` and attach it to the build. |
| [pipeline_generator.py](src/compilers/pipeline_generator.py) | `PipelineGenerator` | — | End-to-end driver: bootstrap + registry dispatch. |
| [project_builder.py](src/compilers/project_builder.py) | `ProjectBuilder` | — | Write the `tcs:File` nodes of a compiled build graph to disk. Not a `Compiler` subclass — this is the filesystem boundary. |
| [utils.py](src/compilers/utils.py) | (internal) `receive_first` | — | Defensive list-head extraction with informative `LookupError`s. |

All public symbols are re-exported from the package root:

```python
from compilers import (
    Compiler, Tier, PipelineGenerator, ProjectBuilder,
    PipelineExtractor, PipelineAssembler,
    SemanticWorksCompiler,
    LdioConfigCompiler, RdfcConfigCompiler, DockerComposeCompiler,
)
```

### 4.2. The `Compiler` ABC

Every concrete compiler inherits from `Compiler` and must satisfy a small contract:

- `__init__(graph: Graph)` — takes the build graph it operates on. The base implementation wraps it in a `GraphReader` stored on `self.graph_reader`. Subclasses that need extra arguments (currently only `PipelineExtractor`, which takes a `pipeline_id`) extend the signature and call `super().__init__(graph)`.
- `tier: ClassVar[Tier]` — declares the compiler’s role in the dispatch order: `Tier.BOOTSTRAP` (seed the build graph, unconditional — currently reserved for `PipelineExtractor` and `PipelineAssembler`), `Tier.BUILD` (shape the build), or `Tier.FILE` (attach a file derived from the build). Tiers run in ascending order (BOOTSTRAP before BUILD before FILE); within a tier ordering is undefined, so compilers in the same tier must be commutative.
- `compile(self) -> Graph` — runs the compilation and returns the (now enriched) build graph. Heavy lifting belongs here, not in `__init__` — this way the work happens predictably at one moment in time and stays composable in the registry dispatch.
- `applies_to(cls, graph_reader: GraphReader) -> bool` (classmethod) — declares the condition under which the compiler should run. The default returns `True` (always-applicable). FILE-tier compilers typically override it with a single filter check, e.g.:
  ```python
  @classmethod
  def applies_to(cls, graph_reader: GraphReader) -> bool:
      return not graph_reader.filter(
          pred="tcs:instantiates",
          obj="ldio:LinkedDataInteractionsOrchestrator",
      ).df.empty
  ```

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

### 4.4. PipelineGenerator: bootstrap + dispatch

`PipelineGenerator` is the entry point that turns a `(pipeline_id, catalog_graph)` pair into a fully compiled build graph. It runs in two phases.

**Phase 1 — bootstrap.** Two compilers are run unconditionally and in a fixed order, because every other compiler depends on the `tcs:PipelineBuild` skeleton they produce:

```python
extractor = PipelineExtractor(self.pipeline_id, self.catalog_graph)
self.build = extractor.compile()
assembler = PipelineAssembler(self.build)
self.build = assembler.compile()
```

These two are excluded from the registry dispatch even though they live in the registry (their special role is documented inline; the registry stays honest about all concrete compilers).

**Phase 2 — dispatch.** Every remaining compiler is consulted via `applies_to`, sorted by `tier`, and run if applicable:

```python
for cls in sorted(candidates, key=lambda c: c.tier):
    if cls.applies_to(GraphReader(self.build)):
        instance = cls(self.build)
        self.build = instance.compile()
        self.compilers[cls] = instance
```

Adding a new compiler is therefore a matter of dropping a new file in `compilers/` and importing it from `compilers/__init__.py`. `PipelineGenerator` discovers it via the registry and runs it whenever `applies_to` returns `True`. No edits to `PipelineGenerator` are needed.

The instances that ran are kept on `gen.compilers`, keyed by class, for post-mortem inspection of their intermediate state.

### 4.5. File attachment: the tcs:File vocabulary

FILE-tier compilers attach their output to the build by calling `self._attach_file(filename=..., filepath=..., content=...)` from inside `compile()`. The helper adds five triples to the build graph:

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

### 4.6. Compiler responsibilities at a glance

| Compiler | Tier | Trigger (`applies_to`) | Reads from the build | Writes to the build |
| --- | --- | --- | --- | --- |
| `PipelineExtractor` | BOOTSTRAP | always (bootstrap) | the catalog | triples concerning the requested pipeline definition |
| `PipelineAssembler` | BOOTSTRAP | always (bootstrap) | the extracted pipeline | `tcs:PipelineBuild`, `tcs:DockerContainer`, `tcs:runs`, `:isAssigned` |
| `SemanticWorksCompiler` | BUILD | any `tcs:PipelineComponent` with a `sw:` URI | step configs + docker configs of `sw:` components | updated `tcs:literal` on each affected `tcs:DockerComposeConfig` |
| `LdioConfigCompiler` | FILE | a container instantiating `ldio:LinkedDataInteractionsOrchestrator` | LDIO components and their configs | `tcs:File` named `ldio/config.yml` |
| `RdfcConfigCompiler` | FILE | a container instantiating `rdfc:Orchestrator` | RDF-Connect components, runners and step graph | `tcs:File` named `rdfc/pipeline.ttl` |
| `DockerComposeCompiler` | FILE | any `tcs:DockerComposeConfig` node | every `tcs:DockerComposeConfig` | `tcs:File` named `./docker-compose.yml` |

## 5. Future directions
The pipeline generator is not fully implemented yet, it is a work in progress. We have the following goals for the year 2026:

- [x] Add support for Pipeline Definitions spanning components of both the RDF Connect and LDIO framework. This warrants automatic generation of interoperable pipelines.
- [x] Add another framework, [semantic.works](https://semantic.works/).
- [x] PipelineAssembler: Looks up whether a Pipeline Definition contains segments to be covered by different microservices. If so, generates a DataTree describing the microservices and the pipeline segments they ought to cover. Also includes a new Pipeline Definition per microservice (to be fed to downstream compilers).
- [x] DockerComposeCompiler: Compiles a DockerCompose file based on the description of the different microservices.
- [x] ProjectBuilder: Takes the semantic description of the PipelineBuild and writes the attached `tcs:File` nodes to a folder using their `tcs:filepath` / `tcs:filename`. Lives outside the `Compiler` hierarchy because it is the filesystem boundary, not a graph-to-graph transformation.
- [x] CompilerAssigner: It may be necessary at some point to provide a lookup which compilers need to be called depending on information contained in the graph. So that compilers can be called dynamically based on need. Implemented as a registry on `Compiler._registry` (auto-populated via `__init_subclass__`) combined with a per-compiler `applies_to(graph_reader) -> bool` classmethod that declares the triggering pattern.
- [ ] SemanticModelVersionMapper: Can map from one version of the semantic model to the internal model that is used by the pipeline generator. Allows decoupling versioning of the official semantic model and the internal model used for implementation.
- [ ] RdfcDockerFileCompiler: Creates an adhoc Dockerfile for RDF-Connect. This allows to include only those dependencies in a docker container which are actually used in the pipeline (currently I use a generic RDF-Connect Docker container that includes most RDF Connect components).
- [x] PipelineGenerator: Uses all previously described compilers to route the compilation flow for generating a pipeline project folder based on a Pipeline Definition. Routing is done via the registry + `applies_to` mechanism described above; producing the project folder on disk is handled by `ProjectBuilder`.


## 6. How To

### 6.1. How to write your own Pipeline Definition
You can find a couple of examples in the pipeline.ttl- file in the data folder. A Pipeline Definition is as simple as a bunch of InstancePipelineComponents, linked to each other. Each InstancePipelineComponent receives a Config and is carried out by a component of the Catalog. So you do not need a lot to write a Pipeline Definition. This part will be made easier in the future by providing a frontend.


### 6.2. How to onboard your own components to the catalogue
Check the Catalog in the data folder for examples. At a minimum, a Pipeline Component needs either a DockerComposeConfig or a dependency to a component with a DockerComposeConfig. The assumption is that each DockerComposeConfig resolves all dependencies of components attached to it, including itself. Depending on the framework, you may also need to include framework-specific properties. For example, the LdioConfigCompiler needs to be able to look up ldio:type and rdf:label, whereas the RdfcConfigCompiler needs to be able to look up owl:imports. Everything else is optional, but it is a good idea to include as many NodeShapes as possible: It serves as a lookup reference for the constraints that need to be fullfilled once a component is included in a pipeline.


### 6.3. How to onboard new frameworks
- Describe pipeline components with the semantic model and add them to the catalog.
- Write compilers for your new framework that can produce the expected output files. Each compiler must subclass `Compiler` (from `compilers.base`), set its `tier` class attribute (typically `Tier.FILE` for compilers that produce a file), implement `compile(self) -> Graph` returning the enriched build graph, and override `applies_to(cls, graph_reader) -> bool` to declare the conditions under which the compiler should run — typically the presence of a framework-specific node type or predicate in the build graph.
- FILE-tier compilers should end their `compile()` method with a call to `self._attach_file(filename=..., filepath=..., content=...)`, which adds a `tcs:File` node to the `tcs:PipelineBuild` carrying the produced file body as a literal.
- Import the new compiler from `compilers/__init__.py`. The auto-registration mechanism (`Compiler.__init_subclass__`) then makes it visible to `PipelineGenerator`, which will run it automatically against any pipeline whose build graph matches its `applies_to` condition. No edits to `PipelineGenerator` are required.
- Test the new compilers based on a new Pipeline Definition written to exclusively include components of the new framework.


