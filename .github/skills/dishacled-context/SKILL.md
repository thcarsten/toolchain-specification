---
name: dishacled-context
description: 'Deep-dive reference for the DiSHACLed project: toolchain/pipeline-generator internals (tcs: ontology, rdfine, compilers package, SHACL validation), the vocabulary cheat-sheet, and the roadmap/open-items list. Use when working on pipeline generator internals, semantic model / tcs: vocabulary questions, or planning what to work on next. Not needed for general orientation - see AGENTS.md for that.'
---

# DiSHACLed deep-dive reference

## 5. Toolchain specification & pipeline generator internals

High-level map so agents don't have to re-read every generator doc from scratch.
Full references are linked at the end of each subsection.

### Ontology (`tcs:`)

Four **core classes**:

- `tcs:Catalog` (subclass of `dcat:Catalog`) — the library of reusable pipeline
  components.
- `tcs:PipelineDefinition` (subclass of `p-plan:Plan`) — a plan: which
  components make part of a pipeline, in what order, with what configs.
- `tcs:PipelineGenerator` (subclass of `prov:SoftwareAgent`) — compiles a
  Definition + Catalog into a Build.
- `tcs:PipelineBuild` (subclass of `prov:SoftwareAgent` + `spdx:Build`) — the
  deployable output of a compilation; consists of one or more
  `tcs:DockerContainer`s.

**Supporting classes:** `tcs:PipelineComponent`, `tcs:InstancePipelineComponent`
(subclass of `p-plan:Step`), `tcs:Config` (with subclasses `DockerComposeConfig`,
`DockerImageConfig`, `PipelineConfig`, `DefaultConfig`), `tcs:DockerContainer`,
`tcs:Compiler`, `spdx:File`, `sh:NodeShape`. Shapes are attached to their
subject (component or compiler) via `dcat:qualifiedRelation` + `dcat:hadRole` —
the role string encodes *when* validation should fire. Semantic-model reference:
[`semantic model/README.md`](toolchain-specification/semantic%20model/README.md).

### `rdfine` package

Ergonomic wrapper over `rdflib`/`pyld`. Location:
[`pipeline generator/src/rdfine/`](toolchain-specification/pipeline%20generator/src/rdfine/).

- **`GraphReader`** — immutable functional view over an `rdflib.Graph`.
  Methods: `filter`, `select`, `construct`, `ask`, `sparql`, `infer`,
  `traverse`, `add`, `remove`, `rename`, `check_exists`, `serialize`,
  `validate`. Every transformation returns a new reader. Cached triples
  DataFrame on `.df`. `validate(**pyshacl_kwargs) -> GraphReader` wraps
  `pyshacl.validate` and returns the SHACL report as a reader; the graph
  is used as both data and shapes source, so shapes are expected to sit
  next to the components they constrain.
- **`GraphDict`** — JSON-LD-shaped dict view. Methods: `frame`, `get`/`set`,
  `find`, `serialize`. `.graph` is always safe to call (unknown prefixes get an
  on-the-fly `na_` placeholder without mutating the original).
- **`PrefixStore`** — namespace registry with `compact`/`expand`/`drop`,
  `bind_to_namespace(graph)`, `include_in_query(sparql)`. Rejects prefix/URL
  collisions via `PrefixConflictError`.
- **Utilities:** `load_yaml`, `drop_empty`, `parse_config`.

Full reference:
[`src/rdfine/README.md`](toolchain-specification/pipeline%20generator/src/rdfine/README.md).

### `compilers` package — the generator

Location:
[`pipeline generator/src/compilers/`](toolchain-specification/pipeline%20generator/src/compilers/).
Every concrete compiler subclasses `Compiler` (in `base.py`) and must:

- Implement `compile(self) -> Graph` — reads from `self.graph_reader`, returns
  the enriched build graph.
- Override `applies_to(cls, graph_reader) -> bool` (classmethod, default
  `False`) — declares the graph-state pattern that must be present for the
  compiler to run.
- Optionally override `compiler_iri(cls)` — the IRI written as `dct:creator`
  for provenance (defaults to `tcs:<ClassName>`).

**Method naming & splitting convention.** `compile()` must be a
thin, ordered list of calls to the compiler's own *public* methods — no
inline logic of its own. Each public method is one traceable step/concern
of the compile process (mirrors `ValidationReportCompiler`'s 8-method
shape) and stays public specifically so it's inspectable/steppable after a
compile. Private (`_`-prefixed) methods are helpers subsumed by exactly
one public step and are never called directly from `compile()` — only from
the one public method that owns them. Every method name — public or
private — must contain a verb true to what it does (`lookup_`, `fold_in_`,
`normalize_`, `validate_`, `gather_`, `fill_`, `list_`, `generate_`,
`attach_`, `describe_`, `extract_`, `seed_`, …); a noun-only name
(`_container_service_names`) must be renamed to include one
(`_lookup_container_service_names`). Full write-up + audit status:
`/memories/repo/compiler-conventions.md`.

`Compiler.__init_subclass__` auto-appends non-abstract subclasses to
`Compiler._registry`. `PipelineGenerator` runs one bootstrap step
(`PipelineExtractor`, the only compiler whose ctor needs the `pipeline_id`) and
then a **fixpoint loop**: every iteration it scans the registry, keeps
not-yet-run compilers whose `applies_to` returns True on the current build, and
runs them. Execution order **emerges** from triggers becoming satisfied as the
graph grows. Every compiler that ran is recorded on the build as `dct:creator`.
When a scan finds nothing eligible, `PipelineGenerator` flips
`<build> tcs:isFinishing true` and does one more scan so finalization-style
compilers (currently only `DockerComposeCompiler`) can trigger; if that pass is
also empty, the loop terminates and the flag is stripped before `compile()`
returns.

