# Retiring `tcs:Channel`, `p-plan:isPrecededBy`, `tcs:readsFrom` / `tcs:writesTo`

> **Status:** C0, C1, C2 and C3 are done (2026-09-22), verified by compiling
> every shipped pipeline definition (all five conform) and by the full pytest
> suite (2026-09-23): 254 passed, 1 pre-existing unrelated failure (a stale
> committed catalog snapshot, needs a harvester re-run). The 31 failures the
> suite initially turned up were all stale tests still written against the
> retired `tcs:readsFrom`/`tcs:writesTo`/`tcs:Channel` vocabulary and the
> deleted `SemanticModelMapper` compiler — no real compiler gaps — and have
> been rewritten against `tcs:Connection`. C4 not started — owned by the
> slice-2 plan.
>
> **What this is.** A migration of the toolchain's wiring vocabulary onto a
> single term, `tcs:Connection`. It is framework-agnostic: it changes what a
> pipeline author writes and what the build graph holds, and hands every
> framework-specific translation to
> [`config-shape-split-slice-2-plan.md`](config-shape-split-slice-2-plan.md).
>
> **Read first, in this order:** this file, then §4a and the open-decision
> section of the slice-2 plan. The parent
> [`config-shape-split-plan.md`](config-shape-split-plan.md) is background — its
> slice 3 is largely absorbed here (§12).
>
> **Conventions.** **[F]** marks a fact checked against the code on 2026-09-22;
> take it as given rather than re-deriving it, but re-check a line number before
> relying on it. **[D1]**–**[D8]** are settled decisions and **[O1]**–**[O3]** are the
> only open ones, both numbered so they can be cited. §9 holds the open list.
>
> **Working in this repo.** Python is
> `~\anaconda3\envs\pipeline_generator\python.exe`; never `pip install`. Tests
> are gated — see the `run-pipeline-generator-tests` skill and ask before running
> them. Load `dishacled-context` for internals and `semantic-model-sync` before
> touching the ontology.

---

## 1. Why

A pipeline's topology is expressible three ways today, and the generator has to
reconcile all of them:

| Vocabulary | Who writes it | What it can say |
| --- | --- | --- |
| `p-plan:isPrecededBy` | the author, 29× across 5 definitions | "B comes after A" — cannot say *which* output of A |
| `tcs:readsFrom` / `tcs:writesTo` + `tcs:Channel` | the author (34 triples), or `PipelineEnricher` / `SemanticModelMapper` synthesising from the other two | a step-to-channel edge; a channel is 1 writer to N readers |
| `tcs:Connection` (`tcs:from` / `tcs:to`) | the author, once, in one definition | a 1:1 edge between two steps |

Three spellings of one fact is the cost. `PipelineEnricher.synthesize_channels`
and `data/inference_rules/inference_rules.yaml` carry **two independent
implementations of the same three-case synthesis logic** — a duplication
[`tests/EDGE_CASES.md:31`](../pipeline%20generator/tests/EDGE_CASES.md) already
flags as a standing maintenance risk. And the config-shape split needs one
vocabulary to write its translation queries against.

**[D1] End state: `tcs:Connection` is the only wiring vocabulary.** The
Connection node *is* the edge. `p-plan:isPrecededBy`, `tcs:Channel`,
`tcs:readsFrom` and `tcs:writesTo` are removed. `tcs:from` and `tcs:to` are the
only predicates left; a step's edges are reached by walking them backwards
(`^tcs:from`, `^tcs:to`).

```turtle
# before
demo:ApiPoll  tcs:writesTo :channel_0 .
demo:Parse    tcs:readsFrom :channel_0 .
:channel_0    a tcs:Channel .

# after
[] a tcs:Connection ; tcs:from demo:ApiPoll ; tcs:to demo:Parse .
```

---

## 2. The model

### [D2] A Connection is an edge: exactly one `tcs:from`, exactly one `tcs:to`

One origin, one destination. Anything wider reads badly at authoring time, which
is the audience this vocabulary exists for.

`tcs:ConnectionCardinalityShape` is **kept and rewritten, not removed**. It stops
being `TEMPORARY` and stops being a restriction awaiting branching support; it
becomes the definition — exactly one `tcs:from`, exactly one `tcs:to`, both
pointing at a `tcs:InstancePipelineComponent`. Two edits:

