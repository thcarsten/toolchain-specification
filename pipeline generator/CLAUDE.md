# Pipeline generator — project context

> **How to use this file.** It is scoped to the **pipeline generator**
> codebase only, and is loaded on demand — when a session opened at the repo
> root touches files in this folder — rather than at session start.
>
> The always-on entry point is one level up:
> [`../CLAUDE.md`](../CLAUDE.md) covers the repo as a whole (this folder plus
> the semantic model, the test suite and the survey). Above that,
> [`../../CLAUDE.md`](../../CLAUDE.md) at the workspace root covers this repo
> and the hand-built `demonstrator/` together.
>
> Deep internals (ontology cheat-sheet, per-module compiler reference,
> vocabulary, roadmap) live in the `dishacled-context` skill
> (`../.claude/skills/dishacled-context/SKILL.md`) — loaded on demand, not on
> every session start. Companion **private notes** live in the user-scope
> memory for this project.

---

## 1. What is this?

The pipeline generator is a source-to-source compiler: it reads a
semantically-annotated **pipeline definition** (RDF, following the
*toolchain* `tcs:` ontology) plus a **component catalog**, and emits a
runnable project folder — a `docker-compose.yml` plus the framework-specific
config files for every component in the pipeline. It currently targets four
frameworks: **RDF-Connect**, **LDIO**, **Apache NiFi**, and
**semantic.works**.

It exists to make the DiSHACLed demonstrator's hand-built pipeline
reproducible from a declarative description instead of hand-wiring
docker-compose + framework configs each time; it is the sibling deliverable
to the `DiSHACLed/demonstrator` repo, which the generator's output is
validated against (byte-identical reproduction of the demonstrator's
Tier-1/2 semantic.works configs, see [README.md](README.md) §3).

Full introduction, installation, workflow, and architecture:
[README.md](README.md).

## 2. Layout

| Path | Content |
| --- | --- |
| `src/rdfine/` | Ergonomic RDF I/O + transformation primitives on top of `rdflib`/`pyld` (`GraphReader`, `GraphDict`, `PrefixStore`). Own [README](src/rdfine/README.md) and pytest suite. |
| `src/compilers/` | The generator itself: `Compiler` ABC, `CompilationRunner` fixpoint driver, and per-framework compiler subpackages (`core/`, `ldio/`, `rdfc/`, `sw/`). See [README.md](README.md) §4 for the module table. |
| `src/rdfc_catalog_harvest/` | Pre-compile step that generates the RDF-Connect section of the component catalog from the RDF-Connect packages' own published definitions (README §4.10). |
| `data/` | `catalog/` (framework catalogs + SHACL shapes + committed `rdfc_harvest/` snapshot), `pipelines/` (pipeline definitions used by the demos), `inference_rules/` (RDFS/channel entailment rules loaded before compilation). |
| `tests/` | Pytest regression suite for the generator (compiler edge cases — see [`tests/EDGE_CASES.md`](tests/EDGE_CASES.md)). `src/rdfine/tests/` is a second, independently-runnable pytest scope for `rdfine` alone. |
| `docs/` | [`architecture-deck.md`](docs/architecture-deck.md) slide deck, pipeline-segments design docs. |
| `resources/rdfc-docker/` | Local RDF-Connect processor packages (e.g. `rdfc_http_out`) used by the harvested catalog. |
| `src/demo.ipynb`, `src/demo_fietsstallingen.ipynb` | Demo notebooks driving the generator end-to-end. |
| `out/` | Generated project output (git-ignored working area, not source). |

## 3. Environment

This project's virtual environment already exists and is shared across the
whole DiSHACLed project — **never** run `pip install` or otherwise try to
(re)create it from scratch. Always invoke Python via the existing conda
environment:

```
~\anaconda3\envs\pipeline_generator\python.exe
```

e.g. `~\anaconda3\envs\pipeline_generator\python.exe -m pytest tests/ -q`
instead of a bare `python`/`pytest`. Full dependency list and manual setup
notes (for reference only, not for routine use): README §2. Test-running
procedure (including the `run-pipeline-generator-tests` skill) is also
covered by the `dishacled-context` skill.

## 4. Architecture at a glance

```
Pipeline Definition (RDF) ─┐
                           ├─►  CompilationRunner  ─►  build graph  ─►  FileMaterializer  ─►  project folder
Component Catalog (RDF) ───┘    (fixpoint loop over                     (walks spdx:File   (docker-compose.yml +
                                auto-registered Compilers)              nodes)             framework configs)
```

RDF is the intermediate representation throughout: the catalog + a pipeline
definition go into an `rdflib.Graph`, a fixpoint loop of small `Compiler`
subclasses progressively shapes it (see README §3 for the request/fixpoint/
finalize/detach phases), and generated files are attached to the build as
first-class `spdx:File` nodes carrying their body in `tcs:literal`. The
filesystem is only touched at the very end, by `FileMaterializer`. Full
module-by-module reference: README §4; slide deck:
[`docs/architecture-deck.md`](docs/architecture-deck.md).

Detailed, dated status, roadmap and open items: `dishacled-context` skill,
"Roadmap & open items → Pipeline generator" section.

## 5. Working on this repo

### Starting a session

1. Open the repo root (`toolchain-specification/`) — that is where the
   Claude Code config, the skills and the test-guard hook live, so opening
   it is what makes them available. Open the workspace root one level above
   only when you need `demonstrator/` visible at the same time.
2. State the concrete goal (new compiler, catalog/SHACL authoring, bugfix,
   test-suite integration, refactor).
3. For deep internals (ontology, per-compiler contracts, vocabulary), load
   the `dishacled-context` skill rather than re-deriving from source.

### During the session

- Every concrete `Compiler` subclasses `Compiler` (`compiler_abc.py`),
  implements `compile(self) -> Graph`, and overrides `applies_to` — see
  README §4.1 for the module table and the `dishacled-context` skill for
  the method-naming/splitting convention.
- Run tests via `~\anaconda3\envs\pipeline_generator\python.exe -m pytest
  tests/ -q` (and `src/rdfine`'s own suite) before considering a change
  done — per the `run-pipeline-generator-tests` skill, only with explicit
  user permission.
- Validate a pipeline definition with `PipelineValidator` before generating
  with `PipelineGenerator` (README §3 shows both).

### Ending a session

1. Update the `dishacled-context` skill's status/roadmap (check off /
   delete done items; add newly-discovered ones) if you touched generator
   internals.
2. Commit this file and the skill alongside the code changes so their
   history mirrors the project's.
