# Pipeline generator edge cases

One line per edge case. Checked = has a test in `test_edge_cases.py`.
Each entry names which pillar covers it: **shape** (SHACL), **guard**
(compiler-level `raise`), or **compiles** (pillar 2, expected to just work).

## Component / instance reuse
- [x] LDIO Transformer/Output reused twice keeps distinct configs — **compiles**
- [x] LDIO Transformer order follows the readsFrom/writesTo chain, not declaration order — **compiles**
- [x] LDIO `Input` used twice in one pipeline — **shape** (`LdioSingularStepShape`)
- [x] LDIO `Adapter` used twice in one pipeline — **shape** (`LdioSingularStepShape`)
- [x] RDF-Connect component reused twice (e.g. `Sdsify`) keeps distinct configs — **compiles**
- [x] semantic.works component reused twice folds both steps' env vars — **compiles**
- [ ] semantic.works component reused twice, both set the *same* env var key — currently silent last-write-wins, no guard yet

## Channels / graph shape
- [x] A step reads from the same channel it writes to (self-loop) — **shape** (`AcyclicGraphShape`)
- [x] Multi-step cycle in the channel graph (A→B→A) — **shape** (`AcyclicGraphShape`)
- [x] Fan-out: two steps reading the same channel — both survive, order between them is non-deterministic (not lossy) — **compiles**

## Config cardinality
- [x] A step with two `p-plan:hasInputVar` configs — **shape** (`InstancePipelineComponentShape` maxCount 1)

## Naming / identity collisions
- [x] Blank-node auto-naming collides with an author-declared resource name — **guard** (`PipelineExtractor.name_blind_nodes`)
- [x] Two compilers (or a re-run) target the same output file path — **guard** (`attach_file`)
- [x] Two `tcs:DockerComposeConfig`s declare the same service/volume/network name — **guard** (`DockerComposeCompiler`)
- [x] Two components require conflicting versions of the same npm/pip package — **guard** (`RdfcDockerFileCompiler`)
- [x] Pipeline name is a prefix of another IRI in the same segment (e.g. `demo:Test` / `demo:TestArchive`) — **guard** (word-boundary regex in `RdfcConfigCompiler`)

## Graph-level robustness
- [x] Typo'd / nonexistent `pipeline_id` — fails loudly via `traverse()`'s `NameError`
- [x] Pipeline definition with zero steps — compiles to an empty build, no crash
- [x] Pipeline with steps but no deployable microservice — compiles cleanly, zero containers
- [x] RDF-Connect segment with zero channel wiring — compiles cleanly (used to crash `GraphReader.construct()`)
- [x] Catalog declares an `rdfc:Runner` no processor in this pipeline actually uses — compiles cleanly

## Cross-pipeline / multi-tenancy
- [ ] Two `tcs:PipelineDefinition`s share a catalog component, each pre-declaring its own container — **known unsupported**, no guard (container pre-declaration itself is unsupported; see AGENTS.md 2026-08-04 entry)
- [x] Same `tcs:InstancePipelineComponent` URI declared `p-plan:isStepOfPlan` of two different plans — **shape** (`InstancePipelineComponentShape` maxCount 1)

## Not yet investigated (speculative, for future sessions)
- [x] Cyclic `dct:requires` between two catalog components (A requires B requires A) — **compiles** (the "microservice" side of the requirement chain naturally breaks the cycle; visited-set guard is a backstop)
- [x] A `tcs:InstancePipelineComponent` whose `prov:specializationOf` target doesn't exist in the catalog at all — **shape** (`SpecializedComponentIsCatalogedShape`; the inference rule correctly still types it `tcs:PipelineComponent`, so this checks `dcat:resource` membership directly instead of relying on the misleading "not deployable" message)
- [ ] `tcs:literal` config body with inconsistent indentation defeating `textwrap.dedent`
- [ ] RDF-Connect processor declaring `dct:requires` on two different `rdfc:Runner`s simultaneously
- [ ] Very large pipeline (100+ steps) — performance/scaling smoke test, not correctness

## Application-profile shape coverage — Section 1 (structural, framework-agnostic)

Audit of every `sh:NodeShape` in `catalog-application-profile-shapes.ttl`,
Section 1 (the generic profile — as opposed to Section 2's RDF-Connect/LDIO/
semantic.works-specific shapes). One `sh:class` check remains genuinely
unreachable: `PipelineComponentShape`'s `tcs:config` class check, since
`tcs:config` is a toolchain-owned predicate and inference_rules.yaml still
(correctly) entails `tcs:Config` from any use of it — using the predicate at
all *is* the declaration of intent, so there's no "wrong domain" risk to
guard against. `dct:requires` (Dublin Core, borrowed vocabulary) and
`prov:specializationOf` (PROV-O, borrowed vocabulary) used to have the same
unconditional entailment and were **descoped** in the 2026-08-04
inference-scoping pass — they no longer auto-type their targets, so both
class checks are now genuinely reachable and tested below.

