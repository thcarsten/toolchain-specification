# Pipeline segments & cross-container bridges — decisions, alternatives, open questions

Companion to [`pipeline-segments-plan.md`](pipeline-segments-plan.md), which
describes the design itself. This document holds everything that is *not*
a description of the design: what was considered and rejected, what's
still undecided, and the session-by-session timeline. Kept separate so
`pipeline-segments-plan.md` only ever has to describe what the design
currently *is*.

## Session timeline

- **2026-08-07** — Design-only session. Two questions (multi-hop framework
  switching, catalog-component reuse) traced to one root cause (container
  assignment keyed on component identity). Produced the "Key findings"
  below and the first version of the design now in `pipeline-segments-plan.md`
  (points 1–7 of "The proposed design"). No code or catalog `.ttl` files
  changed.
- **2026-08-18 (later)** — Two corrections to the bridge design, both
  still no code or catalog changes: (1) entry/exit marking switched from
  a `tcs:boundaryRole` property with enum-like object values to two
  `rdfs:subClassOf tcs:PipelineComponent` classes
  (`tcs:EntryBoundaryComponent`/`tcs:ExitBoundaryComponent`), so typing a
  component as one of these also entails `a tcs:PipelineComponent` via
  plain RDFS inference; (2) endpoint/port discovery moved out of the
  framework's own `configShape` entirely into a separate
  `tcs:bridgeEndpoint`/`tcs:bridgeRole`/`tcs:configPath` annotation on the
  component node, since RDF-Connect's real configShape must stay
  unaware of `tcs:`-specific concerns and `sh:class` doesn't apply to
  literal-valued properties like `rdfc:port` in the first place.
- **2026-08-18 (still later)** — Further simplified the endpoint-discovery
  annotation to two flat literal-valued properties, `tcs:portPath` and
  `tcs:endpointPath`, directly on the component (no blank node, no
  separate `tcs:bridgeRole`/`tcs:configPath` split) — the component's own
  Entry/Exit typing already disambiguates what each path means, so a
  role marker added nothing. Also specified the wiring step's idempotency
  and mismatch-detection behaviour (never silently overwrite a
  manually-authored endpoint value; raise a SHACL violation naming both
  sides on a mismatch). Opened, but did not resolve, the question of
  whether `PipelineExtractor`'s catalog-narrowing is compatible with
  auto-inserting a missing bridge component — see Open questions below.
  No code or catalog changes.
- **2026-08-19** — Full design pivot, no code changes. `BridgeTransportCompiler`'s
  responsibility narrowed to *only* inserting boundary steps; all
  transport configuration moved to a new family of per-boundary-component
  config compilers (one per Entry component, one per Exit component,
  framework-specific). Endpoint discovery no longer uses `tcs:portPath`/
  `tcs:endpointPath` at all — Entry compilers write `tcs:endpoint`/
  `tcs:port` directly onto the channel; Exit compilers read from there.
  Same graph-as-ambient-message-bus pattern as `dct:creator`. Catalog
  boundary components gain a mandatory `tcs:channelType` predicate
  (values `tcs:HttpChannel`, `tcs:KafkaChannel`, …); the compiler carries
  a preconfigured `default_channel_type = tcs:HttpChannel` for MVP.
  Onboarding a new framework's boundaries needs only new catalog
  triples. `PipelineExtractor` splits into an early `PipelineSeeder`
  (no narrowing) and a late `GraphReducer` (narrowing, running after
  `BridgeTransportCompiler`) — resolves the 2026-08-18 open question:
  no catalog-access API needed on individual compilers, they can just
  see the untouched catalog until the reducer runs. `ValidationReportCompiler`
  and `DockerComposeCompiler` become explicit finalize calls (not
  registry-triggered); `tcs:isFinishing` machinery retired. Also confirmed
  by reading the code: today's `PipelineExtractor.extract_pipeline()`
  already preserves shape subgraphs and used-component catalog
  membership through narrowing, so the `GraphReducer` refactor is
  code-motion, not new logic. Also confirmed `ldio:HttpIn` (Entry) and
  `rdfc:HttpOut` (Exit) exist in the catalog — four HTTP boundary
  components in total: `rdfc:HttpServer`/`ldio:HttpIn` (Entries),
  `rdfc:HttpOut`/`ldio:HttpOut` (Exits). `rdfc:HttpFetch` and
  `ldio:HttpInPoller` deliberately excluded — pullers, not boundaries.

## Key findings (2026-08-07 session)

