# Presenter's Handout — Pipeline Generator: Architecture & Design

Companion to the deck. Assumes the presenter is comfortable with RDF, Turtle, SPARQL, and `rdflib`; provides the repository-specific context needed to speak to this codebase.

## Repository context

- **Location.** `toolchain-specification/pipeline generator/`. Compilers live under `src/compilers/`. An end-to-end walkthrough is in `src/demo.ipynb`. The sample catalog is `data/catalog.ttl`.
- **What the tool does.** Reads a pipeline definition and a component catalog (both RDF), assembles an in-memory *build graph*, and emits a project folder containing framework-specific configs and a `docker-compose.yml`.
- **The three target frameworks.**
  - **RDF-Connect** 
  - **LDIO** 
  - **semantic.works** 
- **`rdfine`.** An in-repo wrapper over `rdflib` (`src/rdfine/`) exposing `GraphReader` with `filter`, `traverse`, `select`, `construct`, and `add` helpers. Used by every compiler to keep graph reads and writes concise.
- **Custom vocabulary.** The generator uses the `tcs:` namespace (toolchain specification) for its build-time nodes: `tcs:PipelineBuild`, `tcs:DockerContainer`, `tcs:DockerComposeConfig`, `tcs:File`, `tcs:compiledFile`, `tcs:filename`, `tcs:filepath`, `tcs:literal`, `tcs:instantiates`, `tcs:runs`.

## Slide-by-slide notes

**Title.** Pipeline Generator — Architecture & Design. The deck covers the problem, the core idea, three design decisions, the compiler set, and the roadmap.

**Slide 1 — The problem.** Mixing components from multiple frameworks (RDF-Connect, LDIO, semantic.works) currently requires hand-writing configs in several formats, working around implicit implementation constraints, and rewiring everything whenever the framework mix changes.

**Slide 2 — The core idea.** One RDF description of the pipeline plus a component catalog (also RDF) are the single source of truth. Every framework-specific artifact is derived from them.

**Slide 3 — End-to-end flow.** Two RDF inputs (pipeline definition + catalog) → the generator, which builds an in-memory RDF *build graph* → a project folder on disk. This is where the term "build graph" enters; it is used throughout the rest of the deck.

**Slide 4 — Three design decisions.** Signpost slide. The three decisions are: (1) generation as graph transformation, (2) small self-registering compilers, (3) a self-describing build graph. Each is unpacked on the following slide.

**Slide 5 — Decision 1: everything is a graph transformation.**
- Every compiler takes an `rdflib.Graph` and returns an enriched `rdflib.Graph`. The only I/O happens in `ProjectBuilder`, which is not a `Compiler` subclass.
- The five-stop diagram sequences the graph's states: empty → seeded (extractor) → shaped (assembler and any BUILD-tier compilers) → enriched with file nodes (FILE-tier compilers) → written to disk.
- Because every intermediate is a graph, inspection at any stage is a matter of calling `.compile()` and looking at the result.

**Slide 6 — Decision 2: small compilers, self-registering.**
- Concrete compilers subclass `Compiler` in `src/compilers/base.py`. `Compiler.__init_subclass__` appends every non-abstract subclass to a class-level `Compiler._registry`; the driver iterates that registry sorted by `Tier`. There is no explicit compiler list in the driver.
- Tiers: `SEED` (extractor + assembler — always run first and unconditionally), `BUILD` (shape the graph, e.g. `SemanticWorksCompiler`), `FILE` (attach a `tcs:File` node — the LDIO, RDF-Connect, and Docker Compose compilers).
- Each compiler defines an `applies_to(graph_reader)` classmethod that inspects the build graph and returns a boolean. Default is `True`. FILE-tier compilers typically override it with a single `filter` check (e.g. "is there a container instantiating the LDIO orchestrator?").
- Adding a framework: drop a new `Compiler` subclass into `src/compilers/`, import it from `compilers/__init__.py`. The driver is not touched.

**Slide 7 — Decision 3: the build graph is self-describing.**
- FILE-tier compilers do not return paths or strings. They call `self._attach_file(filename, filepath, content)` from inside `compile()`, which adds a `tcs:File` node to the build graph and links it to the `tcs:PipelineBuild` via `tcs:compiledFile`. The file body is stored on `tcs:literal`.
- The file-node IRI is derived by slugifying `filepath_filename`, so the same `(filepath, filename)` pair yields a stable IRI across runs.
- `ProjectBuilder` reads the `tcs:File` nodes off the compiled build graph and writes them to disk. Planned writes are collected into a `pandas.DataFrame` on `builder.files` first, so they can be inspected without touching the filesystem. A path-traversal guard rejects any `tcs:filepath` that would escape the target directory.
- Consequence: a compiled build is a single serializable RDF artifact. Provenance, caching, diffing, and replay reduce to graph operations.

