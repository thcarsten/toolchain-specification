# Table Of Contents

[1. Introduction](#1-introduction) <br>
[2. Installation](#2-installation) <br>
[3. Workflow](#3-workflow) <br>
&nbsp;&nbsp;&nbsp;&nbsp;[3.1. Generating the RDF-Connect catalog](#31-generating-the-rdf-connect-catalog) <br>
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

The codebase is found in the [src-folder](https://github.com/thcarsten/toolchain-specification/tree/main/pipeline%20generator/src). It consists of three packages: `rdfine` provides ergonomic graph IO and transformation primitives (see its own [README](src/rdfine/README.md)); `compilers` is the pipeline generator itself, built around a small `Compiler` ABC and a self-registering dispatch system orchestrated by `PipelineGenerator`; `catalog` is a pre-compile step that generates the RDF-Connect section of the component catalog from the packages' own published definitions (see [§3.1](#31-generating-the-rdf-connect-catalog)). Section 4 describes the architecture in detail.

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

To run the test suite (`pytest` comes from `rdfine`'s `dev` extra):

```
pip install "./src/rdfine[dev]"
PYTHONPATH=src pytest tests/ -q
```

The tests cover the catalog generator (see [§3.1](#31-generating-the-rdf-connect-catalog)) and an end-to-end check that the merged catalog validates and the demonstrator pipeline still compiles. They need no network — catalog generation runs off the committed snapshot in `data/harvest/`.

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

### 3.1. Generating the RDF-Connect catalog

`data/catalog-rdfc.ttl` is **generated, not hand-written**. An RDF-Connect processor already publishes a `processors.ttl` describing itself, so almost everything its catalog entry needs is a restatement of facts the package ships. Transcribing those by hand is what let the catalog drift out of sync with upstream.

The hand-written input is one block per component in [`data/catalog-rdfc-requests.ttl`](data/catalog-rdfc-requests.ttl):

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
python -m catalog harvest     # network: resolves versions, refreshes data/harvest/
python -m catalog generate    # offline + deterministic: rewrites data/catalog-rdfc.ttl
```

`harvest` resolves each request's version range against the registry (real caret/tilde/prerelease matching, not `dist-tags.latest`), downloads the package, and freezes the Turtle file that declares the component into `data/harvest/` next to a JSON record of the registry facts. Both are committed. `generate` reads only that snapshot, so regeneration needs no network, produces byte-identical output, and a diff shows whether a change came from upstream (the snapshot moved) or from policy (the request file moved) — the same discipline as committing a lockfile. `python -m catalog generate --check` exits non-zero if the committed file is stale.

**Translating the shape.** Upstream shapes cannot be pasted verbatim; four systematic rewrites are applied, all traceable to one root cause — a pipeline definition supplies parameter values as bare IRIs and untyped blank nodes inside a `tcs:embedded` block, so any constraint requiring an `rdf:type` on the value is unsatisfiable as written:

1. `sh:class rdfc:Reader` / `rdfc:Writer` → `sh:class tcs:Channel` (the type `inference_rules.yaml` derives from `tcs:readsFrom` / `tcs:writesTo`). Direction preserved on a non-constraining `tcs:upstreamClass`.
2. `sh:class` at a nested config class → `sh:node` against a named shape, which is then targeted by `sh:targetObjectsOf` on the property path instead of by class.
3. `sh:class` at a class from a foreign vocabulary. Two outcomes, depending on whether the toolchain models that class's *value space*:
   - Listed in `EXTERNAL_SHAPES` (`catalog/shapes.py`) → `sh:node` against a hand-written shape. `rdfl:PathLens` on `tm:path` maps to `:ShaclPathShape` (declared in `catalog-rdfc-manual.ttl`), which describes the property-path grammar rdf-lens can actually interpret. This is enforcement, not documentation: the path is consumed at **execution** time to extract a value from every record, so an unusable path is a runtime failure that validation now catches first — including the most likely mistake, a quoted string.
   - Not listed → demoted to a `tcs:upstreamClass` annotation. A constraint the toolchain can never satisfy is worse than a documented one it does not check. Adding a registry entry is how such a constraint gets promoted from documented to enforced.
4. `sh:datatype xsd:iri` → `sh:nodeKind sh:IRI`. **Not a typo upstream** — rdf-lens keys its own extractor off exactly that term (`ShaclPredicatePath = extractLeaf(XSD.terms.custom("iri"))`), so the marker is load-bearing at runtime. It is simply not valid *validation*: there is no `xsd:iri` datatype, so as SHACL it demands a literal no IRI can be. The original is preserved on `tcs:upstreamDatatype` so the runtime convention survives.

Rules 1 and 2 were reverse-engineered from `:SparqlIngestShape`, the one hand-written shape in the old catalog that actually fired. Rules 3 and 4 only surfaced once generation made every component's shape live — before, eleven of twelve components had no reachable shape at all, so neither problem was observable.

`:ShaclPathShape` accepts the six path forms `ShaclPath` implements in rdf-lens (bare IRI, RDF-list sequence, `sh:alternativePath`, `sh:inversePath`, `sh:zeroOrMorePath`, `sh:zeroOrOnePath`) and deliberately rejects `sh:oneOrMorePath`, which SHACL defines but rdf-lens does not implement — see the note in `catalog-rdfc-manual.ttl`. Validation deliberately matches execution rather than the spec. The accept/reject matrix is the executable spec in [`tests/test_shacl_path_shape.py`](tests/test_shacl_path_shape.py).

**What stays hand-written.** [`data/catalog-rdfc-manual.ttl`](data/catalog-rdfc-manual.ttl) holds everything with no upstream source: the orchestrator and its two `tcs:Config` literals (the compose fragment and the Dockerfile), the two runners, and components whose source is not resolvable from a registry or from this repo. Each of the two files contributes its own `dcat:resource` entries to `:DishacledCatalog`, so catalog membership sits next to the definitions it refers to and cannot outlive them.

Scope is RDF-Connect only. LDIO and semantic.works components have no machine-readable upstream definition to derive from, so `catalog-ldio.ttl` and `catalog-sw.ttl` remain hand-written.

Adding a new compiler is a matter of dropping a new file in the appropriate `compilers/<framework>/` subfolder (or `compilers/core/` for framework-agnostic ones) and importing it from `compilers/__init__.py`. `PipelineGenerator` discovers it via `Compiler._registry` and runs it whenever its `applies_to` returns `True`; the generator never needs editing. The notebook `demo.ipynb` demonstrates the end-to-end workflow.

## 4. Architecture

This section is a closer look at the `compilers` package. (`rdfine` has its own [README](src/rdfine/README.md); `catalog` is described in [§3.1](#31-generating-the-rdf-connect-catalog).)

The `catalog` package is deliberately **not** part of the `Compiler` hierarchy. Compilers are graph-to-graph transformations inside a single pipeline build; catalog generation runs before any build exists and crosses the boundary into the outside world, which puts it in the same category as `ProjectBuilder`. Its modules:

| Module | Purpose |
| --- | --- |
| [catalog/requests.py](src/catalog/requests.py) | Parse `tcs:CatalogRequest` blocks — the whole hand-written input. |
| [catalog/semver.py](src/catalog/semver.py) | Resolve a version range to a concrete version (npm caret/tilde/prerelease rules). |
| [catalog/registries.py](src/catalog/registries.py) | Fetch from npm / PyPI / a local checkout. The only module that touches the network. |
| [catalog/harvester.py](src/catalog/harvester.py) | Pick the Turtle file that declares the requested component, freeze it into the snapshot. |
| [catalog/snapshot.py](src/catalog/snapshot.py) | Read/write the committed `data/harvest/` records. |
| [catalog/shapes.py](src/catalog/shapes.py) | Translate an upstream SHACL shape into a toolchain config shape (the four rewrites). |
| [catalog/emitter.py](src/catalog/emitter.py) | Render the catalog file. Pure function of requests + snapshot. |
| [catalog/turtle.py](src/catalog/turtle.py) | Deterministic Turtle text emission, for stable diffs. |
| [catalog/cli.py](src/catalog/cli.py) | `python -m catalog {harvest,generate}`. |

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
| `PipelineAssembler` | exactly one `tcs:PipelineDefinition` in the graph and it has at least one `:hasStep` | the extracted pipeline | `tcs:DockerContainer`, `dct:hasPart`, `tcs:instantiates`, `tcs:runs`, `:isAssigned` |
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

### 5.2a. Topology is stated twice, and only partly cross-checked

A step declares its channel wiring in two places: framework-specifically inside its `tcs:embedded` config (`rdfc:reader demo:sdsMeasurements`) and framework-neutrally as `tcs:readsFrom` / `tcs:writesTo`. The compilers read the first, the application-profile shapes read the second. Two inference rules now derive `tcs:derivedReadsFrom` / `tcs:derivedWritesTo` from the config plus the generated config shapes, and `tcs:StepChannelWiringShape` requires that every derived edge was also declared — so a step that wires one channel and annotates another is now a validation error instead of a silent inconsistency.

The check is one-directional (derived must be a **subset** of declared) because most declared edges are legitimately underivable. Of 24 declared edges in the demonstrator, 14 are derived and all 14 agree; the other 10 break down as:

- **7 LDIO edges.** LDIO components have no channel-valued parameters at all — topology is implicit in the ordering of the `input` / `input.adapter` / `transformers[]` / `outputs[]` slots, which is what `tcs:LdioStepOrderingShape` enforces. For LDIO, the annotation is the *only* record of the topology, so it cannot be checked against anything.
- **1 cross-framework edge.** `demo:HttpIngest tcs:readsFrom demo:ldioToRdfcBridge`: `rdfc:HttpServer` receives an HTTP POST, not a channel, so there is no config property behind it. Same root cause as [§5.2](#52-cross-framework-channels).
- **2 edges on a component with no config shape.** `proc:JsonLdToNQuads` is hand-maintained (see [§3.1](#31-generating-the-rdf-connect-catalog)), so there is no `tcs:upstreamClass` to key on. These become derivable if its source is vendored and harvested.

Fully closing the loop therefore needs the LDIO ordering model and the cross-framework channel model, not more inference.

One sharp edge worth knowing: `tcs:upstreamClass` is overloaded. On a channel property it records the *direction* (`rdfc:Reader` / `rdfc:Writer`); on `tm:path` it records a *translated foreign class* (`rdfl:PathLens`). The inference rules therefore require `sh:class tcs:Channel` as well — keying on `tcs:upstreamClass` alone derives `sosa:hasSimpleResult` as a written channel. There is a regression test for exactly that.

### 5.2. Cross-framework channels

`tcs:Channel` is a first-class connector inside a framework (LDIO steps, RDF-Connect processors), but does not yet describe transports that cross framework boundaries. The LDIO → RDF-Connect HTTP hop and the RDF-Connect → semantic.works SPARQL Update push are modelled as opaque endpoints, with each side independently configured. The `oslc:Error` rule in §5.1 is the concrete downstream consequence.

### 5.3. Runtime `depends_on` in the emitted `docker-compose.yml`

`dct:requires` links between components already describe the runtime dependency chain (LDIO orchestrator → RDFC ingest, error-alert → delta-notifier → triple-store, …), but `DockerComposeCompiler` drops that information — the emitted compose file declares no `depends_on`, so Docker is free to boot services in any order. Downstream this manifests as first-boot races that a `docker compose restart` typically clears.

Note that this is about *runtime* start-up order, not the order services are written to the file: the emitted file is now sorted by service name and byte-stable across runs (see [`tests/test_docker_compose_determinism.py`](tests/test_docker_compose_determinism.py)). Making the boot order correct still requires translating `dct:requires` into `depends_on`.

### 5.4. LDIO service definition in the catalog

The LDIO workbench service is currently declared with an outdated image tag (`ldes/ldi-orchestrator:2.8.0-SNAPSHOT`) and no volume mounts. Any generated pipeline needs the operator to hand-patch the emitted compose file to use `2.13.0` + the two bind mounts the actual workbench expects — or to refactor to LDIO's Pattern A1 single-file startup model.

### 5.5. Catalog generation covers only RDF-Connect

[§3.1](#31-generating-the-rdf-connect-catalog) derives `catalog-rdfc.ttl` from upstream packages, but `catalog-ldio.ttl` (1136 lines) and `catalog-sw.ttl` (812 lines) are still transcribed by hand, because neither ecosystem publishes a machine-readable component definition. They therefore keep the drift characteristics the RDF-Connect catalog just lost.

Two smaller gaps inside the RDF-Connect path:

- **`owl:imports` values are unvalidated.** The generated IRIs are only correct while `PYTHON_VERSION` and `CONTAINER_WORKDIR` in `catalog/emitter.py` agree with the `FROM python:...` line and `WORKDIR` in the orchestrator's `tcs:DockerImageConfig`. A test asserts the pair matches, but nothing checks that the resulting path exists in the built image — `tcs:RdfcProcessorShape` only requires `sh:minCount 1` on `owl:imports`.
- **The read/write direction of a channel is preserved but unused.** Rewrite 1 collapses `rdfc:Reader` / `rdfc:Writer` into `tcs:Channel` and records the original on `tcs:upstreamClass`. Nothing consumes that annotation yet; it exists so the cross-framework channel work in [§5.2](#52-cross-framework-channels) does not have to re-derive the direction.
- **`EXTERNAL_SHAPES` has one entry.** `rdfl:PathLens` is the only foreign class whose value space is modelled, because it is the only one currently reached by a `sh:class` in a harvested shape. Any future foreign constraint degrades to an annotation until someone writes its shape — safe, but silently unchecked, so it is worth grepping generated output for `tcs:upstreamClass` without an accompanying `sh:node` when adding components.

### 5.6. Test-suite runner

Static SHACL validation of a pipeline definition against the tcs application profile can be run today via `GraphReader.validate()` on the merged catalog + pipeline graph — the demo notebook does exactly this. What is not yet built is the pre-generator runner design captured in [`test suite/README.md`](../../test%20suite/README.md), including the bridge to the external shape-matching algorithm for input/output shape validation.

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
- [ ] Cross-framework `tcs:Channel`: extend the channel model to describe transports that span framework boundaries (LDIO → RDFC HTTP hop, RDFC → sw SPARQL push, sw delta-notifier subscriptions). Prerequisite for synthesising the `oslc:Error` rule in `delta/rules.js` from the pipeline definition rather than shipping it as boilerplate.
- [x] RdfcDockerFileCompiler: Creates an adhoc Dockerfile for RDF-Connect. This allows to include only those dependencies in a docker container which are actually used in the pipeline.
- [x] Catalog generator for RDF-Connect: derive `catalog-rdfc.ttl` from each package's own published `processors.ttl` instead of transcribing it, so a component entry is a three-line request. Implemented as the `catalog` package with a committed harvest snapshot; see [§3.1](#31-generating-the-rdf-connect-catalog).
- [ ] Extend catalog generation beyond RDF-Connect. LDIO and semantic.works publish no machine-readable component definitions, so there is nothing to derive from today. For LDIO the closest source is the generated documentation site; for semantic.works, the per-service READMEs. Both would need a scraper rather than a parser, which is why they are still hand-written.
- [ ] Deterministic `docker-compose.yml` service order. `DockerComposeCompiler` merges compose fragments in graph-iteration order, so the emitted service order varies between runs on identical input (the service bodies are identical — only key order moves). Harmless to Docker, but it makes the committed `out/` diff noisy and blocks byte-comparison in tests.
- [ ] NifiCompiler: Compiler for [Nifi 2](https://nifi.apache.org/). 
- [x] PipelineGenerator: Uses all previously described compilers to route the compilation flow for generating a pipeline project folder based on a Pipeline Definition. Routing is done via the registry + `applies_to` mechanism described above; producing the project folder on disk is handled by `ProjectBuilder`.


## 7. How To

### 6.1. How to write your own Pipeline Definition
You can find a couple of examples in the catalog.ttl file in the data folder. A Pipeline Definition is as simple as a bunch of InstancePipelineComponents, linked to each other. Each InstancePipelineComponent receives a Config and is carried out by a component of the catalog. So you do not need a lot to write a Pipeline Definition. This part will be made easier in the future by providing a frontend.


### 6.2. How to onboard your own components to the catalog

**For an RDF-Connect processor, add three lines and run two commands.** The catalog entry is generated from the package's own `processors.ttl` — see [§3.1](#31-generating-the-rdf-connect-catalog). Append to `data/catalog-rdfc-requests.ttl`:

```turtle
rdfc:MyProcessor a tcs:CatalogRequest ;
    tcs:package "@rdfc/my-processor-ts" ;
    spdx:versionInfo "^1.0.0" .
```

then `python -m catalog harvest && python -m catalog generate`. If the package ships several Turtle files and auto-detection picks the wrong one, add `tcs:sourceFile "configs/mine.ttl"`. If the source is not published, vendor it into the repo and point `tcs:fromPath` at it plus `spdx:downloadLocation` at the container path. Harvesting fails loudly if the component is not declared in the package, which is what catches an upstream rename.

**For everything else** (LDIO, semantic.works, or an RDF-Connect component with no resolvable source) the catalog is hand-written. Check the catalog in the data folder for examples. At a minimum, a Pipeline Component needs either a DockerComposeConfig or a dependency to a component with a DockerComposeConfig. The assumption is that each DockerComposeConfig resolves all dependencies of components attached to it, including itself. Depending on the framework, you may also need to include framework-specific properties. For example, the LdioConfigCompiler needs to be able to look up ldio:type and rdf:label, whereas the RdfcConfigCompiler needs to be able to look up owl:imports. This is explicitly mentioned in the sh:NodeShapes a tcs:Compiler points to. Everything else is optional, but it is a good idea to include as many NodeShapes as possible: It serves as a lookup reference for the constraints that need to be fulfilled once a component is included in a pipeline. A typical NodeShape for a tcs:PipelineComponent is for example the schema of its expected Config.


### 6.3. How to onboard new frameworks
- Describe pipeline components with the semantic model and add them to the catalog.
- Write compilers for your new framework that can produce the expected output files. Each compiler must subclass `Compiler` (from `compilers.base`), implement `compile(self) -> Graph` returning the enriched build graph, and override `applies_to(cls, graph_reader) -> bool` to declare the graph-state conditions under which the compiler should run — typically the presence of a framework-specific node type or predicate in the build graph. The default `applies_to` returns `False`, so this override is required. If your compiler must run only after other shaping compilers have finished (for example because it consumes their output), gate its `applies_to` on `<build> tcs:isFinishing true`, which `PipelineGenerator` sets whenever a shaping pass has settled (see §4.4).
- Compilers that produce a file should end their `compile()` method with a call to `attach_file(self.output_reader, filename=..., filepath=..., content=...)` (from `compilers.utils`), re-assigning `self.output_reader` with the returned reader. The helper adds an `spdx:File` node to the `tcs:PipelineBuild` carrying the produced file body as a literal.
- Import the new compiler from `compilers/__init__.py`. The auto-registration mechanism (`Compiler.__init_subclass__`) then makes it visible to `PipelineGenerator`, which will run it automatically against any pipeline whose build graph matches its `applies_to` condition. New compilers usually belong in a per-framework subfolder (`compilers/<framework>/`); framework-agnostic ones go in `compilers/core/`. No edits to `PipelineGenerator` are required.
- Test the new compilers based on a Pipeline Definition that includes components of the new framework.


