# Slice 2 — channel injection becomes declarative

> **Status: blocked, 2026-09-22.** Parts 2a, 2b and 2f below are vocabulary-
> neutral and can land now. The rest — the catalog conversion and the
> translation queries, which are the actual payload — waits on
> `tcs:Channel` being retired in favour of `tcs:Connection`. See §3a for why,
> and [`channel-to-connection-plan.md`](channel-to-connection-plan.md) for the
> work itself. The dependency is mutual, not linear: 2a, 2b and 2f can land
> alongside its C0-C1; the catalog and query work here needs its C2 (the
> vocabulary flip) done; and its C4 is where this plan's queries replace the
> mechanical retarget C2 leaves behind.
>
> Zoom-in on §4 of
> [`config-shape-split-plan.md`](config-shape-split-plan.md), which stays the
> parent document: the model (`tcs:compilerFacingConfigShape`,
> `tcs:userFacingConfigShape`, `tcs:configTranslation`, `tcs:compilerConfig`)
> and slice 1 are settled there and are not restated here except where slice 2
> puts weight on them. Findings from reading the code on 2026-09-22 are marked
> **[F]**; decisions taken with the user that day are marked **[D]**.
>
> **Terminology.** This document says *user-facing config shape* and
> *compiler-facing config shape* throughout, matching the role IRIs
> `tcs:userFacingConfigShape` / `tcs:compilerFacingConfigShape`. "Authoring
> shape" — used in the parent plan's sketch — is the same thing as the
> user-facing config shape and is not used here.

---

## 1. What slice 2 actually is

Slice 1 built the machinery and shipped it switched off: every component in
every catalog declares one contract, so `ConfigTranslator` aliases rather than
translates and no `tcs:configTranslation` has ever run outside
`test_config_translation.py`.

Slice 2 is the first real user of that machinery, and it is a **relocation, not
a new capability**. `RdfcConfigCompiler.describe_channel_wiring` already fills a
step's channel key in from `tcs:readsFrom` / `tcs:writesTo`
([`rdfc/config_compiler.py:207-350`](../pipeline%20generator/src/compilers/rdfc/config_compiler.py)).
Slice 2 deletes that Python and re-expresses it as a per-component
`tcs:configTranslation` query in the catalog, so the same trick is available to
any framework without writing another compiler.

What is bought:

- **The `tcs:upstreamMinCount` demotion goes away.** It exists only because one
  shape had to serve both audiences. With two shapes, upstream's `sh:minCount 1`
  can be stated for real on the compiler-facing config shape and simply left out
  of the user-facing one.
- **`tcs:RdfcMandatoryReaderWiringShape` / `…WriterWiringShape` can be deleted**
  ([`catalog-application-profile-shapes.ttl:1382-1465`](../pipeline%20generator/data/catalog/catalog-application-profile-shapes.ttl)).
  They re-ask upstream's cardinality question against `tcs:readsFrom` because it
  could not be asked against the config. It now can.
- **An explicit "the build has settled" marker in the fixpoint loop** (§5) —
  which is a general fix, not an RDF-Connect one, and retires a `TODO` already
  written into `GraphReducer`.

What is **not** bought: `rdfc:Sdsify`'s two writer paths stay ambiguous and stay
hand-authored. Resolving those is slice 3's job. **[F]** The authored wiring
keys in the shipped definitions are *only* Sdsify's —
`pipeline_definition.ttl:335-336, 391-392` and the two mirrored blocks in
`pipeline_definition_autobridge.ttl:304-305, 356-357`. Nothing else in `data/`
hand-writes a channel key.

---

## 2. Inventory — what gets a translation, and what stays authored

**[F]** Read out of the current catalogs. A channel property is **fillable**
when its component declares exactly one path in that direction — the same
condition `_lookup_channel_predicate` applies today. Note that the path name
differs per component; this is the point §4 turns on.

| Component | Reader path(s) | Writer path(s) | Fillable | Stays user-facing |
| --- | --- | --- | --- | --- |
| `rdfc:HttpFetch` | — | `rdfc:writer` (min 1) | `rdfc:writer` | — |
| `rdfc:HttpOut` | `rdfc:reader` (min 1) | `rdfc:writer` (min 0) | both | — |
| `rdfc:HttpServer` | — | `rdfc:writer` (min 1) | `rdfc:writer` | — |
| `rdfc:LogProcessorJs` | `rdfc:reader` (min 1) | `rdfc:writer` (min 0) | both | — |
| `rdfc:LogProcessorPy` | `rdfc:reader` (min 1) | `rdfc:writer` (min 0) | both | — |
| `rdfc:SPARQLIngest` | `rdfc:memberStream` (min 1) | `rdfc:sparqlWriter` (no min) | both | — |
| `rdfc:Sdsify` | `rdfc:input` (min 1) | `rdfc:output`, `rdfc:metadataOutput` (both min 1) | `rdfc:input` | **`rdfc:output`, `rdfc:metadataOutput`** |
| `rdfc:SkolemizationProcessor` | `rdfc:incoming` (min 1) | `rdfc:outgoing` (min 1) | both | — |
| `rdfc:ThresholdMonitorJs` | `rdfc:reader` (min 1) | `rdfc:writer` (min 1) | both | — |
| `proc:JsonLdToNQuads` | `rdfc:reader` (min 1) | `rdfc:writer` (min 1) | both | — |