**Slide 8 — The design in practice.** The six-row table lists every concrete compiler with its tier, trigger, and output. The two SEED compilers (`PipelineExtractor`, `PipelineAssembler`) are excluded from registry dispatch and hard-wired at the top of `PipelineGenerator.compile()`; every other compiler is discovered via the registry.

**Slide 9 — Where we go next.**
- *Already delivered*: multi-framework interoperability (RDF-Connect + LDIO + semantic.works), automatic Docker Compose wiring across frameworks, self-describing build graphs, filesystem projection.
- *Coming*: ad-hoc Dockerfile generation for RDF-Connect (ship only the components a pipeline actually uses); a version mapper decoupling the public semantic model from the internal one; validation as a separate deliverable.
- *Synergies*: WP1 discovery-compatible semantic model, WP3 authoring frontend, WP4 uses `demo.ipynb` as its demonstrator pipeline.

## Vocabulary cheat sheet

| Term | Meaning in this codebase |
| --- | --- |
| **Pipeline Definition** | An RDF description of a data pipeline, expressed against the public semantic model. |
| **Component / Catalog** | A component is a reusable pipeline building block. The catalog is the RDF library of all available components (`data/catalog.ttl`). |
| **Build graph** | The in-memory `rdflib.Graph` the generator assembles. All build state lives here. |
| **`tcs:PipelineBuild`** | Root node of a build graph. Generated files hang off it via `tcs:compiledFile`. |
| **`tcs:File`** | RDF node representing a generated file. Carries `tcs:filename`, `tcs:filepath`, and `tcs:literal` (the body). |
| **`Compiler`** | Subclass in `src/compilers/`. Takes the build graph, transforms it, returns it. Registers automatically on import. |
| **`Tier`** | `Tier.SEED` / `Tier.BUILD` / `Tier.FILE`. Sequences the compilers; within a tier, ordering must not matter. |
| **`applies_to`** | Classmethod on every compiler. Decides whether the compiler should run against a given build graph. |
| **`rdfine` / `GraphReader`** | In-repo wrapper over `rdflib` used by every compiler for graph reads and writes. |
| **`ProjectBuilder`** | The filesystem boundary. Walks the `tcs:File` nodes of a compiled build graph and writes them out. |

## Likely questions

- **Why RDF for the internal build state and not a Python object model?** The inputs are already RDF (the public toolchain specification and the catalog); using RDF throughout avoids a translation layer, keeps the vocabulary extensible without schema migrations, and makes every intermediate inspectable with SPARQL.
- **Why bake file bodies into the graph as `tcs:literal` rather than side-tables?** So the compiled build is a single serializable artifact. Provenance, caching, diffing, and replay become graph operations; `ProjectBuilder` stays trivial.
- **How is compiler ordering guaranteed within a tier?** It isn't. Compilers in the same tier must be commutative. In practice each FILE-tier compiler operates on a disjoint slice of the build graph selected by its `applies_to` check.
- **What happens when two frameworks share a container arrangement?** Each framework compiler emits its own config file. They meet in `docker-compose.yml`, produced by a single framework-agnostic `DockerComposeCompiler` that reads every `tcs:DockerComposeConfig` node.
- **How is this tested?** Every compiler is a graph-in / graph-out function, so unit tests are graph fixtures with assertions on the output graph. `src/demo.ipynb` is the end-to-end reference.
- **How does the driver know what to run?** `Compiler.__init_subclass__` populates a class-level registry on import. `PipelineGenerator` runs the two SEED compilers unconditionally, then iterates the remaining registry sorted by tier, calling each compiler's `applies_to` and invoking those that return `True`.

## Fallback framing

If a question digs into implementation detail beyond the slides, the mental model to fall back on is a source-code compiler with an RDF graph in place of an AST: inputs are RDF (definition + catalog), the intermediate representation is an RDF build graph, and code generation is a set of small compilers attaching `tcs:File` nodes that carry the emitted text.
