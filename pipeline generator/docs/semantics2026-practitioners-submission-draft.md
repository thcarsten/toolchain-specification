# SEMANTiCS 2026 — Practitioners Track — Submission Draft

Target: <https://2026-eu.semantics.cc/page/cfp_practitioners.html>
Submission portal: EasyChair (`semantics2026`, track *Practitioners Track*).
Deadline: **August 29, 2026, 23:59 AoE**.
Format: EasyChair form fields only — **no paper is submitted**.

---

## 1. Title

**Compiling and Validating RDF Pipeline Definitions into Multi-Framework Docker Compose Stacks**

---

## 2. Authors


- **Thomas Carsten** — *Sirus NV* — `thomas.carsten@sirus.be`
- *(co-authors, if any — add here)*

Presenting author (on-site in Ghent): Thomas Carsten.

---

## 3. Abstract

Constructing pipelines in practice remains a largely
manual engineering task. Developers must discover suitable components across
multiple repositories, understand their documentation, resolve installation
and framework-specific configuration, and integrate components
from different ecosystems. While individual pipeline frameworks — LDIO,
RDF-Connect, NiFi, semantic.works — provide a smooth experience within their
own boundaries, interoperability is generally not
provided: combining heterogeneous components requires substantial manual
effort in configuration, dependency management, and
resolution of often implicit assumptions.

We present a **pipeline generator** that acts as a translation layer between
framework-independent semantic pipeline descriptions and framework-specific
implementation logic, automating dependency resolution, configuration
generation and deployment preparation. Given a semantic pipeline description
in RDF, the generator extracts all participating components — including
those introduced through dependency relationships — and retrieves their
metadata (default configurations, execution requirements, constraints) from
a component catalogue. It then assembles a pipeline build: components that
provide Docker Compose specifications become independent microservices,
others are assigned to microservices they depend on, yielding a full
deployment topology. A collection of compilers subsequently transforms the
framework-independent representation into framework-specific configuration
artefacts, with SHACL shapes carried in the component metadata evaluated
prior to deployment. Combined with containerisation, this enables the
construction of interoperable pipelines spanning multiple frameworks and
execution environments, delivered as a single Docker Compose project.

*(word count: ~245)*

---

## 4. Keywords

Linked Data pipelines; interoperability; Docker; SHACL; DCAT;
infrastructure as code; provenance.

---

## 5. User Experience Section

**Benefits for the semantics community.** The tool addresses a recurring
pain point in the community: every Linked Data orchestration framework has
its own configuration language, and porting a pipeline from one to another
means rewriting it from scratch. We contribute (i) a small RDF ontology that
lets practitioners describe a pipeline once, framework-agnostically, and
(ii) a working generator that compiles it into a runnable multi-container
stack that transparently spans multiple frameworks. This lowers the barrier
to sharing, reviewing and reproducing pipelines as first-class Linked Data
artefacts.

Beyond interoperability, describing pipelines semantically unlocks a set of
benefits that are hard to obtain from hand-wired configuration:

- **Transformation logic as trackable metadata.** The pipeline description
  is itself an RDF graph, so the transformation steps that produced a
  dataset can be linked to that dataset as first-class PROV-style
  provenance rather than sitting in an opaque YAML file somewhere.
- **Pipelines as validatable artefacts.** Because components carry their
  configuration constraints as SHACL shapes in the catalogue, a pipeline
  composition can be validated *before* deployment — incompatible
  assumptions between components surface at compile time rather than as
  runtime failures.
- **Pipelines as artefacts one can reason over.** With component
  requirements, dependencies and interfaces available as RDF, meaningful
  parts of the implementation logic can be automated: dependency
  resolution, assignment of steps to containers, and the insertion of
  bridge components between containers that would otherwise be
  incompatible.

**Interactive session experience.** Attendees will walk through a live
compile-and-deploy loop on the demonstrator pipeline:

1. Start from the RDF `PipelineDefinition` and the shared component
   `Catalog` in a Jupyter notebook.