Nine live in
[`catalog-rdfc.ttl`](../pipeline%20generator/data/catalog/catalog-rdfc.ttl) and
one in
[`catalog-rdfc-manual.ttl`](../pipeline%20generator/data/catalog/catalog-rdfc-manual.ttl);
per §7 both are hand-edited in this slice. LDIO, NiFi and semantic.works are
untouched — LDIO has no channel parameters at all, which is exactly the
"the user-facing and compiler-facing contracts are the same document" case the
parent plan describes.

---

## 3. Shape layout — the compiler-facing config shape entails the user-facing one

**[D] Each shape is a complete contract, and the compiler-facing one gets there
by `sh:node` rather than by restating.** No property is written twice.

```turtle
# user-facing config shape — the contract the author must satisfy.
# Targeted at p-plan:hasInputVar/tcs:embedded by normalize_config_shapes.
:HttpFetchUserFacingConfigShape a sh:NodeShape ;
    sh:property [ sh:path rdfc:url ; sh:nodeKind sh:IRI ; sh:minCount 1 ;
                  sh:message "rdfc:HttpFetch needs the URL it should fetch." ] ;
    sh:property [ sh:path rdfc:httpOptions ; sh:node :HttpFetchOptionsShape ] .

# compiler-facing config shape — everything above, plus what the translation
# fills in. Targeted at tcs:compilerConfig/tcs:embedded.
:HttpFetchCompilerFacingConfigShape a sh:NodeShape ;
    sh:node :HttpFetchUserFacingConfigShape ;
    sh:property [ sh:path rdfc:writer ; sh:class rdfc:Writer ;
                  sh:minCount 1 ; sh:maxCount 1 ;
                  sh:message "No rdfc:writer was injected: the step is the tcs:from of no tcs:Connection, or of more than one and the translation declined to guess." ] .
```

**[D] `sh:class rdfc:Writer`, not `sh:class tcs:Channel`, and no
`tcs:upstreamClass`.** Both of those come from the harvester's rewrite 1, which
collapses upstream's `rdfc:Reader` / `rdfc:Writer` to `tcs:Channel` and keeps the
direction as a non-constraining annotation. Under `tcs:Connection`-only wiring
that collapse has nothing left to serve: the translation query mints a node
typed `rdfc:Writer` directly, so the shape can assert the real class upstream
asserts. Rewrite 1 joins rewrite 5 on the list of harvester rules that are
obsolete under the split — see §6, where the harvester is frozen rather than
updated.

The compiler-facing shape is still complete — it demands everything the compiler
needs — but it says so by entailment. Nothing is duplicated, so nothing can
drift.

**`sh:node` is used where the translation is additive, which is all ten
components in §2.** A translation that *drops* or *overrides* what the author
wrote — the semantic.works fixed-`GRAPH_NAME` case — cannot entail the
user-facing contract, because its output is not a superset of the authored
config. Such a component simply omits `sh:node` and states its own properties.
That is a per-component choice recorded in the catalog, not a global rule, and
the absence of `sh:node` is the visible marker that a translation is
non-additive.

**Naming. [D]** Both shapes get an explicit role-matching name —
`:XUserFacingConfigShape` and `:XCompilerFacingConfigShape` — rather than
leaving the existing `:XShape` as the compiler-facing one. The cost is renaming
nine IRIs, which shows up in validation reports and in
`reference/dishacled-full/`; the benefit is that neither name can be misread
once a component has two shapes, and a stale `:XShape` reference fails loudly
instead of silently pointing at the wrong contract. Nested shapes
(`:HttpFetchOptionsShape`, `:IngestConfigShape`, …) are neither role and keep
their names.

A component with **no** fillable property gets no user-facing config shape and
no translation, and is left exactly as slice 1 leaves it: one shape, still named
`:XShape`, still compiler-facing, aliased onto the authored config. None of the
ten in §2 fall in that bucket, but most LDIO and NiFi components do.

### 3a. Why this slice waits on `tcs:Channel` being retired

The translation queries are slice 2's whole payload, and they read the wiring.
Written against `tcs:readsFrom` / `tcs:writesTo` they would have to be written
again the moment those go; written against `tcs:Connection` they need the
Connection to still be in the build, which it is not. Either way the ten
catalog blocks get edited twice, and they are the same blocks in both cases.

**Is `tcs:writesTo` the inverse of `tcs:from`, and `tcs:readsFrom` the inverse
of `tcs:to`?** Yes — *provided the Connection node is the channel*. Then
`?conn tcs:from ?w` ⟺ `?w tcs:writesTo ?conn` and `?conn tcs:to ?r` ⟺
`?r tcs:readsFrom ?conn`, exactly. Two places where the meaning is not yet
identical, both worth settling in the channel plan rather than here:

- **Arity. [F]** A channel is one writer to *many* readers —
  `BridgeTransportCompiler._lookup_reader_containers` returns a set, and
  `tcs:RdfcStepChannelWiringShape` speaks of "reader step(s)". A
  `tcs:Connection` is 1:1, enforced by the `TEMPORARY`
  `tcs:ConnectionCardinalityShape`. Under the identification a fan-out becomes
  *N connections*, i.e. N distinct channels, not one channel with N readers.
  Checked: **no shipped pipeline has a channel with more than one reader**
  (and only `semantics-demo-pipeline.ttl` uses `tcs:Connection` at all, once),
  so this costs nothing today — and it is the shape slice 3 wants, since a
  per-branch path hint needs a per-branch node. But it is a modelling decision
  and should be taken deliberately, not inherited.