- **LDIO already supports N independent pipelines per container**, verified
  two ways: the demonstrator's own `demonstrator/LDIO/application.yml`
  already uses Pattern A2 (`orchestrator: directory: /ldio/pipelines` —
  directory-scan startup, every file in that directory is its own
  independent pipeline), and the official docs describe Pattern A1
  (`orchestrator.pipelines:` list) as the single-file equivalent.
  Segment-splitting can piggyback on the already-in-use Pattern A2 — no
  need for the previously-deferred Pattern A1 migration (see private
  memory note `dishacled-ldio-a1-refactor.md`).
- **RDF-Connect has no analogous concept** — confirmed via
  `demonstrator/RDFC/Dockerfile`'s `CMD ["npx", "rdfc", "pipeline.ttl"]`
  (exactly one file argument) and `RdfcConfigCompiler`'s requirement that
  the pipeline node be named `<>` (the document itself). But it doesn't
  need the concept either: one `pipeline.ttl` can contain disconnected
  processor/environment subgraphs that already run independently in
  parallel.
- **Every semantic.works per-service compiler is single-tenant by
  construction** — checked `MuDispatcherCompiler`, `MuClResourcesCompiler`,
  `MuDeltaNotifierCompiler`, `ErrorAlertCompiler` (and by the same pattern
  `VirtuosoCompiler`/`MuAuthorizationCompiler`): each emits exactly one
  static file from exactly one catalog-level `tcs:DefaultConfig`, with zero
  per-step aggregation of file content.
- **Component reuse already works today for RDF-Connect and for LDIO's
  `Transformer`/`Output` types** — the demonstrator's own
  `pipeline_definition.ttl` already instances `rdfc:Sdsify` twice and
  `rdfc:LogProcessorJs` twice, each keeping distinct per-step configs
  correctly. LDIO `Input`/`Adapter`-typed components are the exception —
  reuse there is structurally broken (`fill_in_components()` uses
  `.update()` on a single dict slot) and already forbidden by
  `LdioSingularStepShape`.

These findings led directly to the "real fault line is multi-tenancy, not
segment topology" conclusion now stated as a rule in
[`pipeline generator/README.md`](../README.md#63-how-to-onboard-new-frameworks)'s
container/multi-tenancy contract.

## Rejected / superseded alternatives

- **Segment-driven container assignment** (assign containers by segment
  instead of by component identity) — rejected: breaks infra-only
  components (zero steps, e.g. `sw:mu-identifier`) which have no segment
  to key a per-segment container off of.
- **LDIO Pattern A1 migration** for segment-splitting — unnecessary,
  Pattern A2 (already in use) achieves the same effect with less compiler
  work.
- **`PipelineExtractor` unconditionally preserving all `sh:NodeShape`s via
  traversal** — superseded by keeping the shapes graph entirely separate
  from the narrowed build graph, which needs no traversal logic and can't
  accidentally miss a nested shape body.
- **Gating `ValidationReportCompiler` on `tcs:isFinishing true` alone**
  (mirroring `DockerComposeCompiler`) — superseded once it needed a second
  graph input anyway; pulling it fully out of the registry loop obviates
  the ordering question entirely.
- **`tcs:boundaryRole` as an object-valued property** (`tcs:boundaryRole
  tcs:EntryBoundary`/`tcs:ExitBoundary`) — superseded by two dedicated
  `rdfs:subClassOf tcs:PipelineComponent` classes, `tcs:EntryBoundaryComponent`/
  `tcs:ExitBoundaryComponent`, so a component only needs one type triple
  to be both classified as a boundary component *and* entailed as a
  `tcs:PipelineComponent`.
- **Marking the bridge's port/path properties via `sh:class` inside the
  framework's own `configShape`** — superseded by a separate
  `tcs:bridgeEndpoint` annotation on the component node. Two problems
  with the original approach: it required editing (or wrapping) a
  framework's real config-validation shape with `tcs:`-specific markers,
  and `sh:class` is meaningless for the literal-valued properties
  actually involved (`rdfc:port`, `rdfc:path`, `ldio:endpoint`) since
  `sh:class` constrains the *type* of an object value, not a literal.
- **`tcs:bridgeEndpoint [ tcs:bridgeRole ... ; tcs:configPath ... ]` as a
  blank-node structure** — superseded by two flat literal-valued
  properties directly on the component, `tcs:portPath` and
  `tcs:endpointPath`. The `tcs:bridgeRole` discriminator turned out to be
  redundant: which path is which is already implied by whether the
  component is typed `tcs:EntryBoundaryComponent` (supplies both
  `tcs:portPath` and `tcs:endpointPath`, both read) or
  `tcs:ExitBoundaryComponent` (supplies `tcs:endpointPath` alone,
  written).
- **`tcs:portPath` / `tcs:endpointPath` on the component** — superseded
  entirely 2026-08-19 by writing transport metadata (`tcs:endpoint`,
  `tcs:port`) directly onto the `tcs:Channel` at compile time. The Entry
  config compiler for a given boundary component knows where its own
  port/endpoint sit in its framework's config schema (that's its whole
  reason for existing) — no need to expose that location declaratively
  for a generic compiler to traverse. Decouples Entry and Exit fully:
  the paired-step lookup problem disappears because both sides
  communicate through the shared channel, not through each other.
