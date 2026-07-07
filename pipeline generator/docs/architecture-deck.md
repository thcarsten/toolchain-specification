---
marp: true
theme: default
paginate: true
size: 16:9
title: Pipeline Generator — Architecture & Design
style: |
  section { font-size: 26px; padding: 55px 70px; }
  section h1 { font-size: 42px; }
  section h2 { font-size: 32px; margin-bottom: 0.4em; }
  section ul, section ol { margin: 0.3em 0; }
  section li { margin: 0.15em 0; }
  section p { margin: 0.4em 0; }
  section table { font-size: 20px; }
  section table th { background: #f0f4fa; }
  code { font-size: 0.9em; }

  /* Horizontal flow of labelled boxes */
  .flow {
    display: flex; align-items: stretch; justify-content: center;
    gap: 12px; margin: 24px 0;
  }
  .flow .box {
    flex: 1; border: 2px solid #444; border-radius: 10px;
    padding: 12px 14px; background: #f6f6f6; text-align: center;
    display: flex; flex-direction: column; justify-content: center;
    font-size: 0.85em;
  }
  .flow .box.accent { background: #eef4ff; border-color: #2f6feb; }
  .flow .box .title { font-weight: 700; margin-bottom: 4px; font-size: 1.05em; }
  .flow .arrow {
    align-self: center; font-size: 30px; color: #666; padding: 0 2px;
  }

  /* Tier conveyor: 3 vertical stacks side-by-side */
  .tiers { display: flex; gap: 14px; margin: 20px 0; align-items: stretch; }
  .tier {
    flex: 1; border: 2px solid #ccc; border-radius: 10px;
    padding: 12px 14px; background: #fafafa;
  }
  .tier .label {
    font-weight: 700; font-size: 0.75em; letter-spacing: 0.08em;
    color: #2f6feb; margin-bottom: 4px;
  }
  .tier .role { font-size: 0.9em; color: #555; margin-bottom: 8px; }
  .tier ul { margin: 0; padding-left: 1em; font-size: 0.85em; }
  .tier-arrow {
    align-self: center; font-size: 26px; color: #888;
  }

  /* Build-graph node with file children */
  .buildgraph {
    display: flex; justify-content: center; margin: 24px 0;
  }
  .buildgraph .stage {
    flex: 1; text-align: center; padding: 0 8px;
  }
  .buildgraph .node {
    display: inline-block; padding: 10px 16px; border-radius: 12px;
    border: 2px solid #2f6feb; background: #eef4ff; font-weight: 700;
    font-size: 0.9em;
  }
  .buildgraph .files {
    margin-top: 14px; display: flex; flex-direction: column;
    gap: 6px; align-items: center;
  }
  .buildgraph .file {
    border: 1.5px dashed #666; border-radius: 8px;
    padding: 6px 12px; background: #fff; font-size: 0.75em;
    min-width: 60%;
  }
  .buildgraph .caption {
    font-size: 0.75em; color: #666; margin-top: 6px;
  }

  /* Two-column layout */
  .two-col { display: flex; gap: 28px; }
  .two-col > div { flex: 1; }
---

# Pipeline Generator
## Architecture & Design

The Dishacled Pipeline Generator -
what it does and how it works.

<!--
Companion notes for the presenter. Assumes comfort with RDF, Turtle, SPARQL, rdflib.

REPOSITORY CONTEXT
- Location: toolchain-specification/pipeline generator/. Compilers under src/compilers/. End-to-end walkthrough in src/demo.ipynb. Sample catalog in data/catalog.ttl.
- What it does: reads a pipeline definition and a component catalog (both RDF), assembles an in-memory build graph, emits a project folder with framework-specific configs and docker-compose.yml.
- Target frameworks: RDF-Connect, LDIO, semantic.works.
- rdfine (src/rdfine/): in-repo wrapper over rdflib exposing GraphReader (filter, traverse, select, construct, add). Used by every compiler.
- tcs: namespace: tcs:PipelineBuild, tcs:DockerContainer, tcs:DockerComposeConfig, tcs:compiledFile, tcs:filename, tcs:filepath, tcs:literal, tcs:instantiates, tcs:runs. spdx: namespace: spdx:File.

DECK STRUCTURE
Problem, core idea, three design decisions, compiler set, roadmap.

FALLBACK FRAMING
For any deep implementation question: source-code compiler with an RDF graph in place of an AST. Inputs are RDF (definition + catalog); the intermediate representation is an RDF build graph; code generation is a set of small compilers that attach spdx:File nodes carrying the emitted text.
-->

---

## 1. The problem

Linked Data tooling is largely community-driven and diverse.
Modern data pipelines should integrate components from **multiple frameworks** in a single flow:
RDF-Connect, LDIO, semantic.works, and more to come.

Each framework brings its **own config language, conventions, implementation logic, and dependencies**.
Building an interoperable pipeline by hand means:

- Writing configurations in three or four different formats.
- Debugging often-implicit implementation constraints.
- Reinventing the wiring every time a new framework enters the mix.

**This doesn't scale.** It's the kind of work a computer should do.

<!--
SLIDE 1 - THE PROBLEM
Mixing components from multiple frameworks (RDF-Connect, LDIO, semantic.works) currently requires hand-writing configs in several formats, working around implicit implementation constraints, and rewiring everything whenever the framework mix changes.
-->

---

## 2. The core idea

Describe the pipeline **once, semantically** —
then generate everything else from that description. Implementation logic is no longer implicit; it becomes runnable code.

- One RDF description of *what* the pipeline does.
- A catalog of reusable components (also RDF).
- A tool that reads both and emits every framework-specific
  artifact the pipeline needs to run.

The RDF description is the **single source of truth**.
Everything downstream — configs, Docker Compose, container wiring —
is derived.

<!--
SLIDE 2 - THE CORE IDEA
One RDF description of the pipeline plus a component catalog (also RDF) are the single source of truth. Every framework-specific artifact is derived from them.

Q: Why RDF for the internal build state and not a Python object model?
A: The inputs are already RDF (public toolchain specification and the catalog); using RDF throughout avoids a translation layer, keeps the vocabulary extensible without schema migrations, and makes every intermediate inspectable with SPARQL.
-->

---

## 3. End-to-end flow

<div class="flow">
  <div class="box">
    <div class="title">Pipeline Definition</div>
    <div>RDF — <em>what</em> the pipeline does</div>
  </div>
  <div class="box">
    <div class="title">Component Catalog</div>
    <div>RDF — reusable building blocks</div>
  </div>
  <div class="arrow">→</div>
  <div class="box accent">
    <div class="title">Pipeline Generator</div>
    <div>builds an in-memory<br/>RDF <em>build graph</em></div>
  </div>
  <div class="arrow">→</div>
  <div class="box">
    <div class="title">Project Folder</div>
    <div><code>docker-compose.yml</code>,<br/>framework configs</div>
  </div>
</div>

The user writes **semantics**.
The tool writes **YAML, Turtle, and Docker Compose**.

<!--
SLIDE 3 - END-TO-END FLOW
Two RDF inputs (pipeline definition + catalog) -> the generator, which builds an in-memory RDF build graph -> a project folder on disk. This is where the term "build graph" enters; it is used throughout the rest of the deck.
-->

---

## 4. Three design decisions

Everything else follows from three choices:

1. **Everything is a graph transformation.**
   Compilers generate a build graph that exhaustively describes how a project should be built.
2. **Small compilers, self-registering.**
   Each compiler declares when it should run and how it acts on the graph. Framework extensions stay modular; the core is never touched.
3. **The build graph is self-describing.**
   The output describes itself, including the files it will become. Writing to disk is the last step — projecting the build from RDF space into the file system.

The next three slides go through each in turn.

<!--
SLIDE 4 - THREE DESIGN DECISIONS
Signpost slide. Three decisions: (1) generation as graph transformation, (2) small self-registering compilers, (3) a self-describing build graph. Each is unpacked on the following slide.
-->

---

## 5. Decision 1 — Everything is a graph transformation

**Choice.** Generation is graph-in, graph-out. Disk IO happens
only at the very end, in a single dedicated component.

<div class="flow">
  <div class="box"><div class="title">Catalog graph</div><div>all known resources</div></div>
  <div class="arrow">→</div>
  <div class="box"><div class="title">Seeded</div><div>one pipeline extracted</div></div>
  <div class="arrow">→</div>
  <div class="box"><div class="title">Built</div><div>containers, steps, configs assigned</div></div>
  <div class="arrow">→</div>
  <div class="box accent"><div class="title">Enriched</div><div>files attached as nodes</div></div>
  <div class="arrow">→</div>
  <div class="box"><div class="title">Project</div><div>written to disk</div></div>
</div>

Every arrow is a **compiler**. Every stop is just an RDF graph.
Debugging = look at the graph. Dry-runs = free.

<!--
SLIDE 5 - DECISION 1: EVERYTHING IS A GRAPH TRANSFORMATION
- Every compiler takes an rdflib.Graph and returns an enriched rdflib.Graph. The only I/O happens in ProjectBuilder, which is not a Compiler subclass.
- The five-stop diagram sequences the graph's states: catalog -> seeded (extractor) -> built (assembler and shaping compilers such as SemanticWorksCompiler, LdioConfigCompiler, RdfcConfigCompiler) -> enriched with the docker-compose file node (DockerComposeCompiler, during the finishing pass) -> written to disk.
- Because every intermediate is a graph, inspection at any stage is a matter of calling .compile() and looking at the result.
-->

---

## 6. Decision 2 — Small compilers, self-registering

**Choice.** One responsibility per compiler. The driver never names them —
each compiler declares **when** it should run via an `applies_to()` check
against the current graph, and the driver runs the eligible ones in a
fixpoint loop until nothing new fires.

<div class="tiers">
  <div class="tier">
    <div class="label">SEED THE GRAPH</div>
    <div class="role">extract & assemble</div>
    <ul>
      <li>PipelineExtractor</li>
      <li>PipelineAssembler</li>
    </ul>
  </div>
  <div class="tier-arrow">→</div>
  <div class="tier">
    <div class="label">SHAPE THE GRAPH</div>
    <div class="role">framework specifics</div>
    <ul>
      <li>SemanticWorksCompiler</li>
      <li>LdioConfigCompiler</li>
      <li>RdfcConfigCompiler</li>
    </ul>
  </div>
  <div class="tier-arrow">→</div>
  <div class="tier">
    <div class="label">WRAP UP</div>
    <div class="role">once the graph has settled</div>
    <ul>
      <li>DockerComposeCompiler</li>
    </ul>
  </div>
</div>

The groups above are illustrative — there are no class-level phases in the
code. Execution order **emerges** from each compiler's trigger condition
becoming true as the graph grows. **Adding a framework = dropping in a new file.**

<!--
SLIDE 6 - DECISION 2: SMALL COMPILERS, SELF-REGISTERING
- Concrete compilers subclass Compiler in src/compilers/base.py. Compiler.__init_subclass__ appends every non-abstract subclass to Compiler._registry; PipelineGenerator iterates that registry every loop iteration.
- No tier attribute. Ordering emerges from each compiler's applies_to condition. The illustrative "seed / shape / wrap up" grouping above is a reading aid, not a code construct.
- Each compiler defines an applies_to(graph_reader) classmethod that inspects the build graph and returns a boolean. Default is False, so every concrete compiler must declare its trigger explicitly.
- Adding a framework: drop a new Compiler subclass into src/compilers/, import it from compilers/__init__.py. The driver is not touched.

Q: How is compiler ordering guaranteed?
A: It emerges. A compiler runs as soon as its applies_to trigger evaluates true, and does not run again once it has. Compilers eligible in the same loop iteration must be commutative; in practice each operates on a disjoint slice of the build graph selected by its applies_to check.

Q: How does DockerComposeCompiler know to run last?
A: PipelineGenerator maintains a temporary triple `<build> tcs:isFinishing true` on the build whenever a shaping pass finds nothing eligible. DockerComposeCompiler's applies_to gates on that flag. If it (or any other compiler) runs during the finishing pass, the flag flips back to false and shaping resumes; the loop terminates only when a finishing pass itself is empty. The flag is stripped from the graph before compile() returns.

Q: How does the driver know what to run?
A: Compiler.__init_subclass__ populates the class-level registry on import. PipelineGenerator instantiates PipelineExtractor up front (because it needs the pipeline_id), then loops over the registry until no eligible compiler is found in either a normal or a finishing pass.
-->

---

## 7. Decision 3 — The build graph is self-describing

**Choice.** Generated files aren't kept in a side dictionary.
They're attached to the build graph as first-class RDF nodes.

<div class="buildgraph">
  <div class="stage">
    <div class="node">tcs:PipelineBuild</div>
    <div class="files">
      <div class="file"><b>spdx:File</b> — ldio/config.yml<br/><span style="color:#666">tcs:literal &nbsp;"name: …\ninput: …"</span></div>
      <div class="file"><b>spdx:File</b> — rdfc/pipeline.ttl<br/><span style="color:#666">tcs:literal &nbsp;"@prefix … ."</span></div>
      <div class="file"><b>spdx:File</b> — docker-compose.yml<br/><span style="color:#666">tcs:literal &nbsp;"services: …"</span></div>
    </div>
    <div class="caption">Files hang off the build via <code>tcs:compiledFile</code>.
    Their body lives in <code>tcs:literal</code>.</div>
  </div>
</div>

`ProjectBuilder` just walks these nodes and writes each one to disk.
Nothing else touches the filesystem.

<!--
SLIDE 7 - DECISION 3: THE BUILD GRAPH IS SELF-DESCRIBING
- File-producing compilers do not return paths or strings. They call the free helper attach_file(self.output_reader, filename, filepath, content) from compilers.utils inside compile() and re-assign self.output_reader with its return value; this adds an spdx:File node to the build graph and links it to the tcs:PipelineBuild via tcs:compiledFile. The file body is stored on tcs:literal.
- The file-node IRI is derived by slugifying filepath_filename, so the same (filepath, filename) pair yields a stable IRI across runs.
- ProjectBuilder reads the spdx:File nodes off the compiled build graph and writes them to disk. Planned writes are collected into a pandas.DataFrame on builder.files first, so they can be inspected without touching the filesystem. A path-traversal guard rejects any tcs:filepath that would escape the target directory.
- Consequence: a compiled build is a single serializable RDF artifact. Provenance, caching, diffing, and replay reduce to graph operations.

Q: Why bake file bodies into the graph as tcs:literal rather than side-tables?
A: So the compiled build is a single serializable artifact. Provenance, caching, diffing, and replay become graph operations; ProjectBuilder stays trivial.
-->

---

## 8. The design in practice — six compilers

| Compiler | Triggers on (`applies_to`) | Produces |
| --- | --- | --- |
| PipelineExtractor | always | triples for the requested pipeline; seeds `tcs:PipelineBuild` linked to the definition via `prov:hadPlan` |
| PipelineAssembler | exactly one `tcs:PipelineDefinition` with at least one `:hasStep` | containers, steps, config assignments |
| SemanticWorksCompiler | any `sw:` component present, and `tcs:DockerContainer`s exist | env vars folded into the microservice's docker config |
| LdioConfigCompiler | a container instantiates the LDIO orchestrator | `ldio/config.yml` |
| RdfcConfigCompiler | a container instantiates the RDF-Connect orchestrator | `rdfc/pipeline.ttl` |
| DockerComposeCompiler | `<build> tcs:isFinishing true` set (i.e. loop has settled) and any `tcs:DockerComposeConfig` present | `docker-compose.yml` |

Six small classes. One driver. Every column above is a direct expression
of the three decisions in the previous slides.

<!--
SLIDE 8 - THE DESIGN IN PRACTICE
The six-row table lists every concrete compiler with its trigger and output. PipelineExtractor is instantiated up front by PipelineGenerator because it is the only compiler needing the pipeline_id in its constructor; every other compiler is discovered via the registry and run when its applies_to trigger becomes true.

VOCABULARY (for reference)
- Pipeline Definition: RDF description of a data pipeline, expressed against the public semantic model.
- Component / Catalog: a component is a reusable pipeline building block; the catalog is the RDF library of all available components (data/catalog.ttl).
- Build graph: the in-memory rdflib.Graph the generator assembles. All build state lives here.
- tcs:PipelineBuild: root node of a build graph. Generated files hang off it via tcs:compiledFile.
- spdx:File: RDF node representing a generated file. Carries tcs:filename, tcs:filepath, tcs:literal (the body).
- Compiler: subclass in src/compilers/. Takes the build graph, transforms it, returns it. Registers automatically on import.
- applies_to: classmethod on every compiler. Decides whether the compiler should run against a given build graph. Default is False; every concrete compiler declares its own trigger. Execution order emerges from these triggers becoming true as the graph grows.
- tcs:isFinishing: temporary triple on the build (`<build> tcs:isFinishing <bool>`) that PipelineGenerator toggles to true whenever a shaping pass finds nothing eligible. Compilers that need to run only after the graph has settled (DockerComposeCompiler) gate on this flag. Stripped from the graph before compile() returns.
- rdfine / GraphReader: in-repo wrapper over rdflib used by every compiler.
- ProjectBuilder: the filesystem boundary. Walks the spdx:File nodes and writes them out.

Q: What happens when two frameworks share a container arrangement?
A: Each framework compiler emits its own config file. They meet in docker-compose.yml, produced by a single framework-agnostic DockerComposeCompiler that reads every tcs:DockerComposeConfig node.

Q: How is this tested?
A: Every compiler is a graph-in / graph-out function, so unit tests are graph fixtures with assertions on the output graph. src/demo.ipynb is the end-to-end reference.
-->

---

## 9. Where we go next

**Already delivered**
- Support for RDF-Connect, LDIO, semantic.works — interoperable pipelines.
- Automatic Docker Compose wiring across frameworks.
- Self-describing build graphs and a filesystem projection layer.

**Coming**
- Ad-hoc Dockerfile generation for RDF-Connect
  (only ship the components a pipeline actually uses).
- NifiCompiler for Urban Sense.
- Validation: a deliverable of its own (more on that next).

**Synergies**
- WP1: a semantic model compatible with the discovery specification.
- WP3: a frontend to author pipeline definitions.
- WP4: the Pipeline Generator uses the demonstrator pipeline as its [demo notebook](https://github.com/thcarsten/toolchain-specification/blob/main/pipeline%20generator/src/demo.ipynb).

<!--
SLIDE 9 - WHERE WE GO NEXT
- Already delivered: multi-framework interoperability (RDF-Connect + LDIO + semantic.works), automatic Docker Compose wiring across frameworks, self-describing build graphs, filesystem projection.
- Coming: ad-hoc Dockerfile generation for RDF-Connect (ship only the components a pipeline actually uses); NifiCompiler for Urban Sense; validation as a separate deliverable.
- Synergies: WP1 discovery-compatible semantic model, WP3 authoring frontend, WP4 uses demo.ipynb as its demonstrator pipeline.
-->