- **Mutability — this is the actual blocker. [F]** `BridgeTransportCompiler`
  does not only read the wiring, it rewrites it: it mints fresh channels and
  removes and re-adds `tcs:writesTo` / `tcs:readsFrom`
  ([`bridge_transport_compiler.py:217-277`](../pipeline%20generator/src/compilers/core/bridge_transport_compiler.py)).
  An inverse-property pair is only coherent while both directions are
  maintained together. So after bridging, either the Connection's `tcs:from` /
  `tcs:to` go stale and the inverse claim is simply false, or the compiler
  rewrites the author's own `tcs:Connection` triples — the generator mutating
  authoring vocabulary, which is the thing this whole extension exists to stop.

So the equivalence is real but cannot be *asserted* until bridging is expressed
as surgery on Connections — splitting one Connection into two across the
inserted boundary step — rather than as channel repointing. That is the first
substantial piece of the channel plan, and it is squarely outside slice 2.

### Reporting belongs to `ValidationReportCompiler`, not to the catalog

**[F]** `sh:node` does degrade the raw pySHACL output. Checked directly: a
violation inside the delegated shape is reported against the *delegating* shape
as a generic `NodeConstraintComponent` — "Value does not conform to Shape
`:HttpFetchUserFacingConfigShape`. See details" — with the real message pushed
into `sh:detail`. And because the user-facing shape also carries its own minted
target on the authored config, the same omission surfaces twice.

**[D] That is a report-shaping problem, and
[`ValidationReportCompiler`](../pipeline%20generator/src/compilers/core/validation_report_compiler.py)
owns its own output.** It should not be solved by distorting the catalog. Two
changes there, both small:

- **Validate the user-facing config shapes first, and suppress a component's
  compiler-facing config shape when its user-facing one was violated.** If the
  author's own contract is broken, the derived config's failure is a
  consequence, not a second finding — and it is the generic `sh:node` one. This
  removes the duplicate and the badly-worded result in the same move.
  `validate_normal_shapes` currently runs pySHACL once over every targeted
  shape; this makes it two passes with a suppression list between them, and the
  two reports merged as it already merges the throughput results.
- **Name the component and the role on every result.** **[F]**
  `normalize_config_shapes` already selects `?component ?shape ?role` — it is
  the one place that knows the mapping — so it can record it as bookkeeping for
  the report to read back. A reader then gets "`rdfc:HttpFetch`, user-facing
  config" instead of having to recognise `:HttpFetchUserFacingConfigShape`, and
  learns *which config of which component* to fix. Worth doing independently of
  this slice: it applies to every config shape in every catalog, not just the
  ten here.

---

## 4. The translation query is per-component, not generic

**[F] There is no one generic query.** Four of the ten components in §2 do not
use `rdfc:reader` / `rdfc:writer` at all — `rdfc:Sdsify` reads on `rdfc:input`,
`rdfc:SPARQLIngest` on `rdfc:memberStream` / `rdfc:sparqlWriter`,
`rdfc:SkolemizationProcessor` on `rdfc:incoming` / `rdfc:outgoing`. Injecting
`rdfc:reader` into a Sdsify config would produce a key the processor never reads
and leave `rdfc:input` unset, which the compiler-facing config shape's
`sh:minCount 1` would then reject.

The query is a **template instantiated with that component's own fillable
paths**. Written once per component into the catalog, next to the shape that
declares those paths:

The query is a **template instantiated with that component's own fillable
paths**, reading `tcs:Connection` and minting the `rdfc:Reader` / `rdfc:Writer`
typing itself. Written once per component into the catalog, next to the shape
that declares those paths:

```
CONSTRUCT {
    ?target ?p ?o .
    ?target {READER_PATH} ?inConn .  ?inConn  a rdfc:Reader .   # if fillable as a reader
    ?target {WRITER_PATH} ?outConn . ?outConn a rdfc:Writer .   # if fillable as a writer
}
WHERE {
    ?config tcs:embedded ?source .
    OPTIONAL { ?source ?p ?o }
    OPTIONAL {                                                  # if fillable as a reader
        ?inConn a tcs:Connection ; tcs:to ?step .
        FILTER NOT EXISTS { ?other a tcs:Connection ; tcs:to ?step . FILTER (?other != ?inConn) }
        FILTER NOT EXISTS { ?source {READER_PATH} ?authored }
    }
    OPTIONAL {                                                  # if fillable as a writer
        ?outConn a tcs:Connection ; tcs:from ?step .
        FILTER NOT EXISTS { ?other a tcs:Connection ; tcs:from ?step . FILTER (?other != ?outConn) }
        FILTER NOT EXISTS { ?source {WRITER_PATH} ?authored }
    }
}
```

The Connection node is what both ends name, which is what makes the emitted
`pipeline.ttl` connect: the producer's `{WRITER_PATH}` and the consumer's
`{READER_PATH}` resolve to the same IRI, typed `rdfc:Writer` by one query and
`rdfc:Reader` by the other. That replaces
`RdfcConfigCompiler.describe_channels`, which types every `tcs:Channel` as both
regardless of use.

**This form is correct only while each step has at most one Connection per
direction** — which the shipped pipelines satisfy, but which is not the rule.
See the open decision below before implementing it.

`rdfc:Sdsify`, which is fillable only as a reader and only on `rdfc:input`,
therefore gets:

```sparql
CONSTRUCT {
    ?target ?p ?o .
    ?target rdfc:input ?inConn .
    ?inConn a rdfc:Reader .
}
WHERE {
    ?config tcs:embedded ?source .
    OPTIONAL { ?source ?p ?o }
    OPTIONAL {
        ?inConn a tcs:Connection ; tcs:to ?step .
        FILTER NOT EXISTS { ?other a tcs:Connection ; tcs:to ?step . FILTER (?other != ?inConn) }
        FILTER NOT EXISTS { ?source rdfc:input ?authored }
    }
}
```