Concrete compilers (as of 2026-07-24):

| Compiler | Trigger (`applies_to`) | Emits |
| --- | --- | --- |
| `PipelineExtractor` | always (bootstrap) | seeds `tcs:PipelineBuild` linked to the definition via `prov:hadPlan`; extracts the pipeline's triples |
| `PipelineAssembler` | exactly one `tcs:PipelineDefinition` with ≥1 `:hasStep` | `tcs:DockerContainer`, `dct:hasPart`, `tcs:instantiates`, `tcs:runs` |
| `SemanticWorksEnvVarCompiler` | any `sw:` component + a `tcs:DockerContainer` present | folds step configs into env vars on the microservice's docker config |
| `LdioConfigCompiler` | a container instantiates `ldio:LinkedDataInteractionsOrchestrator` | `spdx:File` `ldio/config.yml` |
| `RdfcConfigCompiler` | a container instantiates `rdfc:Orchestrator` | `spdx:File` `rdfc/pipeline.ttl`. Step→channel wiring is read verbatim from each step's `tcs:embedded` config (author responsibility); the compiler only adds the `rdfc:Reader, rdfc:Writer` type declaration for every referenced channel. |
| `RdfcDockerFileCompiler` | container instantiates `rdfc:Orchestrator` + `tcs:DockerImageConfig` present | `spdx:File`s `rdfc/Dockerfile`, `rdfc/pyproject.toml`, `rdfc/package.json` |
| `VirtuosoCompiler` | a container instantiates `sw:triple-store` | `spdx:File` `semantic-works/config/virtuoso/virtuoso.ini` |
| `MuClResourcesCompiler` | a container instantiates `sw:mu-cl-resources` | `spdx:File`s `semantic-works/config/resources/{domain.json,domain.lisp,repository.lisp}` |
| `MuDispatcherCompiler` | a container instantiates `sw:mu-dispatcher` | `spdx:File` `semantic-works/config/dispatcher/dispatcher.ex` |
| `MuDeltaNotifierCompiler` | a container instantiates `sw:mu-delta-notifier` | `spdx:File` `semantic-works/config/delta/rules.js` |
| `MuAuthorizationCompiler` | a container instantiates `sw:mu-authorization` | `spdx:File` `semantic-works/config/authorization/config.lisp` |
| `ErrorAlertCompiler` | a container instantiates `sw:loket-error-alert-service` | `spdx:File`s `semantic-works/config/error-alert/{config.json,error.hbs}` |
| `DockerComposeCompiler` | `<build> tcs:isFinishing true` + any `tcs:DockerComposeConfig` present | `spdx:File` `./docker-compose.yml` |

File-producing compilers end with
`attach_file(self.output_reader, filename=, filepath=, content=)` from
`compilers.utils`, which adds an `spdx:File` node linked to the build via
`tcs:compiledFile` with body in `tcs:literal`. `ProjectBuilder` (not a
`Compiler` — it is the filesystem boundary) walks those nodes and writes them
to disk. A path-traversal guard rejects any `tcs:filepath` that would escape
the target directory.

**Adding a new framework compiler** — drop a file in `compilers/`, subclass
`Compiler`, implement `compile()`, override `applies_to()`, import the module
from `compilers/__init__.py`. `PipelineGenerator` needs no changes. See §6.3
of [`pipeline generator/README.md`](toolchain-specification/pipeline%20generator/README.md).

### SHACL validation — primitive done, test-suite runner planned