2. Modify a component or a channel (e.g. swap the LDIO enrichment step for
   an RDF-Connect one, or move the threshold monitor from RDF-Connect to a
   different container) and watch the generator re-compile the stack — SHACL
   validation report, framework configs and `docker-compose.yml` all
   regenerate.
3. `docker compose up` the freshly generated project and see the water-level
   observations flow end-to-end through LDIO → RDF-Connect →
   semantic.works, ending in an alert email composed inside the triple store.
4. Inspect the self-describing build graph: every emitted file appears as an
   `spdx:File` on the `tcs:PipelineBuild`, and every compiler that ran is
   recorded via `dct:creator`, giving full provenance of the build.

**Expected feedback from the IE session.** In addition to feedback on design, we are interested in market- and practice-facing feedback:
(a) which concrete use cases attendees see for a framework-agnostic,
semantically-described pipeline generator in their own organisations
(data integration, provenance-tracked publishing, cross-team pipeline
sharing, regulated environments where deployments must be auditable,
etc.); (b) which of the semantic benefits — provenance-as-metadata,
compile-time validation, or automation via reasoning over the description
— they find most immediately valuable and why; (c) what would make the
approach more efficient or higher-value in practice (missing target
frameworks, catalogue governance, IDE tooling, cost of adoption).

---

## 6. System Availability Statement

The system is **fully open-source** and publicly accessible in a single
repository: <https://github.com/thcarsten/toolchain-specification>.

The repository is organised into three parts that can each be used
independently of the others:

- **Semantic model** (`semantic model/`) — the `tcs:` toolchain ontology
  describing pipelines, components, catalogues and builds as RDF. Usable
  on its own as a vocabulary for describing pipelines, without adopting the
  generator.
- **Pipeline generator** (`pipeline generator/`) — a self-contained Python
  project that compiles a semantic pipeline description against a
  component catalogue into a deployable Docker Compose project. Runs
  standalone via the `demo.ipynb` notebook shipped in the folder.
- **`rdfine` package** (`pipeline generator/src/rdfine/`) — a small
  independent Python library providing ergonomic graph I/O and
  transformation primitives over `rdflib`. Reusable in any project that
  needs to read, shape and emit RDF graphs; not tied to the generator.

Additional links:

- **Demo video (screen recording of compile + `docker compose up` +
  end-to-end water-level → email alert):** TODO — record and upload an
  unlisted ≤ 1-minute video to YouTube/Vimeo, paste link here before
  submitting.
- **Presentation format at the conference:** on-site laptop demo; attendees
  can also clone the repo and run it themselves during the session.

---

## 7. Maturity Indicator

Working prototype: compiles a deployable Docker Compose stack from a
semantic pipeline description, reusing components across four
orchestration frameworks (LDIO, RDF-Connect, NiFi, semantic.works), with
SHACL-based validation of the pipeline composition built in.

---

## 8. Data Sources Used

The system builds on the following semantic standards and community
artefacts:

- **Ontologies re-used in the `tcs:` toolchain ontology:** DCAT 3
  (`dcat:Catalog`, `dcat:resource`), Dublin Core Terms (`dct:`), PROV-O
  (`prov:hadPlan`), P-Plan (for `PipelineDefinition`), SPDX
  (`spdx:File`, `spdx:Package`) and SHACL (for compile-time validation of
  component wiring and configs).
- **Component catalog sources:** hand-curated Turtle for LDIO, RDF-Connect, Apache NiFi and semantic.works components

---

## Pre-submission checklist

- [ ] Confirm author list, affiliations and contact email(s).
- [ ] Trim abstract to fit EasyChair's abstract field limit if needed.
- [ ] Record ≤ 1-min demo video, upload unlisted, paste link in §6.
- [ ] Double-check that public repos are accessible without login.
- [ ] Submit via EasyChair (`semantics2026`, Practitioners Track) before
      **August 29, 2026, 23:59 AoE**.
- [ ] Register at least one presenting author for on-site attendance
      in Ghent (Sept 15–17, 2026) upon acceptance notification (Sept 3, 2026).

---

## Appendix — Demo video plan