— and its authored `rdfc:output` / `rdfc:metadataOutput` come across untouched
in the `?source ?p ?o` copy, because they are part of the body, not something
the query injects.

Three clauses are places a naive query goes wrong, and each is worth a test:

- **`OPTIONAL { ?source ?p ?o }`, not a bare `?source ?p ?o`.** **[F]** An empty
  `tcs:embedded [ ]` body — which `test_rdfc_wiring.py` already uses — makes the
  bare pattern match zero rows, so the whole WHERE clause yields nothing and
  *the connection is never injected either*. The body copy must be the optional
  part, not the anchor.
- **The `FILTER NOT EXISTS { … ?other != … }` pair is the "exactly one
  connection" rule.** A bare `OPTIONAL { ?c a tcs:Connection ; tcs:from ?step }`
  on a branching producer yields one row per connection and injects *all* of
  them, violating the property's own `sh:maxCount 1` and guessing precisely
  where today's Python declines to. This is the SPARQL spelling of
  `if len(channels) != 1: return`.
- **`FILTER NOT EXISTS { ?source {PATH} ?authored }` is "never overwrite".**
  Without it an authored key would be joined by an injected one and break
  `sh:maxCount 1`. It must name the component's own path, not a generic one.

### 4a. NiFi — the same input, a different translation

**[D]** [`channel-to-connection-plan.md`](channel-to-connection-plan.md) is
framework-agnostic: it gives every framework the same input — Connections
sharing a `tcs:from`, each optionally carrying a `tcs:writerPath` — and leaves
the translation to the framework. NiFi's is owned here, alongside RDF-Connect's.

**[F] It is a different shape of problem, and a smaller one.** NiFi never
injects a channel key into a step's config. `_connection_plans` reads
`?source tcs:writesTo ?ch . ?destination tcs:readsFrom ?ch` purely to
*reconstruct an edge* for `flow.json`
([`nifi/config_compiler.py:589-620`](../pipeline%20generator/src/compilers/nifi/config_compiler.py)),
which under Connections collapses to `?conn tcs:from ?source ; tcs:to ?destination`
— a simplification, and one the channel plan's mechanical flips already cover.

What lands here is the `nifi:route` replacement:

- `_connection_plans` reads `tcs:writerPath` off the Connection instead of
  `tcs:compilerConfig/tcs:embedded/nifi:route` out of the producer's config.
- The `FILTER (?predicate != rdf:type && ?predicate != nifi:route)` at
  [`:758`](../pipeline%20generator/src/compilers/nifi/config_compiler.py) goes —
  it exists only to keep that bookkeeping out of the emitted property table, and
  there is nothing left to filter once the data lives on the edge.

**[F] The grouping rule below does not apply to NiFi.** Two Connections from one
step carrying the same `tcs:writerPath "success"` are two ordinary `flow.json`
connections from one relationship — there is no shared node to find, and
`tcs:writerPath` simply supplies each connection's `selectedRelationship`.
Collapsing several Connections onto one node is RDF-Connect's problem alone.

**Whether this can be a `tcs:configTranslation` query at all is doubtful, and
should be settled deliberately.** NiFi's output here is not a step's config but
an entry in a *connection table*, which no per-component config query produces.
The likely answer is that NiFi keeps compiler code and only its *input*
vocabulary is unified — still the point, since the authoring surface becomes the
same for both frameworks.

### Open decision — several Connections from one step, and whether a query can do it

**[D]** [`channel-to-connection-plan.md`](channel-to-connection-plan.md) settles
that a `tcs:Connection` is strictly one `tcs:from` to one `tcs:to`, and that
**branching is expressed by several Connections sharing a `tcs:from`**. It
deliberately does not say how those become RDF-Connect vocabulary — that is
framework translation, and it lands here. (NiFi's half is §4a; this section is
RDF-Connect only.)

The rule to implement:

- Several Connections from one step that resolve to the **same** writer path are
  one RDF-Connect channel with several readers. They must translate to **one**
  `rdfc:Writer` node, named by the producer's single `rdfc:writer` and by each
  consumer's `rdfc:reader`.
- Several Connections from one step on **different** writer paths
  (`rdfc:output` / `rdfc:metadataOutput`) are separate channels and translate to
  separate nodes.

So the channel identity is not the Connection — it is the pair *(producing step,
writer path)*. Two Connections collapse onto one node exactly when that pair
agrees.

**And that is what makes this an open question rather than a detail.** The query
of §4 mints per-Connection: `?outConn` *is* the node. Under the grouping rule a
consumer has to name the node its producer chose, which its own Connection does
not identify. Two ways out, and slice 2 has to pick one:

- **Compute a shared identity in the query.** Both sides can see the producing
  step and can reach its writer path through `tcs:from` / `prov:specializationOf`
  and the component's compiler-facing config shape, so
  `IRI(CONCAT(STR(?producer), "#", STR(?path)))` yields the same node from
  either end. Expressible — but it puts cross-component reasoning inside a
  per-component query (the consumer deriving facts from the *producer's* shape),
  which is at odds with the per-component contract these queries are supposed to
  be, and it mints IRIs by string concatenation.
- **Group in the compiler.** `RdfcConfigCompiler` assigns one channel node per
  *(producer, path)* group before translation runs, and the queries reference it.
  Trivial in Python and keeps each query local to its component — but it means
  the channel wiring is not, after all, fully declarative, which is the thing
  slice 2 set out to achieve.

Worth noting before choosing: **[F]** no shipped pipeline has a channel with more
than one reader, so the grouping rule is currently unexercised. That argues for
picking whichever option is simpler to *change later*, not whichever looks more
complete now.

**[F] Nested config bodies survive the copy.** `?target ?p ?o` is a depth-1
copy, but rdflib preserves blank-node identity through a `CONSTRUCT` within the
same store, so a nested body (`rdfc:httpOptions [ … ]`, SPARQLIngest's four
nested config objects) is still reachable from the derived root and
`extract_config`'s CBD traversal walks into it correctly. Verified directly. The
consequence is that the authored and derived configs **share their nested
nodes** — only the root is distinct, which is what
`test_config_translation.py::test_a_translation_does_not_touch_the_authored_config`
already asserts. Safe here, because every injected key is at depth 1.

**This is a real limit on what a non-additive translation can do**, and it is
worth recording next to the queries rather than leaving it to be rediscovered.
A translation that overrides a value at the *root* is fine — the root is freshly
minted. A translation that overrides a value inside a nested node would write
through the shared node and mutate the authored config, which is the one thing
the split exists to prevent. Supporting that needs a deep copy in
`ConfigTranslator._run_translation`, which slice 1 did not build and slice 2 does
not need. The semantic.works fixed-`GRAPH_NAME` case should be checked against
this before it is designed.

---

## 5. Ordering — an explicit "the build has settled" marker

**[F] The problem, and why it is not RDF-Connect's.**

`ConfigTranslator` runs exactly once — `CompilationRunner.run_fixpoint` keeps a
`ran` set shared across both phases
([`compilation_runner.py:190-205`](../pipeline%20generator/src/compilers/compilation_runner.py)).
It fires as soon as `SegmentTagger` has run. But `RdfcHttpOutConfigCompiler`
additionally requires `?channel tcs:endpoint ?endpoint`, written by the paired
Entry compiler; eligibility is computed once per batch, so it is *not* eligible
in the batch its Entry partner runs in and fires a batch **later** — after the
translator has already finished. A bridge-inserted `rdfc:HttpOut` step would
therefore keep an untranslated config, get no `rdfc:reader`, and fail its own
compiler-facing config shape once the real `sh:minCount 1` is restored.

The same shape of bug is already documented elsewhere in the code:
`GraphReducer`'s class docstring carries a standing `TODO` saying its trigger
"must be tightened so narrowing only fires after [`BridgeTransportCompiler` and
the per-boundary config compilers] have finished". Both are the same missing
fact: nothing in the graph says *when the build stopped growing*.

**[F] A third, concrete instance, found 2026-09-22 verifying the
channel-to-connection migration.** `NifiConfigCompiler.applies_to`
([`nifi/config_compiler.py:61-93`](../pipeline%20generator/src/compilers/nifi/config_compiler.py))
tries to encode "wait until the build has settled" itself, by checking for a
`dct:creator tcs:BridgeTransportCompiler` triple (`bridge_ran`) before it will
fire — but that triple only exists if `BridgeTransportCompiler` actually ran,
and `BridgeTransportCompiler.applies_to` only returns `True` once the pipeline
spans more than one `tcs:DockerContainer`. For a single-container NiFi
pipeline (`:DemonstratorPipeline` compiled standalone, not as part of a
multi-framework bridged build) `BridgeTransportCompiler` correctly never runs —
there is nothing to bridge — so that triple never appears, `bridge_ran` stays
`False` forever, and `NifiConfigCompiler` never fires: no `flow.json` is
emitted, silently, with no error. `RequirementClosureCompiler` and the two
per-boundary NiFi config compilers stall the same way, downstream of the same
missing fact. This is exactly what `tcs:EmitPhase` replaces: gating
`NifiConfigCompiler` on `tcs:runPhase tcs:EmitPhase` instead of on a specific
compiler's provenance triple fixes it unconditionally, whether or not bridging
happened, because the phase only advances once `run_fixpoint` has reached a
genuine fixpoint. Confirmed the multi-container path is unaffected today:
`demo_ln:LdioNifiBridgePipeline` (LDIO + NiFi, two containers) does trigger
`BridgeTransportCompiler` and `NifiConfigCompiler` runs immediately after —
the bug is specific to single-container NiFi builds.

**[D] Resolution: make that fact explicit, using the phase mechanism that is
already there.** `CompilationRunner.compile` today runs the fixpoint, sets
`tcs:runPhase tcs:FinalizePhase`, and runs it again. Add one phase in between:

```python
def compile(self) -> Graph:
    self.run_fixpoint()                        # build: topology, containers, configs
    self.set_phase("tcs:EmitPhase")            # <- the build has settled
    self.run_fixpoint()                        # translate, then emit framework files
    self.set_phase("tcs:FinalizePhase")
    self.run_fixpoint()                        # validate, compose
```

The marker means what you asked it to mean: **no further component, step,
container or authored config will enter the build.** Everything that can add one
lives before it — `BridgeTransportCompiler` inserts boundary steps,
`RequirementClosureCompiler` gives containers to components those steps dragged
in, and the per-boundary config compilers mint the configs for them. A
`run_fixpoint` runs to a genuine fixpoint before the phase advances, so the
barrier is hard, not a matter of list position.

**Naming. [D]** `tcs:CompilePhase` was the first suggestion and is too broad —
every phase compiles something. Ruled out for a more specific reason:
`tcs:MaterializePhase` **[F]** collides with `FileMaterializer`, which already
owns "materialize" in this codebase for writing a finished build graph to disk,
*after* `compile()` has returned. A phase of that name inside `compile()` would
name the wrong moment.