`GraphReader.validate(**pyshacl_kwargs) -> GraphReader` is implemented in
[`src/rdfine/graph_reader.py`](toolchain-specification/pipeline%20generator/src/rdfine/graph_reader.py):
wraps `pyshacl.validate`, returns the SHACL report as a `GraphReader`
(conformance is `?r sh:conforms ?bool` inside the returned graph, preserving
rdfine's graph-in / graph-out contract). Data graph and shapes graph are the
same (`self.graph`) — shapes are expected to live next to the components
they constrain. `pyshacl` is a hard dependency of `rdfine`.

The **application profile** lives at
[`data/catalog-application-profile-shapes.ttl`](toolchain-specification/pipeline%20generator/data/catalog-application-profile-shapes.ttl)
(renamed from `tcs_shapes.ttl`), structured in two sections:

1. **Generic profile** — vocabulary-level constraints on the `tcs:` model
   valid for every catalog. Includes deployability of every
   `tcs:PipelineComponent` (transitively reaches a `tcs:DockerComposeConfig`
   via `dct:requires*`), at-most-one `tcs:DefaultConfig` per Config subtype,
   cardinality on `p-plan:isStepOfPlan` / `prov:specializationOf`, mandatory
   `tcs:embedded` xor `tcs:literal` on every `tcs:Config`, `dct:format`
   required for `tcs:literal` bodies, etc.
2. **Compiler-specific constraints** — rules that only apply to components
   targeted at a specific framework. RDF-Connect processors must declare
   `owl:imports`; LDIO components must declare `ldio:type` in
   {`Input`, `Adapter`, `Transformer`, `Output`} and step chains must be
   strictly serial with `Input` initial / `Output` terminal within the LDIO
   segment; semantic.works components must directly own a
   `tcs:DockerComposeConfig`, steps have at most one `p-plan:hasInputVar`,
   and env values must be literals.

Component-scoped shapes (e.g. `:SparqlIngestShape`,
`:SparqlIngestConfigShape`) live inline in the framework-specific catalog
file next to the component they constrain (e.g.
[`data/catalog-rdfc.ttl`](toolchain-specification/pipeline%20generator/data/catalog-rdfc.ttl)
for RDF-Connect), attached via `dcat:qualifiedRelation` +
`dcat:hadRole "configShape"`. Path-based targets are
expressed as SHACL-AF `sh:SPARQLTarget` (Core `sh:targetSubjectsOf` cannot
filter by object or navigate property paths).

Still to build (see
[`test suite/README.md`](test%20suite/README.md)):

1. **Test-suite runner** — the `validate_pipeline_definition(catalog_graph,
   pipeline_id)` entry point plus `ShaclValidationError`. Runs
   pre-`PipelineGenerator`, discovers shapes attached via
   `dcat:qualifiedRelation`, and orchestrates pySHACL + shape-matching
   validation.
2. **Native Python shape-matching library** — a colleague is writing this
   from scratch (decided 2026-08-18), superseding the earlier plan to bridge
   to the external, TypeScript
   [DiSHACLed/query-shape-matching-algorithm](https://github.com/DiSHACLed/query-shape-matching-algorithm)
   over HTTP (`qsm-service`). Once it exists, `match_shapes()` calls into it
   in-process — no bridge, no service.

Simplified role vocabulary: `"configShape"` (component, pySHACL, also drives
the SHACL UI), `"inputShape"` / `"outputShape"` (component *or* instance,
shape-matching), or no role at all (pySHACL, free-form). The old
`"requiredShape"` / `"buildShape"` roles have been dropped along with the
per-compiler gate.

Full plan with phases, verification steps, and open questions:
[`test suite/README.md`](test%20suite/README.md).

## 6. Vocabulary cheat-sheet

### Frameworks / demonstrator side

- **LDIO** — Linked Data Interactions Orchestrator (Spring Boot). Ships with
  `Ldio:HttpInPoller`, `Ldio:HttpIn`, `Ldio:RdfAdapter`, `Ldio:JsonToLdAdapter`,
  `Ldio:SparqlConstructTransformer`, `Ldio:HttpOut`, …
- **RDF-Connect (RDFC)** — streaming pipeline framework. Runners run
  language-specific processors. We use `NodeRunner` here.
- **SDS** — Semantic Data Stream. RDFC's internal envelope format for streaming
  triples between processors, produced by `rdfc:Sdsify`.
- **SSN/SOSA** — W3C ontologies for sensor observations
  (`sosa:Observation`, `sosa:hasFeatureOfInterest`, `sosa:hasResult`,
  `sosa:hasSimpleResult`, `sosa:madeBySensor`, `sosa:observedProperty`).
- **QUDT** — quantity/unit ontology. We use `qudt:QuantityValue`,
  `qudt:numericValue`, `qudt:unit`, and `unit:CentiM` (canonical mapping of
  UN/CEFACT code `CMT`).
- **oslc:Error** — Open Services for Lifecycle Collaboration error resource;
  the shape ThresholdMonitor emits on violation.
- **mu-semtech / semantic.works** — microservice ecosystem where services
  communicate by publishing SPARQL updates to a shared store. `mu-identifier`
  is the front-door SPARQL gateway; `mu-delta-notifier` is the pub/sub bus.

### Generator / semantic model side

- **`tcs:`** — the *toolchain* ontology namespace (this project). Core classes
  `Catalog`, `PipelineDefinition`, `PipelineGenerator`, `PipelineBuild`;
  supporting classes `PipelineComponent`, `InstancePipelineComponent`,
  `Config`, `Channel`, `DockerContainer`, `Compiler`. Key predicates on a
  `tcs:PipelineBuild`: `tcs:compiledFile` (link to an `spdx:File`),
  `tcs:filename`, `tcs:filepath`, `tcs:literal` (file body verbatim).
  Structural: `tcs:instantiates`, `tcs:runs`, `tcs:hasStep`,
  `tcs:config`, `tcs:embedded`. Channel wiring on a step: `tcs:readsFrom`
  / `tcs:writesTo` — semantic annotations pointing at `tcs:Channel`
  instances (typed by inference from either predicate). Runtime flag:
  `tcs:isFinishing` (stripped before `compile()` returns).
- **`p-plan:`** — plan ontology. `p-plan:Plan` (parent of
  `tcs:PipelineDefinition`), `p-plan:Step` (parent of
  `tcs:InstancePipelineComponent`).
- **`dcat:`** — catalog vocabulary. `dcat:Catalog` (parent of `tcs:Catalog`);
  `dcat:qualifiedRelation` + `dcat:hadRole` used to attach `sh:NodeShape`s to
  components and compilers (see the SHACL plan).
- **`spdx:`** — package/build vocabulary. `spdx:Build` (parent of
  `tcs:PipelineBuild`), `spdx:File` (generated files), `spdx:Package`
  (RDF-Connect component dependencies, resolved into `pyproject.toml` /
  `package.json` by `RdfcDockerFileCompiler`).
- **`prov:`** — provenance. `prov:SoftwareAgent` (parent of
  `tcs:PipelineGenerator` and `tcs:PipelineBuild`), `prov:hadPlan` (build →
  definition link). `dct:creator` is used by the generator to record every
  compiler that ran on a build.
- **`sh:NodeShape`** — SHACL shapes; attached to compilers, components, or
  instance-components via `dcat:qualifiedRelation` + `dcat:hadRole` blocks.
  Role vocab: `"configShape"`, `"inputShape"`, `"outputShape"`, or no role.
  Executed via `GraphReader.validate()` (pySHACL) for everything except
  input/output shapes. Application-profile shapes for the whole `tcs:`
  vocabulary live in
  [`data/catalog-application-profile-shapes.ttl`](toolchain-specification/pipeline%20generator/data/catalog-application-profile-shapes.ttl);
  component-scoped shapes live inline in the catalog next to the component.


## 8. Roadmap & open items

> Curated public-facing view: [ROADMAP.md](ROADMAP.md). The list below is the
> working-notes version, updated per session.

### Demonstrator

- [ ] Set realistic `tm:min` / `tm:max` for the water-level threshold monitor
  (currently `0` – `300` cm; real readings are ~1500 cm).
- [ ] Wire the composed `nmo:Email` triples to the mail folder polled by
  `berichtencentrum-deliver-email-service`, and configure SMTP so the last
  hop actually delivers.
- [ ] Decide whether to keep the three separate compose files or merge
  `docker-compose.yml` + `docker-compose-sw.yml` (leaving only `.dev.yml` as
  overlay).

### Pipeline generator

- [ ] **Review newly-added `passthroughShape` attachments** (2026-07-31).
  The trivial `tcs:passthroughShape` on `ldio:HttpOut` in
  [`catalog-ldio.ttl`](toolchain-specification/pipeline%20generator/data/catalog-ldio.ttl)
  and the `passthroughShape → demo:SosaWaterLevelObservationShape`
  override on `demo:LdioForward` in
  [`pipeline_definition.ttl`](toolchain-specification/pipeline%20generator/data/pipeline_definition.ttl)
  are the first uses of the new role in the catalog / pipeline
  definition. User wants to double-check the semantics land as intended
  before rolling out to further passthrough components
  (`rdfc:HttpServer`, `rdfc:SkolemizationProcessor`,
  `ldio:JsonToLdAdapter`, etc.). Also: decide whether
  `demo:JsonLdParse` should switch from `outputShape` to
  `passthroughShape` despite its read-side being JSON bytes.
- [ ] **Add the LDIO service definition** (image, ports, volumes) to
  [`pipeline generator/data/catalog-ldio.ttl`](toolchain-specification/pipeline%20generator/data/catalog-ldio.ttl)
  so the generator can emit an `ldio-workbench` stanza automatically.
  Requires the workbench + starter service pair or a refactor to LDIO
  Pattern A1 — see private note `/memories/dishacled-ldio-a1-refactor.md`.
- [x] **`GraphReader.validate()` in rdfine.** Wraps `pyshacl.validate`,
  returns the SHACL report as a `GraphReader`
  ([`src/rdfine/graph_reader.py`](toolchain-specification/pipeline%20generator/src/rdfine/graph_reader.py)).
  Signature reduced to `validate(**pyshacl_kwargs) -> Self` — the graph is
  used as both data and shapes source. `pyshacl` is a hard `rdfine` dep.
- [x] **Application-profile shapes** for the `tcs:` vocabulary landed at
  [`data/catalog-application-profile-shapes.ttl`](toolchain-specification/pipeline%20generator/data/catalog-application-profile-shapes.ttl)
  (renamed from `tcs_shapes.ttl`; generic profile + framework-specific
  sections for RDF-Connect, LDIO, semantic.works).
- [ ] **Dangling catalog resources.** `rdfc:HttpFetch` and
  `rdfc:LogProcessorPy` are listed in `:DishacledCatalog`'s `dcat:resource`
  (`catalog-core.ttl`) but never defined as `tcs:PipelineComponent`s in
  `catalog-rdfc.ttl` — now surfaced as real `tcs:CatalogShape` violations
  since the 2026-07-29 fix that correctly types `:DishacledCatalog` as
  `tcs:Catalog`. Either remove the two dangling references or add real
  component definitions for them.
- [ ] **Doc cross-references need a filename pass.** `ROADMAP.md`,
  `pipeline generator/README.md`, `docs/architecture-deck.md` and
  `semantic model/README.md` still reference the pre-2026-07-29 filenames
  (`catalog-rdf.ttl`, `tcs_shapes.ttl`) and don't mention `catalog-core.ttl`
  at all — not blocking (the generator has no hardcoded catalog filenames)
  but should be reconciled in a follow-up pass.
- [x] **`tcs:Channel` as first-class connector.** Replaces
  `p-plan:isPrecededBy` on steps; concrete step→channel wiring is now the
  PipelineDefinition author's responsibility via `tcs:embedded` configs.
  `RdfcConfigCompiler` slimmed down accordingly. Per-channel
  `inputShape` / `outputShape` attachment for branching dataflows is a
  *separate* semantic-model refactor — still open, see below.
- [x] **SHACL validation test suite — `ValidationReportCompiler` fully
  implemented.**
  ([`compilers/core/validation_report_compiler.py`](toolchain-specification/pipeline%20generator/src/compilers/core/validation_report_compiler.py))
  All 8 methods from the architecture in
  [`test suite/README.md`](toolchain-specification/test%20suite/README.md)
  are implemented and tested end-to-end against the real demonstrator
  pipeline: `normalize_config_shapes`, `validate_normal_shapes`,
  `gather_throughput_shapes`, `normalize_passthrough_shapes`,
  `fill_missing_shapes`, `list_shapes_to_match`,
  `validate_throughput_shapes`, `generate_validation_report`. The report
  attaches, per shape that actually had a `sh:target`, that shape's own
  `sh:message` plus a `tcs:passed` boolean; shapes without a target are
  never listed. Only remaining dependency: the native Python
  shape-matching library (in development by a colleague) that
  `match_shapes()` calls into once it exists — a documented, overridable
  stub returning `None` until then.
- [x] **Pytest regression suite for the generator itself** (distinct from
  the standalone pre-generator runner above) — 56 tests in
  [`pipeline generator/tests/`](toolchain-specification/pipeline%20generator/tests/)
  covering compiler edge cases (see
  [`tests/EDGE_CASES.md`](toolchain-specification/pipeline%20generator/tests/EDGE_CASES.md))
  plus every `sh:NodeShape` in `catalog-application-profile-shapes.ttl`,
  and 101 tests in
  [`src/rdfine/tests/`](toolchain-specification/pipeline%20generator/src/rdfine/tests/)
  for `rdfine` in isolation — two independently-runnable pytest scopes
  (own `pytest.ini` / `pyproject.toml` `testpaths`).
- [x] **Pipeline segments — entry/exit boundary markers + per-segment LDIO
  output.** Design fully specified 2026-08-19 in
  [`pipeline generator/docs/pipeline-segments-plan.md`](toolchain-specification/pipeline%20generator/docs/pipeline-segments-plan.md)
  (companion decisions doc:
  [`docs/pipeline-segments-decisions.md`](toolchain-specification/pipeline%20generator/docs/pipeline-segments-decisions.md)).
  All slices landed 2026-08-19: slice 1 (vocabulary foundation),
  slice 2 (`ValidationReport` + `DockerCompose` explicit finalize),
  slice 3 (`PipelineSeeder`/`GraphReducer` split +
  `BridgeTransportCompiler` + `SegmentTagger` + shapes), slice 4
  (four per-boundary config compilers:
  `RdfcHttpServerConfigCompiler`, `LdioHttpInConfigCompiler`,
  `LdioHttpOutConfigCompiler`, `RdfcHttpOutConfigCompiler`),
  slice 5 (LDIO Pattern A2 per-segment YAML, curl-sidecar retired,
  `LdioSingularStepShape` rescoped to per-segment). New SHACL
  shapes `tcs:CatalogMissingBridgeShape` and
  `tcs:UnsupportedChannelTopologyShape` added;
  `tcs:BoundaryChannelTypeShape` relaxed to optional channelType.
  Not yet done: `SwSingleInstanceShape` (deferred);
  `test_validation_report_finalization.py` re-verify.
- [x] **`ValidationReportCompiler` becomes an explicit finalize step**
  landed 2026-08-19 as part of slice 3. Same for `DockerComposeCompiler`.
  Both retired from the registry via a new `Compiler.is_explicit_call`
  class attribute; `tcs:isFinishing` machinery removed from
  `PipelineGenerator` and both compilers' `applies_to` overrides.
  `pipeline-segments-plan.md` §Compile-flow diagram documents the
  fixed call order (extractor → fixpoint loop → `ValidationReport` →
  `DockerCompose`).
- [ ] **Per-channel `inputShape` / `outputShape` attachment.** With
  `tcs:Channel` in place, the shape-attachment model gains a third level
  (component → instance → channel, most-specific wins) so branching
  dataflows can express distinct output shapes per downstream branch.
  Coordinates with the shape-matching pass in the test suite.
- [ ] **Manual sync risk: `PipelineEnricher.synthesize_channels()` vs.
  `data/inference_rules.yaml`.** The inference file carries a second,
  declarative copy of the same three-case `p-plan:isPrecededBy`→channel
  logic (mint-fresh / reuse-predecessor's-writesTo /
  reuse-successor's-readsFrom), added 2026-08-05 so pre-compile SHACL
  shapes (`LdioStepOrderingShape`, `AcyclicGraphShape`) can see
  `isPrecededBy`-implied wiring without running the generator.
  `PipelineGenerator.compile()` never calls `GraphReader.infer()`, so
  the two tracks don't interact at runtime — but if the compiler's case
  logic ever changes, the inference rules need the same edit by hand.
  No automated guard against drift today.
- [x] **Compiler package reorganized** into per-framework subfolders
  (`compilers/{core,ldio,rdfc,sw}/`) 2026-07-24. `SemanticWorksCompiler`
  renamed to `SemanticWorksEnvVarCompiler` to reflect narrow scope.
- [x] **Semantic.works Tier-1/2 boilerplate emitted by per-service compilers**
  2026-07-24. Six new compilers under `compilers/sw/` materialize the nine
  files under `semantic-works/config/**` from `tcs:DefaultConfig`s in
  `catalog-sw.ttl`. Bodies byte-identical to the demonstrator.
- [ ] **Tier-3 `delta/rules.js` cross-framework rule synthesis.** The
  `rdf:type oslc:Error → error-alert` rule currently ships as boilerplate;
  long-term it should be derived from a cross-framework `tcs:Channel`.
  Blocked on the semantic-model gap (pipeline-definition header item #1).
- [ ] **Pipeline-level `tcs:Config` override mechanism.** Let a
  `tcs:PipelineDefinition` shadow a catalog-level `tcs:DefaultConfig` with
  a pipeline-specific body. Prerequisite for cleanly moving the
  demonstrator-specific bodies (`ErrorAlertTemplateHbsDefault`, the
  `oslc:Error` rule, `email.folder` IRI, `public` graph ACL, minimal
  dispatcher routes) out of `catalog-sw.ttl` and into
  `pipeline_definition.ttl`. See `pipeline generator/README.md` §5.1.
- [x] **`DockerComposeCompiler` emits `depends_on`** stanzas.
  `dct:requires` links between components already declare the runtime
  dependency chain (LDIO → RDFC, error-alert → delta-notifier →
  triplestore, etc.); as a fallback for pairs with no such link, the
  producing container's step depends on the consuming container's step
  along a `tcs:Channel` (e.g. the demonstrator's `ldio-workbench →
  rdfc` edge). See
  [`compilers/core/docker_compose_compiler.py`](toolchain-specification/pipeline%20generator/src/compilers/core/docker_compose_compiler.py)'s
  `fold_in_depends_on`.
- [ ] **`NifiCompiler`** — add support for [Apache Nifi 2](https://nifi.apache.org/)
  as a fourth target framework (Urban Sense use case).
- [ ] **`SemanticModelVersionMapper`** — map from a version of the public
  semantic model to the internal model used by the generator; decouple
  versioning of the two.

### Cross-cutting

- [ ] _TODO — identify and log any other DiSHACLed org repos beyond
  `demonstrator` and `toolchain-specification`._
- [ ] _TODO — research paper / thesis draft: location, current status, target
  venue._


## Current status (detail)

Full implementation-level detail behind the condensed summary in AGENTS.md §3.

## 3. Current status

- ✅ **End-to-end pipeline verified working** across all three frameworks.
  Water-level readings flow from the source-a API through LDIO enrichment,
  RDF-Connect threshold monitoring, and into the semantic.works triple store,
  producing `oslc:Error` entities and composed `nmo:Email` triples.
  See [README.md](README.md) for the run recipe and verification queries.
  LDIO workbench + `Ldio:JsonToLdAdapter` + SSN/SOSA-mapping SPARQL CONSTRUCT
  in place ([`LDIO/`](LDIO/)). RDF-Connect pipeline targets
  `sosa:Observation` / `sosa:hasSimpleResult` ([`RDFC/pipeline.ttl`](RDFC/pipeline.ttl)).
- ⚠️ `berichtencentrum-deliver-email-service` doesn't actually send SMTP —
  composed `nmo:Email` triples aren't linked to the mail folder the sender
  polls. Deployment gap in the semantic.works wiring, not a pipeline bug.
- ⚠️ `RDFC/pipeline.ttl` `tm:max` is `300 cm`, a demo placeholder — real
  water-level readings (~1500 cm) fire on every poll. Realistic thresholds
  needed before shipping the demo.
- ✅ **Pipeline generator** supports RDF-Connect, LDIO and semantic.works
  compilers. Delivers docker-compose wiring (including `depends_on`
  stanzas, derived from `dct:requires` between containers and,
  as a fallback, from cross-container channel dataflow order),
  framework-specific configs, a self-describing build graph, and an
  ad-hoc RDF-Connect Dockerfile that only ships components a pipeline
  actually uses. End-to-end walkthrough in
  [`pipeline generator/src/demo.ipynb`](toolchain-specification/pipeline%20generator/src/demo.ipynb).
- ✅ **`tcs:Channel`** is the first-class connector between steps.
  Concrete step→channel wiring (`rdfc:reader` / `rdfc:writer` /
  `rdfc:memberStream` …) is the PipelineDefinition author's
  responsibility, expressed inside each step's `tcs:embedded` config
  (`p-plan:isPrecededBy` is also supported as a terser alternative for
  the common strictly-serial case — `PipelineEnricher` expands it into
  concrete channel wiring). `RdfcConfigCompiler.describe_channels()`
  only emits the `rdfc:Reader, rdfc:Writer` type boilerplate for
  channels the emitted `pipeline.ttl` actually references. The
  `tcs:Channel` type is inferred from `tcs:readsFrom` / `tcs:writesTo`
  by [`data/inference_rules.yaml`](toolchain-specification/pipeline%20generator/data/inference_rules.yaml).
- ✅ **Data folder** — the demonstrator pipeline definition is the sole
  pipeline in
  [`data/pipeline_definition.ttl`](toolchain-specification/pipeline%20generator/data/pipeline_definition.ttl).
  The catalog lives in four self-contained files, meant to be loaded
  together into a single graph alongside `pipeline_definition.ttl` and
  the shapes file below:
  [`data/catalog-core.ttl`](toolchain-specification/pipeline%20generator/data/catalog-core.ttl) —
  framework-agnostic bits (`:pip`/`:npm` package-manager stubs,
  `:DishacledCatalog`'s base declaration + full `dcat:resource` list, the
  two water-level datasets + mock JSON-LD API service descriptions, and
  the single canonical `tcs:prefixes` SPARQL-prefix registry used by every
  SHACL SPARQL-based constraint in the catalog);
  [`data/catalog-ldio.ttl`](toolchain-specification/pipeline%20generator/data/catalog-ldio.ttl),
  [`data/catalog-rdfc.ttl`](toolchain-specification/pipeline%20generator/data/catalog-rdfc.ttl) and
  [`data/catalog-sw.ttl`](toolchain-specification/pipeline%20generator/data/catalog-sw.ttl) —
  one file per framework, each self-contained with that framework's
  component definitions, configShapes and `tcs:Config` bodies.
- ✅ **SHACL application profile** at
  [`data/catalog-application-profile-shapes.ttl`](toolchain-specification/pipeline%20generator/data/catalog-application-profile-shapes.ttl).
  Two sections: (1) generic profile for the `tcs:` vocabulary (class/property
  expectations, deployability, `≤ 1 tcs:DefaultConfig` per subtype), and (2)
  compiler-specific constraints for RDF-Connect (`owl:imports` on
  processors), LDIO (`ldio:type` enum, strict seriality, Input/Output
  ordering, `rdfs:label` required), and semantic.works (direct
  `tcs:DockerComposeConfig`, single `p-plan:hasInputVar`, literal env values).
  Every shape carries a top-level `sh:message` describing what it tests
  (in addition to any per-constraint messages), consumed by
  `ValidationReportCompiler` (below). Uses SHACL-AF SPARQL targets and
  constraints where SHACL Core can't express the intent.
- ✅ **`GraphReader.validate()`** implemented in `rdfine`
  ([`src/rdfine/graph_reader.py`](toolchain-specification/pipeline%20generator/src/rdfine/graph_reader.py)).
  Wraps `pyshacl.validate` and returns a `GraphReader` around the results
  graph, preserving the graph-in / graph-out contract. Uses `self.graph`
  as both data and shapes source; conformance is `?r sh:conforms true`
  inside the report. `pyshacl` is a hard `rdfine` dependency.
- ✅ **`ValidationReportCompiler` fully implemented**
  ([`compilers/core/validation_report_compiler.py`](toolchain-specification/pipeline%20generator/src/compilers/core/validation_report_compiler.py)).
  All 8 methods from the architecture in
  [`test suite/README.md`](toolchain-specification/test%20suite/README.md)
  are implemented and tested end-to-end against the real demonstrator
  pipeline, which compiles to `conforms: true` with zero violations. The
  attached report lists, per shape that actually had a `sh:target`
  (untargeted shapes are never listed), that shape's own `sh:message` and
  a `tcs:passed` boolean; shape identifiers that are catalog-normative
  keep their IRI, auto-minted ones (from an originally blank-node shape,
  or from a channel with no producer) render back as blank nodes in the
  report. Only remaining dependency: the native Python shape-matching
  library (in development by a colleague) that `match_shapes()` calls
  into once it exists — a documented, overridable stub returning `None`
  until then.
- ✅ **Full demonstrator reproduction** via the generator. The pipeline
  [`data/pipeline_definition.ttl`](toolchain-specification/pipeline%20generator/data/pipeline_definition.ttl)
  (`demo:DishacledPipeline`) plus the components in
  `data/catalog-{core,ldio,rdfc,sw}.ttl`
  compile down to a project folder that covers every component,
  processor, LDIO adapter, and mu-semtech service the hand-built
  demonstrator deploys — 10/10 compose services, 9/9 RDF-Connect
  processor types, 4/4 LDIO components in common with `demonstrator/`,
  plus **all 9 semantic.works Tier-1/2 config files byte-identical** to
  their `demonstrator/semantic-works/config/` counterparts. Remaining
  known drift: `ldio-workbench` version (deferred LDIO A1 refactor, §8).
  Comparison cells live at the end of
  [`src/demo.ipynb`](toolchain-specification/pipeline%20generator/src/demo.ipynb).
- 🚧 The generator's catalog does **not yet** contain the LDIO service definition
  used by the demonstrator (image, ports, volumes for `ldio-workbench`). Filed
  under §8.
- ✅ **Compiler package** organized into per-framework subfolders:
  `compilers/{core,ldio,rdfc,sw}/`. `base.py`, `utils.py`,
  `pipeline_generator.py`, `project_builder.py` stay at the package root.
  Every compiler follows the method-naming & single-responsibility
  splitting convention in §5.
- ✅ **Semantic.works Tier-1/2 config files** emitted by per-service
  compilers under `compilers/sw/` (`VirtuosoCompiler`,
  `MuClResourcesCompiler`, `MuDispatcherCompiler`, `MuDeltaNotifierCompiler`,
  `MuAuthorizationCompiler`, `ErrorAlertCompiler`) — each materializes its
  file(s) under `semantic-works/config/**` from a `tcs:DefaultConfig` in
  `catalog-sw.ttl`, using a `read_literal` helper in `compilers/utils.py`
  that bypasses `dct:format`-based parsing to preserve file bytes.
- ⚠️ **Some sw `tcs:DefaultConfig`s carry demonstrator-specific content**
  that the current compiler has no override mechanism to displace. Most
  visible: `MuDeltaNotifierRulesJsDefault` includes the `oslc:Error →
  error-alert` cross-framework rule; `ErrorAlertTemplateHbsDefault` is
  the demonstrator's "UFFFFFF!!" email copy;
  `ErrorAlertConfigJsonDefault` bakes in the demonstrator's mail-folder
  IRI. Documented in `pipeline generator/README.md` §5.1 and
  `data/catalog-sw.ttl`'s sw section header. Fix requires a
  pipeline-level override predicate — tracked in §8.
- 🚧 **Tier-3 `delta/rules.js` cross-framework rule.** Same root cause
  as the item above; long-term the `rdf:type oslc:Error → error-alert`
  rule should be synthesized from a cross-framework `tcs:Channel`
  (pipeline-definition header item #1).
- 🚧 **Test-suite runner** (the pre-generator entry point that ties
  everything together — `validate_pipeline_definition(...)`,
  `ShaclValidationError`) not yet built. Design in
  [`test suite/README.md`](test%20suite/README.md).
- 🚧 **Pipeline segments & cross-container bridges** — design frozen
  2026-08-19; slices 1 (vocabulary foundation) and 3 (explicit finalize
  calls) landed. Remaining slices: `BridgeTransportCompiler` +
  `SegmentTagger` + `PipelineSeeder`/`GraphReducer` split, four
  per-boundary config compilers, LDIO segment splitting, shape rescopes.
  See
  [`pipeline generator/docs/pipeline-segments-plan.md`](toolchain-specification/pipeline%20generator/docs/pipeline-segments-plan.md)
  and §8.
- ✅ **`PipelineAssembler`/`DockerComposeCompiler` are self-scoping.**
  Neither compiler depends any more on `PipelineExtractor` having
  already narrowed the catalog down to just one pipeline's components
  — `PipelineAssembler.describe_docker_container()` scopes its
  component universe via a new `_lookup_relevant_components()`
  (components actually specialized by a step of this pipeline, plus
  their transitive `dct:requires` closure) and
  `DockerComposeCompiler.merge_docker_compose_configs()` only
  aggregates configs reachable from an actual `tcs:DockerContainer` on
  this build, mirroring the reachability path
  `_lookup_container_service_names()` already used. Surfaced as a
  prerequisite while evaluating the pipeline-segments auto-insertion
  design (§8) — both queries previously enumerated *every* matching
  node in the whole graph, relying implicitly on upfront narrowing that
  a wider-catalog compile step would have broken.

Full history of how the project got here (bug fixes, superseded designs,
per-session detail): [`SESSION-LOG.md`](../../../../SESSION-LOG.md) §2.


## Working with Copilot — deep procedures

Day-to-day procedures behind the pointer in AGENTS.md §6.

### During the session — demonstrator side

- **Prefer editing over rewriting.** The pipeline is tightly linked across
  three frameworks; a small change in one place often has visible effects in
  another (see the SSN/SOSA switch that touched LDIO, RDFC, and the README).
- **Verify each hop** when changing the schema or thresholds — a broken
  `typeFilter` will silently drop everything downstream (no error, just no
  output). See the end-to-end verification recipe in
  [demonstrator/README.md](demonstrator/README.md#verifying-end-to-end).
- **When editing `RDFC/pipeline.ttl`**, remember the RDFC docker image is
  built from context; a plain `docker compose restart` won't pick up TTL
  changes. Use `docker compose up -d --build --force-recreate rdfc`.
- **When editing `LDIO/pipelines/*.yml`**, a `docker compose restart
  ldio-workbench` is enough — the file is bind-mounted read-only into the
  running container.

### During the session — pipeline generator side

- **Environment.** Always use the conda env literally named
  `pipeline_generator` (`~/anaconda3/envs/pipeline_generator`, i.e.
  `conda activate pipeline_generator`) — the one and only Python
  environment for the whole DiSHACLed project, Python 3.11+. It already
  has `rdfine` (editable, resolving to this repo's
  `pipeline generator/src/rdfine`), `pyshacl`, `rdflib`, and the rest of
  `compilers`' deps (pyld, pandas, PyYAML, boltons, glom, validators)
  installed. To (re)install from scratch: `pip install ./src/rdfine` from
  `toolchain-specification/pipeline generator/` (equivalent to
  `pip install -r requirements.txt` from the same folder). Never use
  system Python or create/activate a different/ad-hoc env for this
  project — a stray `toolchain-specification/.conda` env was mistakenly
  created and used for part of a session on 2026-08-18 before this was
  caught; it has since been removed.
- **Adding a compiler.** Drop a new file in
  `pipeline generator/src/compilers/`, subclass `Compiler`, implement
  `compile()`, override `applies_to()` (default returns `False`), and import
  the module from `compilers/__init__.py` so `__init_subclass__` fires. Do
  **not** touch `PipelineGenerator` — the fixpoint loop will pick the compiler
  up automatically.
- **Emitting files.** Use `attach_file(self.output_reader, filename=,
  filepath=, content=)` from `compilers.utils` and re-assign
  `self.output_reader`. Never write to disk from inside `compile()`;
  `ProjectBuilder` does that at the end.
- **Onboarding a new framework.** Extend the catalog with the new components
  (including framework-specific predicates such as `ldio:type` /
  `rdf:label` for LDIO, `owl:imports` for RDFC), then add compiler(s) as above.
  See §6.3 of
  [`pipeline generator/README.md`](toolchain-specification/pipeline%20generator/README.md).
- **Debugging.** Every compile stage is an `rdflib.Graph`. Serialize it with
  `.serialize(format="turtle")`, or wrap it in `GraphReader` and use
  `.filter()` / `.select()` / `.df` to inspect. After `.compile()` returns,
  `gen.compilers` records exactly which compilers ran and in what order (same
  info also lives on the build as `dct:creator` triples).
- **Validation.** Merge catalog + pipeline definition (+ shapes) into one
  `rdflib.Graph`, wrap in `GraphReader`, apply `.infer(inference_rules.yaml)`,
  then `.validate(advanced=True, inference='rdfs')`. The returned reader is
  the SHACL report — `report.ask("?r sh:conforms true")` for a pass/fail,
  `report.select("?focus ?message", "?r a sh:ValidationResult ; sh:focusNode ?focus ; sh:resultMessage ?message .")`
  to enumerate violations.
- **End-to-end reference.** `pipeline generator/src/demo.ipynb`.

