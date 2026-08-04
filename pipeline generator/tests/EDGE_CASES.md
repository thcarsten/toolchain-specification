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