Goal: a frictionless ≤ 1-minute screen recording that shows (1) a
semantic pipeline definition, (2) compilation to a Docker Compose stack,
(3) the generated files, (4) `docker compose up` with live data flowing
through the log. No local data files, no demonstrator pipeline, no
triple store, no email service — just enough to make the point.

### Pipeline used in the video

A minimal cross-framework "public FOAF card → log" pipeline:

```
LDIO HttpInPoller  ─►  LDIO pass-through transformer  ─►  RDFC LogProcessorJs
      (LDIO container)                                       (RDFC container)
```

The cross-container edge between the LDIO transformer and the RDFC
logger is **not** spelled out in the pipeline definition. It is
auto-inserted by `BridgeTransportCompiler` as an `LdioHttpOut` ↔
`RdfcHttpServer` pair. This keeps the definition to three steps and
gives a strong visual reveal in the compiled build.

### Why this pipeline

| Constraint | How it's met |
| --- | --- |
| No local files | Source is a public Turtle URL; sink is stdout. |
| Visible in console / docker logs | `LogProcessorJs` writes every record to stdout; `docker compose logs -f rdfc` shows it live. |
| Clean Docker Compose stack | Two services only: `ldio-workbench` and `rdfc`. |
| Bridge components | Auto-inserted, and made visible when opening the generated `rdfc/pipeline.ttl` and `ldio/application.yml`. |
| Cross-framework | LDIO → RDFC, matching the interoperability claim of the abstract. |
| Stable | Source URL has been live for 20+ years. |

### Source data

**Tim Berners-Lee's public FOAF card:**
`https://www.w3.org/People/Berners-Lee/card.ttl`

Small (~50 triples), stable, pure Turtle, no auth, no content-type
quirks. Poll every 5 seconds → about 10 pulses in a 60-second video.

*(Fallback if the URL misbehaves on the day: any small stable Turtle
file, e.g. `https://www.w3.org/1999/02/22-rdf-syntax-ns` with an
`Accept: text/turtle` header.)*

### Recording script (≈ 60 s)

| Time | On screen | Voice-over (optional) |
| --- | --- | --- |
| 0:00–0:10 | `pipeline_definition_demo.ttl` open in the editor — highlight the three steps and the FOAF URL config. | "A pipeline in three RDF triples." |
| 0:10–0:20 | Jupyter cell: `gen = PipelineGenerator(":DemoPipeline", catalog); build = gen.compile(); FileMaterializer(build).write("./out/demo")`. Show the resulting file tree in the file browser. | "The generator resolves components, assigns them to microservices, and produces a Compose stack." |
| 0:20–0:30 | Open `out/demo/docker-compose.yml` — reveal the two services. Open `out/demo/rdfc/pipeline.ttl` — reveal the auto-inserted `rdfc:HttpServer` step. Open `out/demo/validation/validation-report.ttl` — cursor on `sh:conforms true`, hold ~2 s. | "The HTTP bridge between LDIO and RDFC was not written by hand — the generator inferred it, and SHACL confirms the composition is valid." |
| 0:30–0:60 | Terminal: `cd out/demo && docker compose up -d && docker compose logs -f rdfc`. RDF pulses appear every 5 s. | "And there it is — data flowing across two frameworks, wired up automatically from one RDF description." |

No voice-over is required per the CfP; a plain screen recording with the
same on-screen sequence works too.

### Pre-recording checklist

- [ ] Add `pipeline generator/data/pipeline_definition_demo.ttl` with the
      three-step pipeline pointing at the FOAF URL.
- [ ] Confirm the catalogue exposes an LDIO pass-through transformer
      (or add a trivial identity SPARQL CONSTRUCT step).
- [ ] Confirm `RdfcLogProcessorJs` writes each record to stdout in a
      readable form.
- [ ] Dry-run the compile end-to-end and the `docker compose up` on a
      clean machine (no cached images) so the video reflects a
      first-time experience.
- [ ] Record at 1080p, 5–10 fps is fine for terminal-heavy footage.
- [ ] Upload unlisted to YouTube or Vimeo; paste the URL in §6.