`tcs:EmitPhase` is the recommendation: the phase produces the `spdx:File` nodes,
and "the file-emitting compilers" is already how both
[`pipeline_generator.py:137`](../pipeline%20generator/src/compilers/pipeline_generator.py)
and the parent plan refer to exactly this group, so the phase name reuses
vocabulary the repo already has rather than introducing a competing one. It also
sits naturally beside `tcs:FinalizePhase`: both are named for what the phase
does. `tcs:TranslatePhase` is the runner-up — it matches `tcs:configTranslation`
and the "source-to-source" framing in the repo README — but it reads as though
the whole phase were `ConfigTranslator`'s, when that compiler is only its
prologue. Naming it for what has *finished* instead (`tcs:BuildSettledPhase`)
was considered and dropped: the other two phases are named for what follows, and
mixing the two conventions makes the list harder to read than either.

Which compilers move behind the marker — everything that translates the build
into output, gated on `tcs:runPhase tcs:EmitPhase` exactly as the three
finalize compilers gate today:

- `ConfigTranslator`
- `LdioConfigCompiler`, `RdfcConfigCompiler`, `RdfcDockerFileCompiler`,
  `NifiConfigCompiler`, `NifiDockerfileCompiler`, `NifiRemoteCompiler`,
  `SemanticWorksEnvVarCompiler`, `VirtuosoCompiler`, `MuClResourcesCompiler`,
  `MuDispatcherCompiler`, `MuDeltaNotifierCompiler`, `MuAuthorizationCompiler`,
  `ErrorAlertCompiler`

What this buys beyond fixing the gap:

- **The §5 problem dissolves rather than being worked around.** The alternative
  — having `RdfcHttpOutConfigCompiler` and `RdfcHttpServerConfigCompiler` write
  their own channel key at mint time — is unnecessary with the marker in place,
  and should not be done: it would re-scatter the very logic slice 2 is
  centralising.