- Its `sh:message` currently directs authors to `tcs:writesTo` as the escape
  hatch for branching. That hatch is what this migration removes.
- It absorbs `SemanticModelMapper.validate_connection_wellformedness`'s
  endpoint-typing check, whose wording is better than most and should be kept.

**[F]** The shape already expresses both directions with `sh:inversePath
tcs:from` / `tcs:to`, so the inverse-path idiom this migration leans on is
already proven in the shape file.

**Branching is several Connections, not one wider Connection.** A producer
feeding two consumers of one stream, and a producer with two distinct output
ports, are both "two Connections sharing a `tcs:from`". At the semantic-model
level they are the same shape; what distinguishes them is framework vocabulary
(§7).

### [D3] `tcs:writerPath` is added; there is no `tcs:readerPath`

Once an edge is 1:1, two Connections leaving one step are indistinguishable, so
something on the Connection must say which framework port it is. This plan adds
the term and the shape permitting it; it does **not** implement the injection
that consumes it (§7). A Connection carrying no hint is the unambiguous case and
needs none.

**Its value space is framework-specific and deliberately heterogeneous:** an IRI
for RDF-Connect (`rdfc:output`, a config predicate), a string for NiFi
(`"splits"`, a relationship name). **Its shape must not constrain
`sh:nodeKind`.**

**No `tcs:readerPath`. [F]** Every component in the catalog declares exactly one
reader path — `rdfc:reader`, `rdfc:input`, `rdfc:memberStream`, `rdfc:incoming`
— so a consumer is never ambiguous about which port an incoming edge lands on,
and a reader-side hint would have nothing to disambiguate. It is not added for
symmetry: an unused term still has to be modelled, shaped, documented and kept in
sync. A multi-input processor can introduce it when one exists.

### [D4] `nifi:route` is replaced by `tcs:writerPath`

**[F]** `nifi:route` is already an edge annotation, and a fairly literal one — a
blank node in the *writer step's config* pairing a channel with a relationship,
because there was nowhere else to put per-edge data:

```turtle
# before, inside demo_n:SplitText's tcs:embedded config
nifi:route [ nifi:channel :nifi_channel_split         ; nifi:selectedRelationship "splits"  ] ;
nifi:route [ nifi:channel :nifi_channel_split_failure ; nifi:selectedRelationship "failure" ] .

# after — the Connection already names both endpoints
[] a tcs:Connection ; tcs:from demo_n:SplitText ; tcs:to demo_n:Downstream ;
   tcs:writerPath "splits" .
```

**Removed here**, because model-level: the `nifi:route` construct, the
`nifi:channel` / `nifi:selectedRelationship` terms, and `tcs:NifiRouteShape`.
That shape's `sh:sparql` check was "route references a channel its owner step
does not write to" — on a Connection that is **structurally impossible**, so a
validation rule is replaced by a modelling invariant.

**Not removed here:** NiFi's *consumption* of it (§7).

### [D5] Connections are blank by default

An edge rarely needs a name. An author *may* give a Connection an IRI, and then
that name surfaces in emitted files and validation reports; otherwise it gets a
generated `:connection_N`, as an unnamed channel gets `:channel_N` today.

Migration therefore **renames** most edges, since today's channel names mostly
came from authors and tomorrow's Connections mostly will not. Preserving names to
keep diffs quiet would mean authoring the definitions in a form they would have
to leave again. What must be preserved is the **topology**, not the names (§11).
[D8] is the one scoped exception.

### [D6] `SemanticModelMapper` is deleted

**[F]** It does one job — turn `tcs:Connection` into channel wiring — and every
step of it dies except one:

| Method | Fate |
| --- | --- |
| `lookup_target_pipeline`, `list_connections` | gone — scoping bookkeeping for work that no longer happens |
| `resolve_connection_channels` | gone — no channels to resolve |
| `attach_channel_wiring` | gone — nothing to attach |
| `detach_connection_nodes` | gone — Connections *are* the topology now |
| `validate_connection_wellformedness` | survives, as SHACL ([D2]) |

**Behaviour change, taken deliberately:** the compiler *raises* today; a SHACL
shape *reports*. A malformed Connection becomes a validation violation rather
than an exception — consistent with how every other modelling error here is
surfaced, but a broken pipeline compiles further before complaining.

### [D7] `GraphReducer` gains one argument