- [x] `PipelineComponentShape` — component whose `dct:requires` chain never reaches a `tcs:DockerComposeConfig` — **shape** (deployability check)
- [x] `PipelineComponentShape` — two `tcs:DefaultConfig`s of the same `tcs:Config` subtype on one component — **shape**
- [x] `PipelineComponentShape` — `dct:requires` pointing at something neither a `tcs:PipelineComponent` nor `spdx:Package` — **shape** (reachable since the 2026-08-04 inference-scoping pass)
- [x] `InstancePipelineComponentShape` — step with no `prov:specializationOf` — **shape**
- [x] `InstancePipelineComponentShape` — step with two `prov:specializationOf` targets — **shape**
- [x] `InstancePipelineComponentShape` — `prov:specializationOf` pointing at something that exists but isn't a `tcs:PipelineComponent` — **shape** (reachable since the 2026-08-04 inference-scoping pass; distinct from the dangling-reference case below)
- [x] `InstancePipelineComponentShape` — step with no `p-plan:isStepOfPlan` — **shape**
- [x] `PipelineDefinitionShape` — plan with zero steps — **shape**
- [x] `ConfigShape` — `tcs:Config` with neither `tcs:embedded` nor `tcs:literal` — **shape** (`sh:xone`, default pySHACL message)
- [x] `ConfigShape` — `tcs:Config` with both `tcs:embedded` and `tcs:literal` — **shape** (`sh:xone`)
- [x] `ConfigShape` — `tcs:literal` body with no `dct:format` — **shape**
- [x] `CatalogShape` — `dcat:resource` pointing at something never defined as a `tcs:PipelineComponent` — **shape**
- [x] `PipelineBuildShape` — build with no `prov:hadPlan` — **shape**
- [x] `PipelineBuildShape` — `dct:hasPart` pointing at a non-`tcs:DockerContainer` — **shape**
- [x] `PipelineBuildShape` — `tcs:compiledFile` pointing at a non-`spdx:File` — **shape**
- [x] `DockerContainerShape` — container with no `tcs:instantiates` — **shape**
- [x] `DockerContainerShape` — `tcs:instantiates` pointing at a non-`tcs:PipelineComponent` — **shape**
- [x] `DockerContainerShape` — `tcs:runs` pointing at a non-`tcs:InstancePipelineComponent` — **shape**
- [x] `SpdxPackageShape` — package with no `spdx:name` — **shape**
- [x] `SpdxPackageShape` — package with no `spdx:suppliedBy` — **shape**
- [ ] `PipelineComponentShape` — `tcs:config` sh:class — **unreachable under current inference** (see note above, this one is by design), not tested

## Application-profile shape coverage — Section 2 (framework-specific)

Audit of every `sh:NodeShape` in Section 2 of
`catalog-application-profile-shapes.ttl` (RDF-Connect, LDIO, semantic.works).
`LdioSingularStepShape` and `AcyclicGraphShape` are covered above (see the
earlier "Component / instance reuse" / "Channels / graph shape" sections).

- [x] `RdfcProcessorShape` — processor requiring an `rdfc:Runner` with no `owl:imports` — **shape**
- [x] `RdfcRunnerShape` — a component typed `rdfc:Runner` with no `dct:requires rdfc:Orchestrator` — **shape**
- [x] `RdfcOrchestratorConfigShape` — `rdfc:Orchestrator` missing its `tcs:DockerComposeConfig` — **shape** (isolated shapes-only graph, since the real catalog always satisfies this)
- [x] `RdfcOrchestratorConfigShape` — `rdfc:Orchestrator` missing its `tcs:DockerImageConfig` — **shape** (isolated shapes-only graph)
- [x] `RdfcPackageManagerShape` — an `spdx:Package` in the RDF-Connect dependency closure with `spdx:suppliedBy` outside `{:pip, :npm}` — **shape**
- [x] `LdioComponentTypeShape` — LDIO component with no `ldio:type` — **shape**
- [x] `LdioComponentTypeShape` — LDIO component with an invalid `ldio:type` enum value — **shape**
- [x] `LdioComponentTypeShape` — LDIO component with no `rdfs:label` — **shape**
- [x] `LdioStepSerialityShape` — LDIO step reading from two channels — **shape**
- [x] `LdioStepSerialityShape` — LDIO step writing to two channels — **shape**
- [x] `LdioStepOrderingShape` — an `Output` step followed by another LDIO step — **shape**
- [x] `LdioStepOrderingShape` — an `Input` step preceded by another LDIO step — **shape**
- [x] `SwComponentDockerConfigShape` — a `sw:` component with no direct `tcs:DockerComposeConfig` — **shape**
- [x] `SwStepEnvValueShape` — a `sw:` step's embedded config with a non-literal (IRI) value — **shape**

`SwStepInputVarShape` was removed from the application profile (2026-08-05):
confirmed entirely subsumed by the generic `InstancePipelineComponentShape`
`hasInputVar` `maxCount 1` check, which already applies to every step
regardless of framework. Its dedicated test
(`test_sw_step_input_var_cardinality_triggers_shape`) was removed too —
coverage now comes from `test_two_configs_on_one_step_triggers_shape`.