- **`BridgeTransportCompiler` also configuring the inserted boundary
  steps** — superseded 2026-08-19 by splitting into per-boundary-component
  config compilers. Configuration is framework-specific expertise; the
  bridge compiler stays framework-agnostic. Same separation-of-concerns
  pattern as `sw/`'s per-service compilers (`VirtuosoCompiler`,
  `MuDispatcherCompiler`, …).
- **Fixed hardcoded `rdfc:HttpServer` / `ldio:HttpOut` pair for MVP** —
  superseded 2026-08-19 by `tcs:channelType`-based matching. The
  compiler carries a `default_channel_type = tcs:HttpChannel` and picks
  Entry/Exit candidates whose `tcs:channelType` matches. Generalizing
  to a new transport (Kafka, AMQP) becomes a catalog-only change plus
  new per-boundary config compilers — never a change to
  `BridgeTransportCompiler` itself.

## Open questions

- Exact vocabulary (`tcs:EntryBoundaryComponent`/`tcs:ExitBoundaryComponent`,
  `tcs:channelType`, `tcs:HttpChannel`, `tcs:endpoint`, `tcs:port`) not
  yet reconciled against `semantic model/README.md`.
- Whether `tcs:multiTenantContainer` is worth introducing now vs. waiting
  for a second framework with mixed multi-tenancy.
- Whether the new sw "unsupported multi-instance" shape should hardcode the
  `sw:` namespace prefix or be expressed generically from the start via a
  marker predicate.
- Per-channel `inputShape`/`outputShape` for `rdfc:Sdsify`'s branching
  outputs (pre-existing roadmap item) is untouched by this plan, though it
  touches the same channel model.
- **`BridgeTransportCompiler` naming** — current best suggestion, open to
  a better one; "Docker" was deliberately dropped since nothing about
  detection/wiring is Compose-specific.
- **Ambiguous catalog** — what should happen if a framework registers
  more than one Entry (or Exit) component for the same
  `tcs:channelType`? MVP: flag via SHACL, refuse to compile. v2: a
  catalog-level default marker (`tcs:defaultBoundary true` or similar)
  picking the canonical choice, or a pipeline-level override.
- **Insertion side-effect on `PipelineEnricher`** — inserted boundary
  steps arrive already channel-wired, so the enricher shouldn't need to
  re-run. Worth verifying with a test.

### Resolved

- **2026-08-19: Is `PipelineExtractor`'s catalog-narrowing compatible
  with auto-inserting a missing bridge component?** Resolved by
  splitting `PipelineExtractor` into an early `PipelineSeeder`
  (no narrowing at bootstrap) and a late `GraphReducer` (narrowing,
  running after `BridgeTransportCompiler`). No catalog-access API on
  individual compilers needed. The `GraphReducer` inherits
  `extract_pipeline()`'s existing shape-preservation and
  used-component catalog-membership preservation verbatim — pure
  code motion, no new logic.

## v2, deferred

With `tcs:channelType` matching, cross-framework generalization within
the *HTTP* transport is already automatic in the MVP — a new framework's
HTTP boundary components need only new catalog triples, no compiler
changes. What remains as v2:

- **Non-HTTP transports.** Kafka, AMQP, gRPC, filesystem, etc. Each
  needs a new `tcs:Channel` subclass, new per-boundary config
  compilers for the frameworks that support it, and (optionally) a
  runtime knob for `BridgeTransportCompiler.default_channel_type` so a
  pipeline can pick the preferred transport for a given cross-container
  hop. No change to `BridgeTransportCompiler`'s core algorithm.
- **Ambiguous catalog resolution.** See the corresponding open question
  above — needed only once a framework registers more than one Entry
  or Exit component for the same `tcs:channelType`.
