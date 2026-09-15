---
name: semantic-model-sync
description: 'Read or edit the toolchain (tcs:) ontology in the sibling "semantic model" folder to keep it in sync with the pipeline generator. Use when a generator change implies an ontology change (new/changed tcs: class, property, or shape), when a question is about what a tcs: term means or where it is defined, or when the generator and the ontology may have drifted apart.'
---

# Semantic model sync

## Why this exists

The pipeline generator (`compilers` package) is driven entirely by the
*toolchain* (`tcs:`) ontology defined in the sibling folder
`../semantic model/` (same git repo, one level up from this project root).
That folder is outside this project's own directory, so it is only
reachable because `.claude/settings.json` grants standing access to it via
`permissions.additionalDirectories`. Without this skill, it's easy to
change compiler behavior (a new config subclass, a new shape role, a new
compiler-generated triple pattern) without updating the ontology doc that
describes it — the two then silently drift apart.

## Layout of `../semantic model/`

- [`README.md`](../semantic%20model/README.md) — the ontology reference:
  core classes (`tcs:Catalog`, `tcs:PipelineDefinition`,
  `tcs:PipelineGenerator`, `tcs:PipelineBuild`), supporting classes
  (`tcs:PipelineComponent`, `tcs:InstancePipelineComponent`, `tcs:Config`
  and subclasses, `tcs:DockerContainer`, `tcs:Compiler`, `spdx:File`,
  `sh:NodeShape`), and how they build on p-plan, dcat, prov, spdx.
- `diagrams/` — `diagrams.drawio` source plus exported SVGs
  (`toolchain_model.svg`, `pipeline_generator.svg`,
  `pipeline_definition.svg`, `component_catalog.svg`).

## When to use

- **Before** adding a new `tcs:` class, property, config subclass, or shape
  role in the generator (catalog `.ttl` files, `compilers/` source) —
  check whether it already exists in the ontology under a different name,
  and whether the README/diagrams need a corresponding update.
- **After** landing a generator change that introduces new semantic-model
  vocabulary (e.g. the pipeline-segments work adding `tcs:segment`,
  `tcs:CompilationRequest`, `tcs:runPhase`) — update
  `semantic model/README.md` (and the relevant diagram, noting in text
  where a diagram is stale if you can't regenerate the SVG directly) so
  the ontology doc doesn't lag behind what the compilers actually emit.
- **When asked** what a `tcs:` term means, or where a class/property is
  formally defined — read the ontology doc rather than inferring the
  definition from generator source alone.

## How to use

Read and Edit both work directly on paths under `../semantic model/`
(reads need no extra permission prompt; edits still follow the normal
edit-confirmation flow). Cross-check terminology against the
`dishacled-context` skill's ontology cheat-sheet, which is a summary of
this same README — if you edit the ontology doc, check whether that
cheat-sheet also needs updating to stay consistent.

Treat `diagrams.drawio` / the SVGs as best-effort: you can describe what a
diagram *should* show, but regenerating the actual SVG/drawio output is
outside a text-editing workflow — flag it to the user instead of trying to
hand-edit the XML/SVG.