- Each emitter's hand-rolled "has bridging settled" gate
  (`dct:creator tcs:SegmentTagger`, "every step in this container has a
  `tcs:compilerConfig`") stops being load-bearing ordering and becomes a
  redundant safety check. Leave them in place for this slice; simplifying them is
  separate work.
- `GraphReducer`'s `TODO` is answerable: move it to the start of the compile
  phase.

**One ordering remains inside the compile phase**: `ConfigTranslator` must run
before the emitters, and the mint-time `tcs:compilerConfig` alias means an
emitter's trigger is already satisfied when the phase opens. Handle it with the
established `dct:creator` idiom — the emitters gate on
`dct:creator tcs:ConfigTranslator` — and make `ConfigTranslator.applies_to`
unconditional within the compile phase (currently it asks whether some step
lacks a `tcs:compilerConfig`). Unconditional is what makes the gate safe: a
compiler that never runs records no `dct:creator`, and a pipeline with no
configured steps at all would otherwise block the emitters forever. This is the
narrow, satisfiable version of the provenance gate the parent plan rejected in
its general form.

**Feasibility.** Four lines in `CompilationRunner.compile`, one `applies_to`
clause per moved compiler, and the same one-line docstring note each. It does
touch `CompilationRunner`, which the parent plan's §6 says the design would not
— that claim should be corrected there when this lands.

---

## 6. The harvester is a stub; the catalog is hand-authored

**[D]** Slice 2 does **not** teach
[`rdfc_catalog_harvest`](../pipeline%20generator/src/rdfc_catalog_harvest/) to
emit two shapes and a translation query. The working assumption from here on is
that **every catalog is hand-authored**, the harvester is currently
non-functional, and it must not be able to overwrite what is authored by hand.
Reviving it is separate, lower-priority work.

Consequences that have to be handled inside this slice, because the current repo
states the opposite in several places:

- **`catalog-rdfc.ttl` stops being a generated file.** Its header
  ([`catalog-rdfc.ttl:1-18`](../pipeline%20generator/data/catalog/catalog-rdfc.ttl))
  says `GENERATED FILE - DO NOT EDIT` and gives regeneration instructions.
  Replace it with a header saying the file is hand-authored, that the harvester
  that once produced it is a stub, and pointing at the harvester's own status
  note.
- **Nothing may write over it.** `cli.py:26` defaults `--output` to
  `data/catalog/catalog-rdfc.ttl` and `_cmd_generate` writes it unconditionally
  (`cli.py:90`). Make `generate` refuse to run — a single explicit error naming
  the stub status — rather than merely changing the default, so no flag
  combination reaches the write.
- **`test_catalog_emitter.py::test_committed_catalog_is_current` inverts.** It
  currently asserts the committed catalog equals the generator's output, which
  is precisely what a hand-edit must now be allowed to break. Replace it with a
  test that `generate` refuses; that keeps a live assertion on the stub status
  instead of deleting coverage.
  `test_generated_file_is_marked_do_not_edit` goes with the header.
- **`shapes.py`'s rewrite 5 stays where it is.** It is dead code in a stub, not
  something to delete now; the module docstring gains a line saying the rewrite
  is obsolete under the split and must go when the harvester is revived. The
  catalog itself simply stops carrying `tcs:upstreamMinCount`, because the hand
  edit restores the real `sh:minCount`.

The remaining harvester tests (`test_shapes.py`, the rest of
`test_catalog_emitter.py`) exercise the translation functions directly and keep
passing; leave them, so the stub does not rot silently before someone revives it.

**Separate work item, not part of slice 2:** teach the harvester to derive the
fillable/non-fillable partition, emit both shapes and generate the per-component
query of §4, then re-point it at a regeneration workflow that can prove it
reproduces the hand-authored catalog before being allowed to write it again.

---

## 7. Work items

Ordered so each lands compiling and testable on its own.

### 2a. The phase marker

Per §5. `CompilationRunner.compile` gains `tcs:EmitPhase`; the thirteen
emitters plus `ConfigTranslator` gain the gate; `ConfigTranslator.applies_to`
becomes unconditional within the phase; the emitters gain
`dct:creator tcs:ConfigTranslator`; `GraphReducer`'s `TODO` is resolved.

This is a **no-op refactor** and should be proven as one before anything else
moves: the compilers already run in this relative order on every shipped
pipeline, so all four builds must come out byte-identical. Land it first and
separately — it is the only change in the slice with reach outside RDF-Connect.

Tests: a new one asserting no compiler that can introduce a step or container
runs after the marker, so the invariant is stated somewhere executable rather
than only in a docstring.

### 2b. Freeze the harvester

Per §6: the `catalog-rdfc.ttl` header, the `generate` refusal, the two
`test_catalog_emitter.py` tests, the `shapes.py` docstring note. Independent of
everything else and worth its own commit — after it, the catalog edits in 2c are
legal.

### 2c. Hand-author the ten components *(blocked on the channel plan)*

`catalog-rdfc.ttl` (nine) and `catalog-rdfc-manual.ttl` (one). Per component:

1. Rename `:XShape` → `:XUserFacingConfigShape` and **move the fillable channel
   properties of §2 out of it** — so Sdsify's two writer paths stay, and its
   `rdfc:input` leaves. Everything else stays put; the existing `sh:message`s
   are already written for the author.
2. Add `:XCompilerFacingConfigShape` carrying `sh:node :XUserFacingConfigShape`
   and the moved channel properties, each with `tcs:upstreamMinCount` replaced
   by the real `sh:minCount` it was demoted from and an `sh:message` giving the
   "this step is not wired" diagnosis inherited from the profile shapes deleted
   in 2e.
3. Re-point the existing `dcat:qualifiedRelation` at the compiler-facing shape
   and add a second one with `dcat:hadRole tcs:userFacingConfigShape`, plus the
   `tcs:configTranslation` literal instantiated from §4's template with that
   component's own paths.

Note the rename direction: the shape that keeps its properties becomes the
**user-facing** one, and the compiler-facing shape is the new, small file. That
is the reverse of what the parent plan's sketch implies, and it is what falls
out of entailment.

Do one component first — `rdfc:ThresholdMonitorJs` is the smallest, fillable in
both directions on the plain `rdfc:reader` / `rdfc:writer` paths — and validate
before doing the other nine. `catalog-rdfc-manual.ttl` is additionally parsed
standalone by `test_shacl_path_shape.py`, so it must stay valid Turtle without
the rest of the catalog loaded.

Head the changed catalog section with the nested-node limit from §4.

### 2d. Delete `describe_channel_wiring` *(blocked on the channel plan)*

[`rdfc/config_compiler.py`](../pipeline%20generator/src/compilers/rdfc/config_compiler.py)
— remove `describe_channel_wiring`, `_inject_wiring_key`,
`_lookup_channel_predicate` and the `compile():88` call. Roughly 140 lines.

Worth noting what this also fixes: `_inject_wiring_key` writes into
`lookup_step_config`'s result, which for a one-contract component **is the
authored config node**. Today's injection therefore mutates the document the
author wrote — the exact mixing the two roles exist to prevent. Afterwards the
injection only ever lands on a derived node.

`tests/test_rdfc_wiring.py` is rewritten rather than deleted: the four
behaviours it pins (reader injected, writer injected, ambiguity declined,
authored value never overwritten) are exactly the clauses of §4's template and
should keep a test each, plus one for the empty-body case and one asserting a
component-specific path (Sdsify's `rdfc:input`) is used rather than
`rdfc:reader`.

### 2e. Delete the two profile shapes *(blocked on the channel plan)*

- `catalog-application-profile-shapes.ttl:1382-1465` —
  `tcs:RdfcMandatoryReaderWiringShape` and `tcs:RdfcMandatoryWriterWiringShape`,
  plus the comment block above them explaining the demotion they compensate for.
- `tests/EDGE_CASES.md:40-42` — the three rows naming them. Rows 40-41 re-point
  at the compiler-facing `sh:minCount`, which now covers the same ground
  directly; row 42 (the untestable reader mirror) goes.
- `src/demo.ipynb:225,227` — two `tcs:passed` lines in a committed output cell;
  re-run the notebook rather than editing the JSON.
- Grep for `tcs:upstreamMinCount`: after 2c it should survive only in
  `shapes.py` (the frozen rewrite) and possibly the semantic-model prose.

**Leave the `tcs:derived*` inference rules
([`rdfc_inference_rules.yaml`](../pipeline%20generator/data/inference_rules/rdfc_inference_rules.yaml))
unfiltered by role.** They match any shape reached through
`dcat:qualifiedRelation/dct:relation` and read channel keys off the *authored*
config — and after 2c an authored channel key (Sdsify's) is declared by the
user-facing config shape while an injected one is declared by the compiler-facing
one. Filtering on either role would break one of the two cases, so the rules
stay role-agnostic.

### 2f. Report shaping in `ValidationReportCompiler`

The two changes of §3: user-facing config shapes validated first with the
matching compiler-facing shape suppressed on violation, and component + role
recorded on every config-shape result.

The second half is worth landing **first and on its own** — it applies to every
config shape in every catalog, improves the report whether or not slice 2
proceeds, and gives 2c a readable report to be judged against while the ten
components are converted.

### 2g. Semantic model

Via the `semantic-model-sync` skill. `tcs:upstreamMinCount` is retired from the
catalogs; `tcs:userFacingConfigShape` and `tcs:configTranslation` gain their
first real users; `tcs:EmitPhase` is a new term, as is whatever bookkeeping
predicate 2f introduces for the shape → component/role mapping.

---

## 8. Risks

| Risk | Why | Mitigation |
| --- | --- | --- |
| **Restoring `sh:minCount 1` surfaces genuine gaps** | The demotion has been hiding every unwired mandatory channel since it was introduced. A terminal RDFC step with a mandatory writer path and no `tcs:writesTo` fails validation for the first time. | Do it first: after the single component in 2c, run the validator over all shipped pipelines and enumerate failures before converting the other nine. Each is either a real modelling gap or evidence the property should not have been fillable. |
| **The phase marker is a cross-cutting change** | It touches `CompilationRunner`, which the parent plan promised the design would not, and moves thirteen compilers. | 2a lands alone and must be byte-identical on all four pipelines. If it cannot be, nothing else in the slice should proceed. |
| **Empty `tcs:embedded` body swallows the injection** | A bare `?source ?p ?o` anchor yields zero rows and constructs nothing at all. | The `OPTIONAL` of §4, plus a test with a literally empty body. |
| **Wrong path injected** | The template must be instantiated per component; four of ten do not use `rdfc:reader`/`rdfc:writer`. | The per-component test in 2d; and the compiler-facing `sh:minCount` catches it, since the right path stays unset. |
| **Branching producer over-injects** | A bare `OPTIONAL` binds every channel. | The `FILTER NOT EXISTS ?other` pair, plus the Sdsify case today's `test_ambiguous_writer_paths_left_unwired` covers. |
| **Shape renaming churn** | Nine IRIs appear in validation reports and in `reference/dishacled-full/`. | Expected and reviewed, not suppressed: the `validated_shapes` set comparison in §9.3 is where the rename is checked to be exactly a rename. |
| **Hand-authored catalog drifts from upstream** | The harvester was the mechanism keeping shapes faithful to the upstream `processor.ttl`s; freezing it removes that guarantee. | Accepted, per §6. The frozen snapshots under `data/rdfc_harvest/` stay in the repo, so the comparison remains possible by hand and mechanisable when the harvester is revived. |
| **Report quality regresses before 2g lands** | `sh:node` reports the delegated violation generically and the user-facing shape reports it again from its own target, so between 2c and 2g every authoring omission reads twice, once badly. | Land 2g in the same series, and keep a worked example of a deliberately broken config in the 2g test so the reporting is judged on output rather than on intent. |

---

## 9. Verification

Project environment only (`~\anaconda3\envs\pipeline_generator\python.exe`), and
the full suite only with explicit permission per the
`run-pipeline-generator-tests` skill.

1. **2a is a no-op, and must be proven so.** Generate `demo:DishacledPipeline`
   before and after, in fresh processes, into two folders and `diff -r`.
2. **After 2c-2e, emitted files are still byte-identical** while the *build
   graph* changes shape. The behaviour is being relocated, not altered, so any
   difference in an emitted file is a bug; the derived configs that appear in the
   build graph are not emitted files.
3. **`validated_shapes` set comparison**, as in slice 1's §7.3. Expect it to
   change here — ten user-facing config shapes appear, nine compiler-facing ones
   are renamed, two profile shapes leave — so the check is that the diff is
   exactly that, with nothing silently dropping out.
4. **All four reachable pipelines**, not just the demonstrator:
   `demo:DishacledPipeline`, `demo_ab:AutoBridgedPipeline` (the one that
   exercises §5), and the two NiFi/LDIO ones.
5. **The parent plan's slice-2 acceptance test**: remove Sdsify's four authored
   wiring keys from `pipeline_definition.ttl` and confirm the emitted
   `rdfc/pipeline.ttl` changes exactly as the ambiguity predicts — the two
   writers vanish and are *not* guessed at. The positive half is slice 3.
6. **`semantics-demo-pipeline.ttl`**, prepared in slice 1 as the acceptance case
   for the authoring style this feature targets. Its `demo_sd:ThresholdMonitor`
   uses `rdfc:ThresholdMonitorJs`, fillable in both directions — so it should
   compile with no channel key authored anywhere.

---

## 10. Out of scope

- **Reviving the harvester.** §6. Separate, lower-priority work item.
- **Sdsify's two writers.** Slice 3, via `tcs:writerPath` on the connection.
- **semantic.works fixed values.** Blocked on `catalog-sw.ttl` having any config
  shapes at all, which the parent plan's §6 defers as follow-up work. It is also
  the first translation that will not be additive — so it is the first
  compiler-facing config shape that cannot use `sh:node` (§3), and the first to
  run into the nested-node limit of §4.
- **Splitting NiFi's *config shape* into two roles.** Its shape doubles as a
  `nifi:propertyName` mapping table and is already half compiler-facing; that is
  a separate decision. NiFi's *channel translation* (§4a) is in scope; its
  user-facing / compiler-facing shape split is not.
- **Simplifying the emitters' now-redundant ordering gates.** §5 leaves them in
  place deliberately.
- **Making `tcs:compilerFacingConfigShape` mandatory in SHACL.** Same reason as
  slice 1: it would fire on all nine unshaped semantic.works components.