**[F]** `reduce_to_pipeline` narrows by walking forward from the build, and a
`tcs:Connection` is the *subject* pointing at its steps — nothing reaches it
going forward. (That is exactly why `detach_connection_nodes` exists today.) No
"floats independently" rescue block is needed, of the kind the reducer keeps for
the `tcs:CompilationRequest`, the `sh:NodeShape`s and the `tcs:Catalog`
assertions. The traversal already takes inverse predicates, and already uses that
idiom to pull in the steps themselves:

```python
# graph_reducer.py:155
self.output_reader.traverse(self.build_id, against="p-plan:isStepOfPlan")
#                                          ["p-plan:isStepOfPlan", "tcs:from", "tcs:to"]
```

**[F] Verified against `GraphReader.traverse` directly**, including scoping: a
Connection belonging to another plan is *not* pulled in, because it is only
reachable from a step outside this pipeline. So `SemanticModelMapper`'s pipeline
scoping does not relocate anywhere — it falls out of the traversal.

---

## 3. The target authored form

**The pipeline definitions are an output of this plan, not a constraint on it.**
Changing the semantic model makes every shipped definition stale; they are the
first thing the change invalidates, not evidence of what authors must keep being
able to write.

The hardest case in the repo is `rdfc:Sdsify`, whose two outputs feed two
different consumers. **Before**
([`pipeline_definition.ttl`](../pipeline%20generator/data/pipelines/pipeline_definition.ttl)
`:281`, `:323-344`, `:367`, `:404-416`):

```turtle
demo:sdsMeasurements     a rdfc:Reader, rdfc:Writer .          # :281 — hand-typed
demo:sdsMeasurementsMeta a rdfc:Reader, rdfc:Writer .

demo:SdsifyMeasurements
    p-plan:isPrecededBy demo:JsonLdToRdf ;                     # read side
    p-plan:hasInputVar [ tcs:embedded [
        rdfc:output         demo:sdsMeasurements ;             # write side, in the config
        rdfc:metadataOutput demo:sdsMeasurementsMeta ;
        rdfc:typeFilter     sosa:Observation ] ] ;
    tcs:writesTo demo:sdsMeasurements , demo:sdsMeasurementsMeta .

demo:ThresholdMonitor    tcs:readsFrom demo:sdsMeasurements .
demo:LogMeasurementsMeta tcs:readsFrom demo:sdsMeasurementsMeta .
```

**After:**

```turtle
demo:SdsifyMeasurements
    p-plan:hasInputVar [ tcs:embedded [
        rdfc:typeFilter sosa:Observation ] ] .                 # no channel keys

[] a tcs:Connection ; tcs:from demo:JsonLdToRdf        ; tcs:to demo:SdsifyMeasurements .
[] a tcs:Connection ; tcs:from demo:SdsifyMeasurements ; tcs:to demo:ThresholdMonitor ;
   tcs:writerPath rdfc:output .
[] a tcs:Connection ; tcs:from demo:SdsifyMeasurements ; tcs:to demo:LogMeasurementsMeta ;
   tcs:writerPath rdfc:metadataOutput .
```

