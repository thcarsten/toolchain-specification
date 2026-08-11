# Adding support for pipeline segments

Design session, 2026-08-07. Discussion-only — no code or catalog changes
were made; this document captures the reasoning so implementation can start
directly from "The proposed design" below without re-deriving it.

## Motivation

Two related questions exposed the same underlying gap in the generator's
container/config assignment model:

1. What does the generator compile for a pipeline that switches frameworks
   more than once (LDIO → RDFC → LDIO → RDFC)?
2. Does the generator support instancing the same catalog `PipelineComponent`
   twice (e.g. two `sw:mu-dispatcher` steps)?

Both trace back to the same fact: container assignment
(`PipelineAssembler.describe_docker_container()`) is keyed purely on catalog
**component identity** — one container per component, every
`InstancePipelineComponent` specializing it shares that one container,
regardless of how many times the component is reused or where in the
pipeline it appears. This is *necessary* (see "Why `PipelineAssembler` stays
unchanged" below) but it interacts badly with LDIO's file-emission model,
and is outright unsafe for semantic.works.

## Key findings from this session

- **LDIO already supports N independent pipelines per container**, verified
  two ways: the demonstrator's own
  `demonstrator/LDIO/application.yml` already uses Pattern A2
  (`orchestrator: directory: /ldio/pipelines` — directory-scan startup,
  every file in that directory is its own independent pipeline), and the
  official docs describe Pattern A1 (`orchestrator.pipelines:` list) as the
  single-file equivalent. Segment-splitting can piggyback on the
  **already-in-use Pattern A2** — no need for the previously-deferred
  Pattern A1 migration (see private memory note
  `dishacled-ldio-a1-refactor.md`).
- **RDF-Connect has no analogous concept** — confirmed via
  `demonstrator/RDFC/Dockerfile`'s `CMD ["npx", "rdfc", "pipeline.ttl"]`
  (exactly one file argument) and `RdfcConfigCompiler`'s requirement that
  the pipeline node be named `<>` (the document itself). But it doesn't
  need the concept either: one `pipeline.ttl` can contain disconnected
  processor/environment subgraphs that already run independently in
  parallel. Segments there are informational only (validation / wiring
  checks), never something the compiler needs to act on for file emission.
- **Every semantic.works per-service compiler is single-tenant by
  construction** — checked `MuDispatcherCompiler`, `MuClResourcesCompiler`,
  `MuDeltaNotifierCompiler`, `ErrorAlertCompiler` (and by the same pattern
  `VirtuosoCompiler`/`MuAuthorizationCompiler`): each emits exactly one
  static file from exactly one catalog-level `tcs:DefaultConfig`, with zero
  per-step aggregation of file *content* (only `SemanticWorksEnvVarCompiler`
  folds per-step data, and only into env vars, never into these files).
  These are genuinely single-tenant microservices, unlike LDIO/RDF-Connect's
  multi-tenant orchestrator runtimes.
- **Component reuse already works today for RDF-Connect and for
  LDIO's `Transformer`/`Output` types** — the demonstrator's own
  `pipeline_definition.ttl` already instances `rdfc:Sdsify` twice
  (`SdsifyMeasurements`/`SdsifyViolations`) and `rdfc:LogProcessorJs` twice
  (`LogMeasurementsMeta`/`LogViolationsMeta`), each keeping distinct
  per-step configs correctly. LDIO `Input`/`Adapter`-typed components are
  the exception: reuse there is structurally broken today (`fill_in_components()`
  uses `.update()` on a single dict slot, so a second `Input` step silently
  overwrites the first) and is already forbidden by the existing
  `LdioSingularStepShape`.
- **The real fault line is runtime multi-tenancy, not segment topology.**
  Sharing one container across multiple dataflow runs is safe exactly when
  the underlying runtime is a genuine multi-tenant orchestrator (LDIO,
  RDF-Connect, presumably future Nifi) and unsafe when it's a single-config
  microservice (every semantic.works service checked). That sw steps also
  happen to have no `tcs:readsFrom`/`tcs:writesTo` at all (gap #3 in
  `pipeline_definition.ttl`'s own header — causality runs through the
  shared triple store + delta-notifier, not channels) is a related but
  *incidental* fact, not the cause.

## The proposed design

1. **Catalog: mark components as entry-point / exit-point.** Generalizes
   LDIO's existing `ldio:type` Input/Output enum into a toolchain-level
   `tcs:` concept usable by every framework, e.g. `tcs:boundaryRole` with
   values `tcs:EntryBoundary` / `tcs:ExitBoundary` (multi-valued, not a
   boolean pair — a trivial one-step segment's sole component could be
   both). Exact predicate/value names are placeholders, not finalized
   against the semantic model.
2. **`PipelineEnricher` tags segments.** New pass alongside its existing
   channel-synthesis logic: walks `tcs:readsFrom`/`tcs:writesTo` from every
   Entry-marked step to the nearest reachable Exit-marked step(s), tagging
   each maximal run as a segment. `sw` steps, having no channel wiring at
   all, trivially form their own one-step segments — this falls out of the
   existing algorithm for free, no special-casing needed.
3. **`PipelineAssembler` stays exactly as-is.** Container existence
   (`describe_docker_container()`) and step assignment (`describe_step()`)
   remain purely driven by the catalog dependency graph
   (`dct:requires*` reachability to a `tcs:DockerComposeConfig`), completely
   decoupled from segments. This is *required*, not just simpler: components
   with zero `InstancePipelineComponent` steps at all (e.g. `sw:mu-identifier`,
   infrastructure per gap #4 in `pipeline_definition.ttl`'s header) have zero
   segments to be discovered by a channel-walking pass, but still need a
   container. Segment-driven container assignment would silently lose these.
4. **`LdioConfigCompiler` refactored to emit one config file per segment**
   instead of one merged file — using the already-in-use Pattern A2
   directory-scan mechanism (`ldio/pipelines/segment_N.yml`), not a Pattern
   A1 migration. This directly fixes the original "LDIO → RDFC → LDIO →
   RDFC" clobbering bug (two `Input` steps silently colliding via
   `dict.update()`).
5. **`LdioSingularStepShape` gets rescoped, not removed.** The "≤1 `Input`,
   ≤1 `Adapter`" constraint is still real and necessary *within one
   segment* — removing it outright would silently reopen the exact
   clobbering bug within a single segment. Only its counting unit changes:
   from "per `tcs:PipelineDefinition`" to "per segment."
6. **semantic.works gains a new SHACL shape** flagging ">1
   `InstancePipelineComponent` specializing the same `sw:` component" as
   currently unsupported. Pragmatically scoped to the `sw:` namespace for
   now (every current sw component is single-tenant); a more general
   `tcs:multiTenantContainer` marker predicate is a possible future
   generalization, worth introducing only if/when a framework with a *mix*
   of multi-tenant and single-tenant components shows up (e.g. Nifi).
7. **`ValidationReportCompiler` becomes an explicit "finalize" step**,
   symmetric to `PipelineExtractor`'s explicit "bootstrap" step, rather
   than a registry-discovered `Compiler`:
   - Runs once, after the fixpoint loop terminates, before `ProjectBuilder`.
     Needs no `tcs:isFinishing` gating or `dct:creator`-ordering guard
     against `DockerComposeCompiler`, because it's no longer racing
     anything in the registry — it's just called directly, last.
   - Takes **two** graph inputs (like `PipelineExtractor` takes
     `pipeline_id` + the catalog graph): the finished build graph, and the
     **original, untouched `catalog_graph`** (kept entirely separate from
     the extraction/traversal narrowing). pySHACL's `data_graph` stays the
     build graph; `shacl_graph` becomes the build graph merged with the
     untouched catalog graph — so generic application-profile shapes (which
     never survive `PipelineExtractor`'s traversal-based narrowing today)
     become visible to validation, with **no new shape-preservation logic
     needed inside `PipelineExtractor` at all**. (Superseded an earlier idea
     in this session of having `PipelineExtractor` unconditionally pull in
     every `sh:NodeShape` via traversal — simpler to just never narrow the
     shapes graph in the first place.)
   - `ProjectBuilder` naturally only ever sees a build graph that already
     has the validation report attached, since `PipelineGenerator.compile()`
     doesn't return until this last step has run. No separate "wait for the
     report" logic needed on the caller side — it falls out of the existing
     "compile() blocks until the loop settles" semantics.

## Rejected / superseded alternatives

- **Segment-driven container assignment** (assign containers by segment
  instead of by component identity) — rejected: breaks infra-only
  components (see point 3 above).
- **LDIO Pattern A1 migration** for segment-splitting — unnecessary, Pattern
  A2 (already in use) achieves the same effect with less compiler work.
- **`PipelineExtractor` unconditionally preserving all `sh:NodeShape`s via
  traversal** — superseded by keeping the shapes graph entirely separate
  from the narrowed build graph (point 7), which needs no traversal logic
  and can't accidentally miss a nested shape body.
- **Gating `ValidationReportCompiler` on `tcs:isFinishing true` alone**
  (mirroring `DockerComposeCompiler`) — superseded once it needed a second
  graph input anyway; pulling it fully out of the registry loop obviates
  the ordering question entirely.

## Open questions

- Exact vocabulary (`tcs:boundaryRole`, `tcs:EntryBoundary`/`tcs:ExitBoundary`)
  not yet reconciled against `semantic model/README.md`.
- Whether `tcs:multiTenantContainer` is worth introducing now vs. waiting
  for a second framework with mixed multi-tenancy.
- Whether the new sw "unsupported multi-instance" shape should hardcode the
  `sw:` namespace prefix or be expressed generically from the start via a
  marker predicate.
- Per-channel `inputShape`/`outputShape` for `rdfc:Sdsify`'s branching
  outputs (pre-existing roadmap item) is untouched by this plan, though it
  touches the same channel model.

## Status

Design-only session (2026-08-07). No code or catalog `.ttl` files were
changed. Next session should start implementation at point 1 (catalog
vocabulary) of "The proposed design" above.
