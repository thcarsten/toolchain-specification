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
This deck is about intent and mechanism, not line-by-line code.
Walk-through: the problem, the core idea, three design decisions,
then a concrete view of the compilers, then the roadmap.
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

---

## 6. Decision 2 — Small compilers, self-registering

**Choice.** One responsibility per compiler. The driver never names them —
it iterates a registry, sorted into three tiers:

<div class="tiers">
  <div class="tier">
    <div class="label">SEED</div>
    <div class="role">seed the graph</div>
    <ul>
      <li>PipelineExtractor</li>
      <li>PipelineAssembler</li>
    </ul>
  </div>
  <div class="tier-arrow">→</div>
  <div class="tier">
    <div class="label">BUILD</div>
    <div class="role">shape the graph</div>
    <ul>
      <li>SemanticWorksCompiler</li>
    </ul>
  </div>
  <div class="tier-arrow">→</div>
  <div class="tier">
    <div class="label">FILE</div>
    <div class="role">attach files as nodes</div>
    <ul>
      <li>LdioConfigCompiler</li>
      <li>RdfcConfigCompiler</li>
      <li>DockerComposeCompiler</li>
    </ul>
  </div>
</div>

Each compiler carries an `applies_to()` check that inspects the graph and decides
whether it should run. **Adding a framework = dropping in a new file.**

---

## 7. Decision 3 — The build graph is self-describing

**Choice.** Generated files aren't kept in a side dictionary.
They're attached to the build graph as first-class RDF nodes.

<div class="buildgraph">
  <div class="stage">
    <div class="node">tcs:PipelineBuild</div>
    <div class="files">
      <div class="file"><b>tcs:File</b> — ldio/config.yml<br/><span style="color:#666">tcs:literal &nbsp;"name: …\ninput: …"</span></div>
      <div class="file"><b>tcs:File</b> — rdfc/pipeline.ttl<br/><span style="color:#666">tcs:literal &nbsp;"@prefix … ."</span></div>
      <div class="file"><b>tcs:File</b> — docker-compose.yml<br/><span style="color:#666">tcs:literal &nbsp;"services: …"</span></div>
    </div>
    <div class="caption">Files hang off the build via <code>tcs:compiledFile</code>.
    Their body lives in <code>tcs:literal</code>.</div>
  </div>
</div>

`ProjectBuilder` just walks these nodes and writes each one to disk.
Nothing else touches the filesystem.

---

## 8. The design in practice — six compilers

| Compiler | Tier | Triggers on | Produces |
| --- | --- | --- | --- |
| PipelineExtractor | SEED | always | triples for the requested pipeline |
| PipelineAssembler | SEED | always | containers, steps, config assignments |
| SemanticWorksCompiler | BUILD | any `sw:` component | env vars folded into the microservice's docker config |
| LdioConfigCompiler | FILE | container instantiating the LDIO orchestrator | `ldio/config.yml` |
| RdfcConfigCompiler | FILE | container instantiating the RDF-Connect orchestrator | `rdfc/pipeline.ttl` |
| DockerComposeCompiler | FILE | any `tcs:DockerComposeConfig` node | `docker-compose.yml` |

Six small classes. One driver. Every column above is a direct expression
of the three decisions in the previous slides.

---

## 9. Where we go next

**Already delivered**
- Support for RDF-Connect, LDIO, semantic.works — interoperable pipelines.
- Automatic Docker Compose wiring across frameworks.
- Self-describing build graphs and a filesystem projection layer.

**Coming**
- Ad-hoc Dockerfile generation for RDF-Connect
  (only ship the components a pipeline actually uses).
- A version mapper decoupling the public semantic model from the internal one.
- Validation: a deliverable of its own (more on that next).

**Synergies**
- WP1: a semantic model compatible with the discovery specification.
- WP3: a frontend to author pipeline definitions.
- WP4: the Pipeline Generator uses the demonstrator pipeline as its [demo notebook](https://github.com/thcarsten/toolchain-specification/blob/main/pipeline%20generator/src/demo.ipynb).

**Thank you — questions welcome.**