Three things leave: `tcs:writesTo` / `tcs:readsFrom`, because this plan removes
them; the `rdfc:output` / `rdfc:metadataOutput` keys, because they are
compiler-facing and get injected (the slice-2 plan's subject); and the hand-typed
`a rdfc:Reader, rdfc:Writer` lines, because the framework translation types
Connections.

---

## 4. Scope

**[F]** Counted with rdflib, not grep.

### Data

| | Size |
| --- | --- |
| `p-plan:isPrecededBy` edges | 29, across 5 definitions |
| Explicit `tcs:readsFrom` / `tcs:writesTo` triples | 34, across 3 definitions (≈17 edges — each edge is a `writesTo` **and** a `readsFrom`) |
| **Connections to author in total** | **≈46**, from 63 triples across 6 definitions |
| …steps with several ports among them | 8 — `Sdsify` ×2 per RDFC definition, plus 4 NiFi steps including a fan-in funnel |
| Compiler-facing channel keys to remove from configs | 8 — `rdfc:output` + `rdfc:metadataOutput` on two `Sdsify` steps, in each of the two RDFC definitions |
| Hand-typed `a rdfc:Reader, rdfc:Writer` declarations | 8, across the two RDFC definitions, plus the comment block at `:273-280` explaining them |
| `nifi:route` blank nodes to fold into `tcs:writerPath` | 4, in `pipeline_definition_nifi.ttl` |
| Inference-rule files | `rdfc_inference_rules.yaml` deleted entirely (§7); `inference_rules.yaml` loses its `isPrecededBy` rules |
| Catalogs naming `tcs:Channel` | `catalog-rdfc.ttl`, `catalog-rdfc-manual.ttl`, `catalog-sw.ttl` |

### Code

| File | Sites | Role |
| --- | --- | --- |
| `core/bridge_transport_compiler.py` | 18 | **rewrites** wiring — C2 |
| `core/semantic_model_mapper.py` | 12 | **deleted** — C2 |
| `core/validation_report_compiler.py` | 9 | throughput matching — switched off, §8 |
| `core/pipeline_enricher.py` | 6 | `isPrecededBy` synthesis — **deleted**, C1 |
| `core/graph_reducer.py` | 1 | one `against=` argument — C2 |
| 12 other files | 27 total | mechanical direction flips — C2 |

### Application-profile shapes — 17 of 36 touch channel vocabulary

| Fate | Count | Which |
| --- | --- | --- |
| Mechanical direction flip | 9 | — |
| Redesigned over Connection groups | 2 | `tcs:UnsupportedChannelTopologyShape`, `tcs:NifiChannelTopologyShape` (§5) |
| Rewritten | 1 | `tcs:ConnectionCardinalityShape` ([D2]) |
| Deleted here | 2 | `tcs:NifiRouteShape` ([D4]), `tcs:RdfcStepChannelWiringShape` (§7) |
| Deleted by the slice-2 plan — **not work here** | 2 | `tcs:RdfcMandatory{Reader,Writer}WiringShape` |
| Untouched despite the name | 1 | `tcs:BoundaryChannelTypeShape` (§5) |

---

## 5. The two shapes that are not a direction flip

**[F]** `tcs:UnsupportedChannelTopologyShape` and `tcs:NifiChannelTopologyShape`
do not merely *name* the channel predicates, they constrain **channel-level
topology**:

- "Channel `{?this}` has writers in more than one container — multi-container
  fan-in is not auto-bridged."
- "Channel `{?this}` has readers spanning more than two containers total."
- "NiFi channel `{?this}` has more than one NiFi writer; explicit fan-in is not
  supported yet."

Each is a statement about a node with N readers and N writers. Under a 1:1
Connection each is **vacuously true**, so a mechanical rewrite would switch three
real constraints off while leaving them looking alive.

The constraints do not go away. They must be re-expressed over **groups of
Connections sharing an endpoint** — a genuinely different query, not a flip. That
is slice **C3**.

**[F] `tcs:BoundaryChannelTypeShape` is not one of them**, despite the name: it
targets `tcs:EntryBoundaryComponent` / `tcs:ExitBoundaryComponent` and bounds
`tcs:channelType` on a *component*. Nothing to do with channel arity; no work
here.

---

## 6. Slices

### C0 — switch the throughput half off ✅ *done, 2026-09-22*

The five throughput calls are commented out of
`ValidationReportCompiler.compile()`, leaving `normalize_config_shapes`,
`validate_normal_shapes` and `generate_validation_report`. §8 has the detail and
the supporting change it needed.

### C1 — authoring migration, still running on channels ✅ *done, 2026-09-22*

The definitions move to `tcs:Connection` **before** the code changes vocabulary.
This is the half that genuinely stages, one file at a time.

1. Add `tcs:writerPath` and the shape permitting it ([D3]).
2. Teach `SemanticModelMapper` two things, both of which are the end state
   arriving early: **use the Connection's own IRI as the channel** when the
   Connection is named, and **drop the `Counter` fan-out rejection**, minting one
   channel per Connection so multi-port steps are expressible.
3. Rewrite the ≈46 edges across the 6 definitions into the target form of §3.
4. Delete `p-plan:isPrecededBy` authoring, `PipelineEnricher.synthesize_channels`,
   its `inference_rules.yaml` twin, and the `isPrecededBy` clauses in the profile
   shapes. `tests/EDGE_CASES.md:21-31` and `tests/test_channel_synthesis.py`
   retire here.

**[D8] The eight framework channel keys and the four `nifi:route` nodes stay
until C4.** Removing them is behavioural, not a re-spelling: until the framework
side consumes `tcs:writerPath`, `describe_channel_wiring` still declines to
inject an ambiguous writer (Sdsify would emit unwired) and `_connection_plans`
still reads the relationship out of the config (NiFi connections would lose their
`selectedRelationship`).

So in C1 those eight Connections are **named after the channels they replace** —
`demo:sdsMeasurements a tcs:Connection ; …` — so the retained config keys still
resolve against them. This is the one exception to [D5], it is scoped to those
eight edges, and C4 removes both the keys and the need for the names.

**Done when:** every definition parses, all pipelines compile, topology is
equivalent per §11, and no definition mentions `p-plan:isPrecededBy`.

### C2 — the vocabulary flip *(atomic)* ✅ *done, 2026-09-22*

**This cannot be staged.** The moment `tcs:readsFrom` / `tcs:writesTo` stop being
written, every reader must already have flipped. Materialising the two as
inverses of `tcs:from` / `tcs:to` to buy a gentler transition is a dead end: it
adds to every minting site the redundancy this migration exists to remove, and
leaves the graph quietly inconsistent wherever a site forgets.

In one commit:

- delete `SemanticModelMapper` ([D6]);
- `GraphReducer`'s `against=` argument ([D7]);
- `BridgeTransportCompiler` splits Connections instead of repointing channels;
- the 27 consumer direction flips;
- the 9 mechanical profile shapes;
- `tcs:Channel` gone from the three catalogs;
- `RdfcConfigCompiler.describe_channels` and NiFi's equivalent **mechanically
  retargeted** — type Connections where they typed channels — so the build keeps
  working. Designing that properly is C4.

**Three deletions rather than rewrites** (§7): `tcs:RdfcStepChannelWiringShape`
and both `tcs:derived*` inference rules, which lose their input entirely. **[F]
That is the whole of `rdfc_inference_rules.yaml`** — it holds those two rules and
nothing else — so the file goes, with its seven references:
`DEFAULT_INFERENCE_FILES` in
[`pipeline_generator.py:98`](../pipeline%20generator/src/compilers/pipeline_generator.py)
and [`pipeline_validator.py:75`](../pipeline%20generator/src/compilers/pipeline_validator.py),
`tests/testing_helpers.py:71`, `tests/test_pipeline_topology.py` (which exists to
test them), `semantics-2026-demo/_build_demo_catalog.py:42`,
`src/demo_fietsstallingen.ipynb`, and
[`README.md:529-535`](../pipeline%20generator/README.md) — itself a description
of the two-places problem this migration removes, so rewrite rather than delete.

**Expect a rename, not a no-op:** edge IRIs move to `:connection_N`, and those
names appear in emitted configs and in `segment_N.yml`-style filenames.
Bridge-minted edges rename the same way.

**Done when:** no `tcs:Channel`, `tcs:readsFrom` or `tcs:writesTo` remains in
`src/` or `data/`, all pipelines compile, and topology is equivalent per §11.
**Verified** — only comment/prose mentions remain, and every shipped pipeline
(`demo:DishacledPipeline`, `demo_ab:AutoBridgedPipeline`,
`demo_ln:LdioNifiBridgePipeline`, `demo_nl:NifiLdioBridgePipeline`,
`:DemonstratorPipeline`) compiles and conforms.

### C3 — the two topology shapes, over Connection groups ✅ *done, 2026-09-22*

Per §5. Lands **with or immediately after C2** — leaving the gap open is what
makes the vacuity dangerous.

**Done when:** each rewritten shape fires on a constructed violation (§11.3).
`tcs:UnsupportedChannelTopologyShape` is re-expressed over Connection groups
sharing an endpoint, per §5; `demo_ab:AutoBridgedPipeline` (the shipped pipeline
whose cross-container topology exercises it) conforms with it in place.
`tcs:NifiChannelTopologyShape`'s fan-in/fan-out blocks were **removed**, not
re-expressed — see [O2]'s resolution in §9 for why the check they encoded no
longer corresponds to anything the compiler or NiFi enforces; its self-loop
check, a genuine and unrelated constraint, was kept.

### C4 — framework translation → the slice-2 plan

Not work owned here; §7 says what is handed over. C4 is also what releases [D8].

---

## 7. What this plan hands to the framework plans

**Everything framework-specific is out of scope here**, RDF-Connect's and NiFi's
alike. Both are owned by
[`config-shape-split-slice-2-plan.md`](config-shape-split-slice-2-plan.md), which
widens beyond RDF-Connect for exactly this reason.

What is handed over is a **uniform shape of input, not a uniform rule**: several
Connections may share a `tcs:from`, each optionally carrying a `tcs:writerPath`.
What that means in emitted output differs per framework and is deliberately not
decided here —

- **RDF-Connect:** Connections sharing a `tcs:from` *and* a path are one channel
  node referenced by several readers; different paths are separate nodes. So
  channel identity is the pair *(producing step, path)*, not the Connection.
- **NiFi:** there is no shared node to find. Two Connections on the same
  relationship are two `flow.json` connections, and `tcs:writerPath` supplies
  each one's `selectedRelationship`. What it needs is `_connection_plans` reading
  `tcs:writerPath` off the Connection instead of `nifi:route` out of the config
  ([`nifi/config_compiler.py:589-620`](../pipeline%20generator/src/compilers/nifi/config_compiler.py)),
  and the property-table `FILTER` at `:758` dropped with it.

Generalising those into one rule would invent an abstraction neither framework
asked for.

### The reverse-direction check is deleted, not ported

**[F]** `tcs:RdfcStepChannelWiringShape` exists because the topology was stated
twice and could disagree: the `tcs:derived*` inference rules read a channel key
out of the **authored** config (`p-plan:hasInputVar/tcs:embedded`), and the shape
asserted that everything derived was also declared. After C1 no authored config
carries a channel key — they are compiler-facing and injected from the
Connections — so the rules derive nothing and the shape passes vacuously on every
pipeline.

It is not weakened, it is **structurally redundant**: the config is now
*generated from* the annotation, so the two cannot disagree.

**Be precise about why, so nobody ports the rules into a query.** The rules run
config → semantic model, for *checking*; the slice-2 translation queries run
semantic model → config, for *generating*. Inverse directions, not the same
computation. What the queries remove is not the rules' work but their *reason*:
two independent statements of one topology, which could drift. With one source of
truth there is nothing left to cross-check.

---

## 8. Throughput shape matching is off for the duration

**Switched off, and rewritten under its own plan later.** Not designed here.

**[F]** Five methods repeat the same channel discovery —
`gather_throughput_shapes`, `normalize_passthrough_shapes`,
`fill_missing_shapes`, `list_shapes_to_match`, `_order_instances_by_dataflow` —
every one selecting on `tcs:readsFrom` / `tcs:writesTo`; and `_attach_shape`
anchors its CONSTRUCT on the `tcs:Channel` *type*. Removing the predicates and
the type takes out both anchors, so this is a rewrite, not an adjustment.

**What is done instead:** those five calls are commented out of `compile()`. The
compiler stays in both presets and the emitted report keeps its SHACL half; only
the per-channel `tcs:ThroughputMatchResult` block disappears.

**[F] One supporting change this needed.** `self.throughput_matches` is assigned
nowhere but inside `validate_throughput_shapes` — no `__init__` default, no class
attribute — while `generate_validation_report` iterates it, so commenting the
calls out raised `AttributeError`. Fixed by defaulting it to an empty `DataFrame`
in `__init__`.

**The rewrite must also settle what validator mode validates at all. [F]**
`PipelineValidator` emits no files, so the contract it can hold an author to is
the **user-facing** config shape; checking compiler-facing shapes there asks about
a document the preset never produces. That over-reach is invisible today only
because the eight hand-written `a rdfc:Reader, rdfc:Writer` triples happen to
satisfy it — `RdfcConfigCompiler` is **not in `PipelineValidatorConfig`**. Once
those triples go, a compiler-facing `sh:class rdfc:Reader` check in validator mode
starts failing on something the author cannot fix. **That is the signal the role
filter is missing, not a regression from C1.**

Three further questions for the rewrite: **what the check is a check *of*** once
edges are 1:1 (`tcs:ThroughputMatchResult` is emitted per channel into the
committed report); **whether the five discovery sites stay five**, since
`^tcs:from` / `^tcs:to` off one helper would collapse them; and **what surfaces in
the report**, given `_unblank_synthetic_shape_ids` re-blanks `:nodeshape_N` /
`:emptyshape_N` but would now meet `:connection_N`.

---

## 9. Open decisions

Everything else in this plan is settled. Each of these blocks the slice named.

**[O1] — `pipeline_definition_fietsstallingen.revised.ttl` — resolved, migrated
with C1 (2026-09-22).** Its 9 edges are now `tcs:Connection`s. The notebook
itself (`src/demo_fietsstallingen.ipynb`) could not be run end-to-end as-is:
cell 3 hand-rolls `GraphReader.infer().validate()`, which — per README
§4.7.1 — skips `SemanticModelMapper` and so sees an unmapped Connection; two
`RdfcMandatory{Reader,Writer}WiringShape` violations on
`fs:Skolemize`/`fs:SparqlIngest`/`fs:MemberIngest` are the result, and are an
artifact of that wrong entry point, not a real defect (a separate pre-existing
bug, unrelated to this migration: cell 5 also calls `PipelineGenerator` with a
second positional argument the current single-argument signature doesn't
accept). Verified instead via `PipelineValidator` (which does run the mapper
first) and via direct `PipelineGenerator` compilation, both against the
migrated file: both `fs:PublisherPipeline` and `fs:ConsumerPipeline` conform
and compile with every edge intact. The notebook's cell 3 should switch to
`PipelineValidator` and cell 5 to the current `PipelineGenerator` signature —
left for whoever next touches that notebook, since both are pre-existing and
independent of this plan's scope.

**[O2] — resolved (2026-09-22).** `tcs:UnsupportedChannelTopologyShape` is
re-expressed over groups of Connections sharing an endpoint, per §5, and
`demo_ab:AutoBridgedPipeline` conforms with it in place.

`tcs:NifiChannelTopologyShape`'s fan-in/fan-out blocks turned out not to need
re-expressing at all — they were **removed**. The mechanical carry-over ("channel
has >1 writer" → "step has >1 distinct incoming Connection") re-created a check
whose original justification no longer held: the old shape guarded against
`NifiConfigCompiler.plan_connections` grouping rows from *different writers*
together when they shared one multi-writer `tcs:Channel` node — an implicit
Cartesian product of relationships. Under `tcs:Connection` that hazard is
structurally gone, because `plan_connections` groups by `(source, destination,
channel)` where `channel` is now the Connection's own IRI, always unique to one
writer/reader pair; verified directly by calling `plan_connections` against a
synthetic three-writer fan-in (`:SplitText`, `:ExecuteGroovyScript`,
`:CreateVersionObjectProcessor` all feeding `:FailureFunnel`'s `"failure"`
relationship, mirroring `pipeline_definition_nifi.ttl`) and confirming each
writer's relationship stayed attached to its own Connection. Real NiFi has no
fan-in/fan-out restriction either — any destination can have multiple incoming
connections; funnels are a UI convenience for it, not a capability boundary — so
the fix was to delete the two blocks rather than narrow them to exclude funnels.
The shape's self-loop check, a genuine and unrelated compiler limitation, was
kept. `:DemonstratorPipeline` (the shipped NiFi pipeline, which trips exactly
this pattern at `:FailureFunnel`) now conforms again, matching its pre-migration
state.

**[O3] — whether NiFi's translation can be a `tcs:configTranslation` query at all**
(owned by the slice-2 plan; blocks C4). Its output is not a step's config but an
entry in a *connection table*, which no per-component config query produces.
*Recommendation:* expect NiFi to keep compiler code and unify only its *input*
vocabulary — still the goal, since the authoring surface becomes the same for
both frameworks.

---

## 10. Risks

| Risk | Why | Mitigation |
| --- | --- | --- |
| **C2 is one large atomic commit** | ~27 consumer edits, 3 rewritten compilers and 9 shapes at once. | C1 takes the data migration out of it; C0 takes the report compiler out. What remains is mechanical apart from bridging — do bridging as its own reviewable step *within* the commit. |
| **Shapes go vacuously true** | Two topology shapes constrain N-reader/N-writer channels that 1:1 Connections cannot have (§5); `tcs:RdfcStepChannelWiringShape` loses its input entirely (§7). A mechanical rewrite leaves all three alive-looking and checking nothing. | C3 for the two that survive, deletion for the third. Assert the rewritten shapes fire on a constructed violation (§11.3), not `conforms: true`. |
| **`GraphReducer` silently drops Connections** | Forward narrowing cannot reach a Connection; the failure mode is a build that loses its topology rather than one that errors. | The one-line `against=` change — plus an explicit test that a Connection survives narrowing and another plan's does not. The fix is trivial; noticing it was needed would not be. |
| **Bridging regressions are silent** | A mis-split Connection drops a step from a segment or emits an unwired config, and the build still succeeds. | `pipeline_definition_autobridge.ttl` and the NiFi/LDIO pair are the gate; compare emitted files, not just `conforms`. |
| **Throughput checking is off throughout** | A shape mismatch between two steps goes unnoticed until the rewrite lands. | Accepted and bounded: it validates a *pipeline definition*, not the generator, and the SHACL half — which catches migration damage — keeps running. Do not let the window stay open past C4. |
| **Validation moves from raising to reporting** | A malformed `tcs:Connection` stops failing the compile ([D6]). | Deliberate; carry the mapper's wording into the shape's message. |

---

## 11. Verification

Project environment only (`~\anaconda3\envs\pipeline_generator\python.exe`), and
the full suite only with explicit permission per the
`run-pipeline-generator-tests` skill.

**The invariant is topology equivalence.** Every slice through C2 changes how the
graph *spells* an edge, never which steps are connected or through which
framework port. Concretely, for a generated project: the set of *(producer step,
framework port, consumer step)* triples recovered from the emitted files is
unchanged, and so is the set of files.

**Byte-identity is an instrument for checking that, not the requirement.** Where a
slice happens not to rename anything, `diff -r` answers the question for free.
Where it renames — C1 and C2 both do — the check is a diff with edge IRIs
normalised, or a comparison of the extracted triple set. **Build that comparison
first:** it is needed at three slices and is the only thing between a
mis-translated edge and a silent topology change.

1. **C1, per definition.** Migrate one file at a time; topology equivalent after
   each.
2. **C2, whole build.** Generate before and after **in fresh processes** — rdflib
   reuses blank-node labels within a process, so two back-to-back generations can
   agree while two real runs do not. Topology equivalent; every difference in the
   emitted files traceable to an edge rename.
3. **C3 fires.** Construct a violating topology for each of the two redesigned
   shapes and assert a violation, since their failure mode is passing vacuously.
4. **All shipped pipelines at every slice** — `demo:DishacledPipeline`,
   `demo_ab:AutoBridgedPipeline` (the bridging gate), the two NiFi/LDIO ones, and
   `semantics-demo-pipeline.ttl`; plus the fietsstallingen notebook if [O1] is
   resolved by migrating.
5. **`validated_shapes` set comparison** at C2 and C3. The expected diff is §4's
   breakdown — nine flipped, two redesigned, one rewritten, four gone — and
   nothing else silently leaving the set.
6. **The report's throughput block is gone from C0 onward** — the SHACL half of
   every report unchanged, only `tcs:ThroughputMatchResult` entries absent.

---

## 12. Related plans

- **[`config-shape-split-slice-2-plan.md`](config-shape-split-slice-2-plan.md)**
  — mutual dependency, not linear. Its 2a (the `tcs:EmitPhase` marker), 2b
  (freezing the harvester) and 2f (report shaping) are vocabulary-neutral and can
  land alongside C0-C1. Its catalog and query work needs C2 done. Its framework
  translation *is* C4, and releases [D8]. Its 2f and §8 here touch the same
  compiler and should be sequenced against each other.
- **[`config-shape-split-plan.md`](config-shape-split-plan.md)** — the parent.
  Its slice 3 (branching) is largely absorbed: the two obstacles it names —
  `resolve_connection_channels` rejecting fan-out, and `detach_connection_nodes`
  deleting hints before they can be read — are removed by C1 and C2. Its
  `tcs:writerPath` moves here ([D3]); its `tcs:readerPath` is dropped as
  unmotivated. What stays with slice 3 is the *use* of the hint.
- **The `semantic-model-sync` skill** runs at C1 and C2: `p-plan:isPrecededBy` and
  `nifi:route` leave at C1, `tcs:Channel` / `tcs:readsFrom` / `tcs:writesTo` at
  C2, `tcs:writerPath` arrives at C1, and `tcs:Connection` becomes the single
  wiring term.
