# Pipeline segments & cross-container bridges

See [`pipeline-segments-decisions.md`](pipeline-segments-decisions.md) for
rejected alternatives, open questions, and the session timeline.

## Motivation

Container assignment (`PipelineAssembler.describe_docker_container()`) is
keyed on catalog **component identity** — one container per component,
every `InstancePipelineComponent` specializing it shares that container.
Three consequences the current design doesn't handle:

- A pipeline switching frameworks more than once (LDIO → RDFC → LDIO →
  RDFC) puts multiple LDIO segments in one container; `LdioConfigCompiler`
  must keep them separate.
- Reusing a catalog component more than once must be safe for every
  framework, not just the multi-tenant ones. See
  [`pipeline generator/README.md`](../README.md#63-how-to-onboard-new-frameworks)'s
  container/multi-tenancy contract.
- Every cross-container hop needs a bridge component pair (Exit + Entry,
  e.g. `ldio:HttpOut` → `rdfc:HttpServer`) with matching transport
  configs — today hand-authored on both sides, unchecked.

## New vocabulary (semantic model)

Two boundary component classes plus a channel-transport classification:

```turtle
tcs:EntryBoundaryComponent rdfs:subClassOf tcs:PipelineComponent .
tcs:ExitBoundaryComponent  rdfs:subClassOf tcs:PipelineComponent .

# tcs:channelType is a metadata predicate on a boundary component; its
# object is a subclass of tcs:Channel (transport discriminator).
tcs:channelType    a rdf:Property ; rdfs:range rdfs:Class .
tcs:HttpChannel        rdfs:subClassOf tcs:Channel .
tcs:SparqlUpdateChannel rdfs:subClassOf tcs:HttpChannel .

# Transport metadata on a channel, populated at compile time by Entry
# config compilers and read by their paired Exit config compilers.
tcs:endpoint       rdfs:domain tcs:Channel ; rdfs:range xsd:string .
tcs:port           rdfs:domain tcs:Channel ; rdfs:range xsd:integer .
```

`tcs:SparqlUpdateChannel` is introduced now for correctness: RDF-Connect's
`rdfc:SPARQLIngest` (Exit) and semantic.works' `sw:mu-identifier` (Entry)
both speak HTTP but exchange SPARQL Update payloads, not arbitrary
JSON-LD — they can't be swapped with the generic
`rdfc:HttpOut`/`rdfc:HttpServer` boundaries. Marking them with the
subtype prevents `BridgeTransportCompiler` from confusing them at
matching time. MVP's `BridgeTransportCompiler.default_channel_type =
tcs:HttpChannel` deliberately skips `tcs:SparqlUpdateChannel` — the
demonstrator's RDFC → sw hop stays hand-authored for now, and
auto-insertion for SPARQL-update bridges is v2 work.

A component typed as either boundary class also entails
`tcs:PipelineComponent` under RDFS inference, so no separate
`a tcs:PipelineComponent` triple is needed. A component can carry both
types (a trivial one-step segment's sole component).

## Catalog typing

| Component | Boundary type | `tcs:channelType` |
| --- | --- | --- |
| `rdfc:HttpServer` | `tcs:EntryBoundaryComponent` | `tcs:HttpChannel` |
| `ldio:HttpIn` | `tcs:EntryBoundaryComponent` | `tcs:HttpChannel` |
| `rdfc:HttpOut` | `tcs:ExitBoundaryComponent` | `tcs:HttpChannel` |
| `ldio:HttpOut` | `tcs:ExitBoundaryComponent` | `tcs:HttpChannel` |
| `rdfc:SPARQLIngest` | `tcs:ExitBoundaryComponent` | `tcs:SparqlUpdateChannel` |
| `sw:mu-identifier` | `tcs:EntryBoundaryComponent` | `tcs:SparqlUpdateChannel` |

`rdfc:HttpFetch` and `ldio:HttpInPoller` are **not** boundaries — they
pull rather than accept, which doesn't fit a push-based
container-to-container hop.

Marking `sw:mu-identifier` explicitly (even though MVP
`BridgeTransportCompiler` doesn't process `tcs:SparqlUpdateChannel`)
matters for the SHACL side: without it, a future pipeline that models
the RDFC → sw hop as a channel would trigger
`CatalogMissingBridgeShape` incorrectly, since the compiler would find
no registered Entry on the sw side.

## Compile flow

`PipelineSeeder`, `ValidationReportCompiler`, and `DockerComposeCompiler`
are called explicitly by `PipelineGenerator` (not registry-discovered) so
their position is fixed. Everything in between stays in the registry
fixpoint loop; ordering emerges from each late compiler's `applies_to`
gating on a `dct:creator` provenance triple written by the immediately
preceding compiler(s).

```
PipelineSeeder                       (bootstrap; explicit call)
  → PipelineAssembler
    → PipelineEnricher
      → BridgeTransportCompiler
        → SegmentTagger
          → per-Entry config compilers (framework-specific, one per Entry component)
            → per-Exit config compilers (framework-specific, one per Exit component)
              → LdioConfigCompiler
                RdfcConfigCompiler
                sw file compilers    (existing; gated on boundary configs)
                → GraphReducer
                  → ValidationReportCompiler   (finalize; explicit call)
                    → DockerComposeCompiler    (finalize; explicit call)
```

## Compilers in detail

Each compiler's `applies_to` docstring is the authoritative trigger doc;
the summaries below are reading aids.

### `PipelineSeeder` (was: extraction half of `PipelineExtractor`)

Extracts the pipeline's own triples, seeds the `tcs:PipelineBuild`, names
typed blank nodes. **Does not narrow** — the full catalog stays visible so
`BridgeTransportCompiler` can reach not-yet-used components.

### `PipelineAssembler`

Unchanged. Mints one `tcs:DockerContainer` per catalog microservice;
assigns steps via `tcs:runs`.

### `PipelineEnricher`

Unchanged in scope from today. Expands `p-plan:isPrecededBy` into
`tcs:readsFrom` / `tcs:writesTo`; ensures every step has a
`tcs:PipelineConfig` slot. Segment tagging — previously proposed as an
extension of this compiler — lives in its own `SegmentTagger` (below),
running after `BridgeTransportCompiler` so inserted boundary steps are
visible to it.

### `BridgeTransportCompiler` (new)

**Trigger.** More than one `tcs:DockerContainer` in the build carries a
`tcs:runs` triple (i.e. the pipeline is spread across containers).

**Responsibility (only).** Insert missing boundary steps. It never
configures them and never writes transport metadata onto a channel —
those concerns belong to the per-boundary config compilers.

**Algorithm.** Per cross-container channel (a `tcs:Channel` whose writer
and reader steps run on different containers):

1. Check whether a step of the correct boundary type already sits on
   each side of the channel in the correct container. If both present,
   skip (idempotency — the demonstrator's hand-wired `demo:LdioForward`
   / `demo:HttpIngest` case).
2. Otherwise, look up catalog candidates for each missing side:
   - Exit: `tcs:ExitBoundaryComponent` whose `dct:requires` chain
     reaches the upstream container's microservice, and whose
     `tcs:channelType` matches `self.default_channel_type` (MVP:
     `tcs:HttpChannel`).
   - Entry: same for the downstream side.
3. Insert missing steps (`prov:specializationOf` the catalog component,
   `p-plan:isStepOfPlan` the current pipeline, `tcs:runs` on the correct
   container). Graph surgery on the channel wiring, reusing the original
   cross-container channel as the bridge:
   - The **original channel** stays typed as the cross-container
     transport (`a tcs:HttpChannel`), read only by the inserted Entry
     and written only by the inserted Exit.
   - A **fresh intra-container companion channel** is minted on each
     container's side; the original writer now writes to the
     upstream-side companion (which the Exit reads), and every original
     reader on the downstream side now reads from the downstream-side
     companion (which the Entry writes).

   This handles branching naturally: N readers of the same channel in
   the same downstream container all get rewired to the one
   downstream-side companion channel, served by one Entry.
4. Mark the (now-cross-container-only) original channel `a
   tcs:HttpChannel`.

**Non-boundary cross-container adjacency** — e.g. the pipeline author
wired `LdioTransformer → RdfcSDSify` directly across containers with no
boundary component between them — is treated identically to "missing
on both sides": the compiler auto-inserts an Exit + Entry pair from the
catalog, no error.

**Empty catalog** (no candidate for one side): flagged by
`CatalogMissingBridgeShape`.

**Ambiguous catalog** (multiple candidates per side): v2 (see decisions
doc).

**Channel topology out of MVP scope:** a channel with writers or
readers spanning more than two containers, or with writers in more than
one container, is flagged by `UnsupportedChannelTopologyShape` — not
auto-bridged. Ordinary branching (N readers in one downstream container)
is fully supported per step 3 above.

### `SegmentTagger` (new)

**Trigger.** `dct:creator tcs:BridgeTransportCompiler` present on the
build.

**Responsibility.** Tag every maximal run of steps within one container,
delimited by Entry- and Exit-typed steps, as one segment. Walks
`tcs:readsFrom` / `tcs:writesTo` from each Entry-marked step forward to
the next Exit-marked step (or container boundary), attaching
`tcs:segment :segment_N` to each step encountered. sw steps, with no
channel wiring, trivially form single-step segments.

Deliberately separated from `PipelineEnricher` so that boundary steps
inserted by `BridgeTransportCompiler` are visible at tagging time.

### Per-Entry config compilers (new; one per Entry component)

Examples: `RdfcHttpServerConfigCompiler`, `LdioHttpInConfigCompiler`.

**Trigger.** An `InstancePipelineComponent` specializes the compiler's
target Entry component AND has no `p-plan:hasInputVar` yet.

**Responsibility.** Attach `p-plan:hasInputVar tcs:PipelineConfig` with
sensible defaults, then write the resulting transport metadata to the
shared cross-container channel the step writes to:

```turtle
:bridge_channel_N a tcs:Channel, tcs:HttpChannel ;
    tcs:endpoint "http://<container-hostname>:<port><path>" ;
    tcs:port <port> .
```

Container hostname comes from `_lookup_container_service_names`
(factored from `DockerComposeCompiler` into `compilers/utils.py` as
part of this refactor).

**Port allocation.** Framework-fixed default (e.g. `9000` for
`rdfc:HttpServer`, `8080` for `ldio:HttpIn`) with collision bump: if
the default is already taken on the same container by another Entry of
the same component (multi-segment case), increment by one until free.
Output port numbers stay human-readable this way instead of hashed.
Path is derived from the segment name (or the pipeline name for a
single-segment pipeline).

### Per-Exit config compilers (new; one per Exit component)

Examples: `LdioHttpOutConfigCompiler`, `RdfcHttpOutConfigCompiler`.

**Trigger.** An `InstancePipelineComponent` specializes the compiler's
target Exit component AND the channel it reads from carries
`tcs:endpoint` — i.e. the paired Entry compiler has already run.

**Responsibility.** Attach `p-plan:hasInputVar tcs:PipelineConfig` with
the framework-specific endpoint key populated verbatim from the channel's
`tcs:endpoint`.

### Framework config-file compilers (existing)

`LdioConfigCompiler`, `RdfcConfigCompiler`, sw file compilers. Existing
behaviour, but `applies_to` gains a `dct:creator` gate on any
per-boundary config compiler relevant to the same container having run
first — so the emitted config file sees fully-configured boundary steps.

`LdioConfigCompiler` additionally emits one file per segment (Pattern A2
directory-scan, `ldio/pipelines/segment_N.yml`), reading the segment
tags attached by `SegmentTagger`.

### `GraphReducer` (new; narrowing half of today's `PipelineExtractor`)

**Trigger.** `dct:creator tcs:BridgeTransportCompiler` present, or (for
single-container pipelines where `BridgeTransportCompiler.applies_to`
returned False) the graph has otherwise settled.

**Responsibility.** Narrow the build graph to just what's referenced by
the pipeline. Same three-step logic as today's
`PipelineExtractor.extract_pipeline()`, just moved: traverse from the
pipeline definition, re-add shape subgraphs, re-add catalog-membership
assertions for retained components.

### `ValidationReportCompiler` (finalize; explicit call)

Called by `PipelineGenerator` after the fixpoint loop terminates, before
`DockerComposeCompiler`. Not registry-triggered. `tcs:isFinishing`
machinery is retired.

### `DockerComposeCompiler` (finalize; explicit call)

Called last, explicitly. Also no longer registry-triggered.

## SHACL changes

- **`LdioSingularStepShape`** — rescoped from per-`PipelineDefinition` to
  per-segment. The "≤1 `Input`, ≤1 `Adapter`" constraint applies within a
  segment.
- **`SwSingleInstanceShape`** (new) — flags >1
  `InstancePipelineComponent` specializing the same `sw:`-namespace
  component. semantic.works services are single-tenant.
- **`BoundaryChannelTypeShape`** (new) — every
  `tcs:EntryBoundaryComponent` / `tcs:ExitBoundaryComponent` must carry
  exactly one `tcs:channelType`.
- **`CatalogMissingBridgeShape`** (new) — flags a cross-container
  channel whose upstream/downstream frameworks have no boundary
  component registered for the required `tcs:channelType`. Scope: *formal
  presence only* — the shape checks that container-crossing is realised
  via bridge components, nothing about their configuration or transport
  implementation details (those are the boundary config compilers' job
  and would themselves be caught later if broken).
- **`UnsupportedChannelTopologyShape`** (new) — flags topologies
  `BridgeTransportCompiler` deliberately doesn't handle: a channel with
  writers in more than one container, or with readers spanning more than
  two containers total (multi-container fan-in / fan-out). Ordinary
  branching (N readers in one downstream container) stays supported.

## Inference rules

[`data/inference_rules.yaml`](../data/inference_rules.yaml) already
types every channel (from any use of `tcs:readsFrom`/`tcs:writesTo`)
and every step (from `p-plan:isStepOfPlan`). The new vocabulary needs
two additional entailments so pre-compile SHACL shapes see the same
graph the compiler will produce:

1. **Boundary-typing entailment.** RDFS handles
   `?comp a tcs:EntryBoundaryComponent ⇒ ?comp a tcs:PipelineComponent`
   (and the Exit counterpart) via plain subclass inference — no explicit
   rule needed, provided the shapes graph carries the
   `rdfs:subClassOf` triples and `GraphReader.validate(inference='rdfs')`
   is used (which is already the case). Same for
   `tcs:SparqlUpdateChannel rdfs:subClassOf tcs:HttpChannel`.
2. **Channel-type propagation.** When a step specializes a boundary
   component, the channel that step writes to (Entry) or reads from
   (Exit) inherits the boundary's `tcs:channelType`. Explicit rule
   (two variants):
   ```yaml
   - construct: |
       ?ch a ?channelType .
     where: |
       ?step tcs:writesTo ?ch ;
             prov:specializationOf ?comp .
       ?comp a tcs:EntryBoundaryComponent ;
             tcs:channelType ?channelType .
   - construct: |
       ?ch a ?channelType .
     where: |
       ?step tcs:readsFrom ?ch ;
             prov:specializationOf ?comp .
       ?comp a tcs:ExitBoundaryComponent ;
             tcs:channelType ?channelType .
   ```
   Lets `BoundaryChannelTypeShape` and other channel-type-aware shapes
   run pre-compile without waiting for `BridgeTransportCompiler` to
   materialize the `tcs:HttpChannel` triple itself.

Both `inference_rules.yaml`'s `context:` prefix block (already contains
`tcs:`) and its `rules:` list gain updates as part of this
implementation — no new prefixes are needed, since every new predicate
and class lives in `tcs:`.
