# toolchain-specification — repo context

> **How to use this file.** It is the entry point for any session opened at
> this repo root, and it is auto-loaded for sessions opened in a subfolder
> too (CLAUDE.md loads from the cwd upward). It covers what the four
> subprojects are and the rules that hold across all of them.
>
> Per-subproject detail lives one level down — see
> [`pipeline generator/CLAUDE.md`](pipeline%20generator/CLAUDE.md), which is
> loaded on demand when you touch files there.
>
> Deep internals (ontology cheat-sheet, per-module compiler reference,
> vocabulary, roadmap) live in the `dishacled-context` skill
> (`.claude/skills/dishacled-context/SKILL.md`) — loaded on demand, not on
> every session start.

---

## 1. What is this?

**DiSHACLed** is a research project on declaratively-specified, semantically
annotated data pipelines. This repo, `toolchain-specification`, holds the
specification side: the semantic model, the generator that consumes it, the
validation story, and the survey that motivates the work.

Its sibling repo `demonstrator/` (one level up, outside this repo) holds the
hand-built reference pipeline that the generator's output is validated
against. See [`../CLAUDE.md`](../CLAUDE.md) for how the two fit together.

## 2. Subprojects

| Folder | What it is |
| --- | --- |
| [`pipeline generator/`](pipeline%20generator/) | The source-to-source compiler: reads an RDF pipeline definition + component catalog, emits a runnable project folder (docker-compose + per-framework configs) for RDF-Connect, LDIO, Apache NiFi and semantic.works. The only folder with code. Own [CLAUDE.md](pipeline%20generator/CLAUDE.md) and [README](pipeline%20generator/README.md). |
| [`semantic model/`](semantic%20model/) | The `tcs:` toolchain ontology — prose reference plus diagrams, no code. The generator is driven entirely by these terms, so the two must stay in sync; the `semantic-model-sync` skill covers that. |
| [`test suite/`](test%20suite/) | How pipeline definitions are to be validated (SHACL role vocabulary, validation order) *before* the generator runs. Design prose; the suite itself is not built yet. |
| [`survey/`](survey/) | The state-of-the-art survey report (PDF + README). Background, not wired to anything. |
| [`plan/`](plan/) | Implementation plans — the slice-by-slice design docs written before the work is done. See §5. |

Inside [`pipeline generator/`](pipeline%20generator/), `out/` is scratch —
gitignored, safe to delete, never committed. The one tracked build is
`reference/dishacled-full/`, refreshed only by promoting a run on purpose
(see the generator README).

## 3. Environment

The project's virtual environment already exists and is shared across the
whole DiSHACLed project — **never** run `pip install` or otherwise try to
(re)create it from scratch. Always invoke Python via the existing conda
environment:

```
~\anaconda3\envs\pipeline_generator\python.exe
```

e.g. `~\anaconda3\envs\pipeline_generator\python.exe -m pytest tests/ -q`
from within `pipeline generator/`, instead of a bare `python`/`pytest`.
Running tests is gated: see the `run-pipeline-generator-tests` skill, and
note the `PreToolUse` hook in [.claude/settings.json](.claude/settings.json)
that enforces it.

## 4. Skills

Loaded on demand, not at session start:

- **`dishacled-context`** — deep-dive reference: `tcs:` ontology, `rdfine`,
  the compilers package, SHACL validation, vocabulary cheat-sheet, and the
  dated roadmap / open-items list. Load this rather than re-deriving
  internals from source, and before planning what to work on next.
- **`semantic-model-sync`** — read or edit the `tcs:` ontology in
  [`semantic model/`](semantic%20model/) when a generator change implies an
  ontology change, or when the two may have drifted.
- **`run-pipeline-generator-tests`** — run the generator's full pytest
  suite. Requires explicit user permission; never invoke automatically.

## 5. Working on this repo

### House rules

These hold for every session, and override any default inclination to be
thorough.

- **Keep diffs minimal.** No repo-wide reformatting, renaming or drive-by
  refactoring. Touch what the task needs and nothing else.
- **Ask before fixing what you weren't asked to fix.** If you spot a bug,
  say what it is and how you'd fix it, then wait. This applies even when
  the fix looks trivial and even when it's adjacent to the task — the
  exception is a change without which the requested work cannot land, and
  that one gets called out explicitly in the reply.
- **Separate the side quests.** Before editing, ask which changes the task
  actually requires and which are improvements nobody asked for. Make the
  minimal set; offer the rest as suggestions.
- **Concept first, detail on request.** Lead with the conceptual
  explanation in plain language. Accurate-but-unreadable is a failure.
  Detail, file-by-file accounting and caveats come after, or when asked.
- **When in doubt, prefer clarity, readability, maintainability and
  robustness** over cleverness or brevity.

### Where plans go

Every implementation plan lives in [`plan/`](plan/) at the repo root, one
Markdown file per plan, named after the change it describes
(e.g. `pipeline-segments-plan.md`). This holds for plans written in plan
mode as well as ones drafted ad hoc — do **not** put them under a
subproject's `docs/`. Companion material that is *not* the plan itself
(decision logs, rejected alternatives) stays with its subproject.

Links inside a plan are relative to `plan/`, so a path into the generator
reads `../pipeline%20generator/...`.

### Starting a session

1. Open this folder for anything inside the repo. Open the workspace root
   one level up only when you need `demonstrator/` visible at the same time.
2. State the concrete goal (new compiler, catalog/SHACL authoring, bugfix,
   ontology change, test-suite work, refactor).
3. Load `dishacled-context` for internals rather than re-deriving them.

### Ending a session

1. Update the `dishacled-context` skill's status/roadmap (check off / delete
   done items; add newly-discovered ones) if you touched generator
   internals or the ontology.
2. Commit the context files (this one, `pipeline generator/CLAUDE.md`, the
   skills) alongside the code changes so their history mirrors the
   project's.
