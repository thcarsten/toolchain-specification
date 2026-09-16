# User-facing vs. compiler-facing config shapes — design plan

> Status: **proposed**, not implemented. Companion to
> [`pipeline-segments-plan.md`](pipeline-segments-plan.md); same slice-by-slice
> shape. Decisions taken with the user on 2026-09-16 are marked **[D]**.

---

## 1. Context

Today a `tcs:PipelineComponent` carries exactly one shape under
`dcat:qualifiedRelation` / `dcat:hadRole tcs:configShape`. That single shape has
to serve two audiences at once:

- the **pipeline author**, who wants to be told what they must write; and
- the **compiler**, which needs a complete, unambiguous config to emit a
  framework file from.

Those two are not the same document, and the repo already shows the strain:

- **RDF-Connect.** Upstream marks channel parameters `sh:minCount 1` and means
  it, but an author should not have to hand-write `rdfc:reader`/`rdfc:writer` —
  the generator knows the wiring from `tcs:readsFrom`/`tcs:writesTo`. The
  harvester therefore *demotes* that cardinality to `tcs:upstreamMinCount`
  (rewrite 5 in [`shapes.py`](../src/rdfc_catalog_harvest/shapes.py)) and two
  bespoke application-profile shapes
  (`tcs:RdfcMandatoryReaderWiringShape` / `…WriterWiringShape`,
  [`catalog-application-profile-shapes.ttl:1404-1465`](../data/catalog/catalog-application-profile-shapes.ttl))
  re-ask the question against `tcs:readsFrom` instead. That is a workaround for
  a missing distinction.
- **semantic.works.** Several aspects are fixed per component and should never
  be author-visible (a `GRAPH_NAME`, a mail-folder IRI). There is nowhere to
  put them, so they are baked into `tcs:DefaultConfig` bodies — the open
  "pipeline-level override mechanism" roadmap item.
- **NiFi.** The configShape doubles as a `nifi:propertyName` mapping table, so
  it is already half compiler-facing.

**Intended outcome.** Split the one role in two. The compiler-facing shape is
the contract the generator relies on; the user-facing shape is the (smaller)
contract the author must satisfy; a per-component SPARQL `CONSTRUCT` bridges
them. This lets the author omit what the generator can infer, lets a component
hard-code what the author must not set, and — in the final slice — makes
RDF-Connect branching expressible for the first time.

---

## 2. Model

**[D] The mandatory role is the compiler-facing one.** `tcs:configShape` is
renamed to `tcs:compilerFacingConfigShape`; exactly one per component, and for
most components that rename is the whole change. `tcs:userFacingConfigShape` is
**optional**. If it is absent, the compiler-facing shape *is* the user-facing
contract and translation is an identity copy. If it is present, a
`tcs:configTranslation` query **must** be present too.

```turtle
rdfc:HttpFetch
    a tcs:PipelineComponent, dcat:Resource, rdfc:Processor ;

    # mandatory — what the compiler needs (today's :HttpFetchShape, renamed role)
    dcat:qualifiedRelation [
        a dcat:Relationship ;
        dcat:hadRole tcs:compilerFacingConfigShape ;
        dct:relation :HttpFetchShape
    ] ;

    # optional — what the author must write; omits rdfc:writer
    dcat:qualifiedRelation [
        a dcat:Relationship ;
        dcat:hadRole tcs:userFacingConfigShape ;
        dct:relation :HttpFetchAuthoringShape
    ] ;

    # mandatory *iff* a user-facing shape is declared
    tcs:configTranslation """
        CONSTRUCT { ?target ?p ?o . ?target rdfc:writer ?channel . }
        WHERE {
            ?source ?p ?o .
            OPTIONAL { ?step tcs:writesTo ?channel }
        } """ .
```

New `tcs:` terms (coordinate via the `semantic-model-sync` skill — the
[`semantic model/README.md`](../../semantic%20model/README.md) is prose and
carries no term list, so this is a README/diagram note, not an ontology-file
edit):

| Term | Meaning |
| --- | --- |
| `tcs:compilerFacingConfigShape` | Role. Mandatory, exactly one per component. |
| `tcs:userFacingConfigShape` | Role. Optional, at most one per component. |
| `tcs:configTranslation` | String literal on the component: a SPARQL `CONSTRUCT`. |
| `tcs:CompilerConfig` | Class. Subclass of `tcs:Config`. |
| `tcs:compilerConfig` | Step → its translated config. |
| `tcs:readerPath` / `tcs:writerPath` | Slice 3 only. Author hints on a `tcs:Connection`. |

**[D] The translated config lives under a new predicate.** The authored config
stays untouched at `p-plan:hasInputVar`; the translator attaches its output at
`tcs:compilerConfig`. The two must be distinguishable, because each shape has
to target its own config — and the existing `if len(configs) != 1: return`
guards all over the compilers would break if both hung off `hasInputVar`.

**Query contract.** The translator binds `?source` (the authored
`tcs:embedded` node), `?target` (the fresh compiler-side `tcs:embedded` node),
`?step` and `?component` into the query before running it. `?target` must be a
named IRI, not a blank node, so the CONSTRUCT template stays safe across
multi-row WHERE results — the same constraint documented at
[`validation_report_compiler.py:127-131`](../src/compilers/core/validation_report_compiler.py).

**Corrected while implementing (2026-09-16).** Two things in this section
did not survive contact with the code.

*The query reaches the authored body by matching, not by substitution.* This
section originally had the translator substitute `?source` as text. That
cannot work: the authored `tcs:embedded` node is a blank node, and a
blank-node label written into a SPARQL `WHERE` clause is not a reference to
that node — it is an existential that matches anything, so `?source ?p ?o`
becomes "every triple in the graph" and the translated config swallows the
catalog (observed as a pySHACL `ShapeLoadError` once the profile shapes ended
up inside a config body). The contract is now that a query binds `?config` —
a named IRI, since `PipelineSeeder` names every config node — and reaches the
body itself:

```sparql
CONSTRUCT { ?target ?p ?o . ?target rdfc:writer ?channel . }
WHERE {
    ?config tcs:embedded ?source .
    ?source ?p ?o .
    OPTIONAL { ?step tcs:writesTo ?channel }
}
```

*`?target` is named only inside the query.* The named-IRI requirement is real
but purely local: a blank node in a CONSTRUCT template is minted afresh per
solution, so a multi-row WHERE would scatter the body over unconnected nodes.
The translator therefore substitutes a temporary IRI and **relabels the result
back to a blank node** before it reaches the build. A translated config then
has exactly the shape an authored one has, and nothing downstream knows the
difference. An earlier attempt left the name in place and paid for it twice:
`extract_config`'s CBD traversal stops at named nodes, so every translated
config read as empty, and framing a named root yields an `@id` key that
`SemanticWorksEnvVarCompiler` duly emitted as a docker-compose environment
variable.

**Every configured step carries `tcs:compilerConfig`; only some carry a
*derived* one.** A component declaring no `tcs:userFacingConfigShape` has one
contract, not two, so the predicate is pointed straight at the authored config
node — one triple, no copy. A component declaring both gets a node built by its
`tcs:configTranslation`.

That alias is what lets each role's SHACL target stay unconditional, which is
the whole reason it exists:

```sparql
# compilerFacingConfigShape
SELECT ?this WHERE { ?instance prov:specializationOf <Component> ;
                     tcs:compilerConfig/tcs:embedded ?this . }
# userFacingConfigShape
SELECT ?this WHERE { ?instance prov:specializationOf <Component> ;
                     p-plan:hasInputVar/tcs:embedded ?this . }
```

Without it, a compiler-facing shape would select nothing for an untranslated
step and validate nothing at all, while still reporting `conforms: true`.

**Compilers read `tcs:compilerConfig` and never `p-plan:hasInputVar`.** The
author writes one document, the compilers read another, and nothing in the
compiler package looks at both — that separation is the point of the split, not
an implementation detail. `utils.lookup_step_config` is the single read path.

**Whoever mints a config states both predicates.** This is the part that took
two wrong attempts to find. `ConfigTranslator` runs at most once, but configs
are not all minted by then: the per-boundary compilers mint one for each step
`BridgeTransportCompiler` inserted, and `RdfcHttpOutConfigCompiler` /
`LdioHttpOutConfigCompiler` only become eligible in a *later* fixpoint pass,
after the translator has already run. A step configured then would keep no
`tcs:compilerConfig` at all and its file-emitting compiler would stall —
silently dropping `ldio/`, `rdfc/pipeline.ttl` or `nifi/flow.json` from the
build, observed on three of the four shipped pipelines.

The resolution is not a gate but an observation: a generator-minted config has
no author, so it *is* compiler-facing by construction. Each of the six minting
sites now writes `p-plan:hasInputVar` and `tcs:compilerConfig` together, and
`ConfigTranslator` handles only author-written configs, which exist from
`PipelineEnricher` onwards — long before it runs. Should a boundary component
ever declare a user-facing shape, the translator replaces the alias rather than
skipping the step.

**Invariant:** every config carries `tcs:compilerConfig` from the moment it
exists, and the authored side never leaves `p-plan:hasInputVar`.

**Rejected along the way**, both recorded because they look reasonable:

- *Copy every config so the predicate is universal.* Makes consumers wait for
  the copy, so each needs an ordering gate; one such gate ("wait until every
  boundary step has an authored config") is unsatisfiable on
  `pipeline_definition_autobridge.ttl`, whose bridge-inserted
  `sw:rdf-ingest-service` step never gets one.
- *Let consumers fall back to the authored config when no derived one exists.*
  Removes the ordering problem, but reinstates exactly the mixing the two roles
  exist to prevent — a compiler reading the authoring contract.

---

## 3. Slice 1 — rename + no-op translator

Puts the architecture in place with no behaviour change.

**1a. Rename the role.** 40 attachments: 20 in
[`catalog-ldio.ttl`](../data/catalog/catalog-ldio.ttl), 9 generated into
[`catalog-rdfc.ttl`](../data/catalog/catalog-rdfc.ttl), 9 in
[`catalog-nifi.ttl`](../data/catalog/catalog-nifi.ttl), 1 in
`catalog-rdfc-manual.ttl`, 1 in `tests/fixtures/catalog-rdfc-handwritten.ttl`.
**[D] Hard rename, no compatibility alias.**

Do **not** hand-edit `catalog-rdfc.ttl` — it is generated. Change
[`emitter.py:154`](../src/rdfc_catalog_harvest/emitter.py) and regenerate.

While here, fix the **string-vs-IRI split**: `catalog-nifi.ttl` and
[`nifi/config_compiler.py:223,742`](../src/compilers/nifi/config_compiler.py)
use the plain literal `"configShape"` where everything else uses the IRI. The
consequence today is that `ValidationReportCompiler.normalize_config_shapes`
never sees a NiFi shape at all. Normalise NiFi onto the IRI form as part of the
rename.

Python consumers to update (4 sites):
`validation_report_compiler.py:114`, `rdfc/config_compiler.py:340`,
`nifi/config_compiler.py:223,742`, `rdfc_catalog_harvest/emitter.py:154`.
Plus the 2 discriminator targets at
`catalog-application-profile-shapes.ttl:1415,1449`, and 3 test files
(`test_catalog_regression.py`, `test_edge_cases.py`, `test_rdfc_wiring.py`).

**1b. Name the LDIO shapes. [D]** All 20 LDIO configShapes are inline blank
nodes; RDFC and NiFi already use named IRIs. Promote them
(`ldio:HttpInPollerConfigShape`, …). Note this is now *decoupled* from the rest
of the slice — under the mandatory-compiler-facing model a second reference to
the same shape is not needed, so nothing blocks on it. Worth doing anyway: it
makes validation reports cite a real IRI instead of an auto-minted
`:nodeshape_N`, and it is a self-contained commit.

**1c. Add `ConfigTranslator`.** New compiler at
`src/compilers/core/config_translator.py`, following the `compiler_abc.py`
contract (one-arg `__init__`, verb-named public methods called in order from
`compile()`).

```
compile():
  lookup_target_pipeline()
  list_translatable_steps()     # steps with a component + exactly one config
  translate_step_configs()      # per step: identity copy, or run tcs:configTranslation
  attach_compiler_configs()     # <step> tcs:compilerConfig :compilerconfig_N
```

The identity path should **reuse** the CBD traversal
`extract_config` already performs
([`utils.py:169-192`](../src/compilers/utils.py)) —
`traverse(config_id, stop_at_named_nodes=True, exclude="dcat:qualifiedRelation")`
— rather than a naive `CONSTRUCT {?s ?p ?o}`, which would drag in the whole
graph. That exclusion exists precisely to stop shape bookkeeping leaking into
an extracted config, and applies identically here.

> **Implementation trap.** The copy must mint *fresh* blank nodes for the
> embedded body. If the compiler config reuses the authored config's bnodes,
> the two configs share structure and a later injection into the compiler
> config silently mutates the authored one. Mint a named
> `:compilerembedded_N` root and re-label descendants.

**1d. Placement in the run.** Insert into `PipelineGeneratorConfig` and
`PipelineValidatorConfig`
([`pipeline_generator.py:107`](../src/compilers/pipeline_generator.py),
[`pipeline_validator.py`](../src/compilers/pipeline_validator.py)) *after* the
per-boundary config compilers — which mint `p-plan:hasInputVar` configs for
inserted bridge steps and must be allowed to finish first — and *before* the
file-emitting compilers.

Because the runner runs a whole eligible batch in config-list order, list
position alone is not a guarantee. Gate the file-emitting compilers' triggers
on `<build> dct:creator tcs:ConfigTranslator`, reusing the exact idiom
`RdfcConfigCompiler.applies_to` already uses for
`dct:creator tcs:SegmentTagger`.

**1e. Delete the hand-authored `sh:target`s; let the compiler mint them. [D]**

Every existing configShape targets `?step … p-plan:hasInputVar/tcs:embedded
?this`, and as compiler-facing shapes they would all need to move to
`tcs:compilerConfig/tcs:embedded`. Rather than rewrite them, **remove them**:
`normalize_config_shapes` exists precisely to mint this target, and the
catalog-side copies are an oversight — their authors did not know it was
automatic. The idempotence guard at
[`validation_report_compiler.py:124`](../src/compilers/core/validation_report_compiler.py)
then skips every RDFC and NiFi shape, which is why that method is effectively
LDIO-only today.

Removing them collapses this step from "rewrite four producers of a target
path" to "delete 19 blocks and stop generating them", and leaves exactly one
place that knows where a config lives:

- 9 hand-written `sh:select`s in `catalog-nifi.ttl`
- 9 generated into `catalog-rdfc.ttl` — delete `_sparql_target`
  ([`shapes.py:286-306`](../src/rdfc_catalog_harvest/shapes.py)) and its call
  site, then regenerate
- 1 in `catalog-rdfc-manual.ttl`

Leave the 16 `sh:target`s in `catalog-application-profile-shapes.ttl` alone —
those are free-standing profile shapes, not configShapes, and nothing mints
targets for them.

`normalize_config_shapes` then needs one change: mint a target for **both**
roles, each against its own config node — `p-plan:hasInputVar/tcs:embedded` for
the user-facing shape, `tcs:compilerConfig/tcs:embedded` for the
compiler-facing one. Keep the existing guard so an author-supplied `sh:target`
still wins.

This also means the NiFi IRI normalisation in 1a is load-bearing rather than
cosmetic: once NiFi's targets are gone, a NiFi shape that still spells its role
as a string literal would be targeted by nothing and validate nothing.

**1f. Downstream config readers.** Every compiler that consumes a step config
switches from `p-plan:hasInputVar` to `tcs:compilerConfig`. The `FILTER NOT
EXISTS { ?step p-plan:hasInputVar ?c }` *minting* guards in the boundary
compilers stay on `hasInputVar` — they run before translation and mint
authored-side configs.

**Done 2026-09-16.** The inventory above was close but not right on two
counts, both worth recording:

- `nifi/dockerfile_compiler.py:48` and `rdfc/dockerfile_compiler.py:170` are
  **not** step-config readers. They read a *component-level* `tcs:config` of
  type `tcs:DockerImageConfig` (`rdfc:Orchestrator tcs:config ?config`), which
  translation does not touch. Left alone.
- `nifi/config_compiler.py` has four read sites, not two: the property table,
  two `OPTIONAL { ?step p-plan:hasInputVar ?config }` joins in the processor
  and controller-service queries, and the `nifi:route` path used for
  relationship selection.

Final list, 11 sites in 5 files: `ldio/config_compiler.py` (2),
`rdfc/config_compiler.py` (3), `nifi/config_compiler.py` (4),
`nifi/remote_compiler.py` (1), `sw/env_var_compiler.py` (1).

**Ordering.** 1d proposed gating the file emitters on `dct:creator
tcs:ConfigTranslator`. A provenance gate is the wrong shape — it blocks
forever on a pipeline the translator never applies to. What works is the
mint-time alias described in §2: because every config carries
`tcs:compilerConfig` from the moment it exists, an emitter's existing "every
step in scope has a config" trigger simply asks for the compiler-facing one
and is satisfied at the same moment it used to be.
`SemanticWorksEnvVarCompiler` keeps one explicit check, its trigger keying off
a container existing rather than off configs.

**A note on the 1f framing.** This section describes rerouting reads "from
`p-plan:hasInputVar` to `tcs:compilerConfig`", which is right — but the two
predicates point at the *same node* for every component in the catalog today,
so the reroute is a no-op at the data level and a real change at the
architectural one: after it, no compiler reads the authoring contract.

**Exit criterion:** the demonstrator pipeline still compiles to
`conforms: true` and byte-identical output. This slice is a pure no-op.

---

## 4. Slice 2 — RDF-Connect channel injection becomes declarative

**Honest framing: this slice buys clarity, not capability.** The behaviour it
describes already exists, in Python, at
[`rdfc/config_compiler.py:207-350`](../src/compilers/rdfc/config_compiler.py).
`describe_channel_wiring` → `_inject_wiring_key` → `_lookup_channel_predicate`
already injects a step's single `tcs:readsFrom`/`tcs:writesTo` channel under
the component's declared reader/writer path, reading the direction from
`tcs:upstreamClass`, refusing to guess when there are 0 or >1 candidates, and
never overwriting an authored value. The demonstrator pipeline authors only
three wiring keys (`pipeline_definition.ttl:335,363,391`) — all of them the
ambiguous cases that code deliberately declines.

So slice 2 is a **relocation**: move that logic out of one framework's compiler
and into per-component `tcs:configTranslation` queries, where LDIO and
semantic.works can use the same mechanism. The payoffs are real but should be
stated accurately:

- RDFC components gain a genuine `tcs:userFacingConfigShape` that simply omits
  the channel properties, instead of the current `tcs:upstreamMinCount`
  demotion. The harvester can stop applying rewrite 5, and
  `tcs:RdfcMandatoryReaderWiringShape` / `…WriterWiringShape` can be deleted —
  the compiler-facing shape now asserts the real `sh:minCount 1` against the
  config that is actually required to have it.
- semantic.works components get a place to hard-code fixed values without a
  `tcs:DefaultConfig`, which is the unblocking half of the "pipeline-level
  override mechanism" roadmap item.
- LDIO needs no query at all — omitting `tcs:userFacingConfigShape` is exactly
  the "user-facing and compiler-facing are the same" case.

Generating the RDFC queries belongs in the harvester
(`shapes.py` knows which properties are channels and in which direction), not
in hand-edits to `catalog-rdfc.ttl`.

---

## 5. Slice 3 — branching (the actual new capability)

This is where the architecture earns its place, and it retires two open
roadmap items at once (`SemanticModelMapper` sub-items: Connection branching,
and removing the `TEMPORARY` `tcs:ConnectionCardinalityShape`).

The author annotates the connection with which framework predicate it is:

```turtle
[ a tcs:Connection ;
  tcs:from demo:SdsifyMeasurements ; tcs:writerPath rdfc:output ;
  tcs:to   demo:ThresholdMonitor   ; tcs:readerPath tm:stream ] .
```

Required changes in
[`semantic_model_mapper.py`](../src/compilers/core/semantic_model_mapper.py):

1. **Relax the fan-out/fan-in rejection** in `resolve_connection_channels`
   (`:182-234`), which currently rejects branching outright via a `Counter`.
   With an explicit path hint per connection, a branching producer is
   well-defined: mint one channel per connection.
2. **Carry the hints onto the channel.** `detach_connection_nodes` (`:249-259`)
   deletes *every* triple with the Connection as subject, and the minted
   `:channel_N` IRI has no derivation from the connection IRI — so the hints
   must be copied onto the channel (`?channel tcs:writerPath rdfc:output`)
   before detaching, or they are lost.
3. **Drop `tcs:ConnectionCardinalityShape`** from the application profile.

The per-component translation query then reads the hint off the channel rather
than falling back to the component's single declared path — the same
`_lookup_channel_predicate` logic, but with the ambiguity resolved by the
author instead of declined by the compiler.

**Likely bonus.** This plausibly closes the open "auto-bridging breaks a
channel named in a framework config" item. That bug bites because
`rdfc:Sdsify` names `rdfc:output` in its *authored* config, which
`BridgeTransportCompiler` then invalidates by repointing the channel. If
Sdsify's outputs are expressed as connection hints and injected by a translator
that runs *after* bridging, the emitted names are correct by construction and
there is no stale authored value to contradict them. This should be verified
against `data/pipelines/pipeline_definition_autobridge.ttl` rather than
assumed — and note the user has deferred that item for separate treatment, so
treat the fix as a side-effect to confirm, not a goal to design around.

---

## 6. Impact & feasibility

| Area | Impact | Risk |
| --- | --- | --- |
| Catalog role rename (40 attachments, 5 files) | Mechanical; 9 are regenerated | **Low** |
| Naming 20 LDIO shapes | Mechanical, independent commit | **Low** |
| `ConfigTranslator` (new, ~150 lines) | Follows an established compiler shape | **Low** |
| Deleting 19 catalog `sh:target`s | Net simplification; one minting site left | **Low-Medium** — an un-targeted shape validates nothing and still reports `conforms: true` |
| Switching ~12 config-read sites | Wide but shallow; each is a 1-line predicate change | **Medium** — a missed site reads the untranslated config and regresses quietly |
| Compiler ordering | Needs `dct:creator` gates on file emitters | **Medium** — the fixpoint runs batches in list order; position alone is not a guarantee |
| Slice 3 mapper changes | Touches the branching rejection logic | **Medium** |
| Blank-node aliasing in the identity copy | Subtle, easy to get wrong | **Medium** |

**Feasibility: good.** Every primitive needed already exists —
`GraphReader.sparql()` dispatches CONSTRUCT to a new reader, `extract_config`
has the correct CBD traversal, `dct:creator` gating is an established idiom,
and the fixpoint runner needs no changes. The design adds one compiler and one
predicate; it does not touch `CompilationRunner`, `FileMaterializer`, or
`rdfine`.

**The main caveat is that slice 1 is much larger than slice 2's payoff.** Slice
1 rewrites 40 catalog attachments, retargets every shape, and reroutes a dozen
config reads — all to end in a verified no-op — while slice 2 mostly relocates
working code. The value is concentrated in slice 3. If the branching use case
is not wanted soon, slices 1-2 are defensible as cleanup (they delete two
application-profile shapes and a harvester rewrite rule) but should not be sold
as a functional gain.

**Two things worth knowing before starting:**

- **`catalog-sw.ttl` and `catalog-core.ttl` have zero configShapes. [D]** All
  nine semantic.works components are unshaped, so "exactly one compiler-facing
  shape per component" cannot be enforced on day one. **Decided:** those shapes
  are follow-up work after this extension; the rule is "every
  `tcs:PipelineComponent` needs at least one `tcs:compilerFacingConfigShape`
  *unless it is genuinely not configurable*". So do **not** write a SHACL
  constraint making the role mandatory in slice 1 — it would fire on all nine
  sw components plus the core stubs. Add it once the sw shapes land, and use
  the same pass to decide which components are legitimately unconfigurable.
  Practical consequence: the semantic.works motivating example in §1 (a fixed
  `GRAPH_NAME` / mail-folder IRI injected by the query) cannot be demonstrated
  until those shapes exist — slice 2's sw payoff is deferred with them.
- **`semantics-demo-pipeline.ttl` is not loaded.** It is untracked and absent
  from `DEFAULT_PIPELINE_FILES` in both configs. It is the cleanest existing
  example of the authoring style this feature targets, so it is the natural
  acceptance test. **Prepared for that, 2026-09-16** — it was *not* loadable as
  written, and for a worse reason than this note first recorded: it did not
  merely reuse the `demo:DishacledPipeline` plan IRI, it reused *every* named
  subject in `pipeline_definition.ttl` (`demo:ApiPoll`,
  `demo:ThresholdMonitor`, `demo:TriggerAlert`, `demo:DeliverEmail`,
  `demo:measurementsStream`, `demo:thresholdMonitorAgent`). Loading both would
  not have produced two pipelines but one, with each step carrying two
  `prov:specializationOf` values and belonging to two plans —
  `DEFAULT_PIPELINE_FILES` is documented as safe to load wholesale precisely
  because "definitions use disjoint pipeline-id IRIs"
  ([`pipeline_generator.py:81`](../src/compilers/pipeline_generator.py)).
  Resolved by moving the file's own entities to `demo_sd:`
  (`http://example.org/example/semantics-demo/`) with the plan as
  `demo_sd:SemanticsDemoPipeline`, keeping the borrowed
  `demo:SosaWaterLevelObservationShape` on `demo:` — exactly what
  `pipeline_definition_autobridge.ttl` already does. An audit of all eight
  definitions found no other cross-file subject overlap, bar the deliberate
  one between `pipeline_definition_nifi.ttl` and its `.deployment.ttl`
  overlay. Also fixed: `a tcs:Config` → `a tcs:PipelineConfig` on
  `demo_sd:TriggerAlert` (**[D]** clerical), and a dangling
  `tcs:outputShape` of `shape:Observation` on `demo_sd:ApiPoll` — an IRI
  declared nowhere in `data/` — repointed at
  `demo:SosaWaterLevelObservationShape`, which is what
  `demo_sd:ThresholdMonitor` already names on its input side. That last one
  matters beyond tidiness: this demo elides the demonstrator's parse/map
  steps and wires poller straight into monitor, so those two shapes are the
  two ends of one channel and `ValidationReportCompiler`'s throughput check
  compares them.

  **Not** fixed: `demo_sd:DeliverEmail` still has no `p-plan:hasInputVar`.
  This note previously listed that as a defect; it is not one. The
  demonstrator's own `demo:DeliverEmail` omits it too, deliberately and with
  a comment saying so, and `sw:deliver-email-service` declares no config
  shape to satisfy — it carries only a `tcs:config` docker-compose stanza.
  Its `tcs:Connection` wiring is likewise *not* a problem:
  `SemanticModelMapper` is in both `PipelineGeneratorConfig` and
  `PipelineValidatorConfig` and expands connections into
  `tcs:readsFrom`/`tcs:writesTo` early in the fixpoint loop, long before
  `ValidationReportCompiler` runs in the finalize phase.

**Checked and dismissed.** `_unblank_synthetic_shape_ids`
([`validation_report_compiler.py:599`](../src/compilers/core/validation_report_compiler.py))
re-blanks `:nodeshape_N` / `:emptyshape_N` but not `:configshapetarget_N` or
`:{role}rel_N`. This is cosmetic and currently inert: those nodes are targets
and relationship wrappers, not shapes, and pySHACL results cite the shape via
`sh:sourceShape`, so they never reach the report. All three generated reports
under `out/` contain zero occurrences. No action needed — but if slice 1 starts
minting targets for two roles per component, re-run that grep to confirm it
stays true.

---

## 7. Verification

Run with the project environment —
`~\anaconda3\envs\pipeline_generator\python.exe` — and only with explicit
permission, per the `run-pipeline-generator-tests` skill.

1. **Per-slice regression.** `python -m pytest tests/ -q` (23 files) plus
   `src/rdfine`'s own suite. `test_rdfc_wiring.py` and
   `test_catalog_regression.py` are the two that pin the current configShape
   behaviour and should be updated deliberately, not just made to pass.
2. **Slice 1 no-op proof.** Generate `demo:DishacledPipeline` before and after
   into two folders and `diff -r` them. This is the strongest available check
   and should be a hard gate: any byte difference in slice 1 is a bug.
   The byte-identical semantic.works Tier-1/2 comparison cells at the end of
   [`src/demo.ipynb`](../src/demo.ipynb) cover the same ground.
   **This gate is only meaningful because of the determinism work in §8** —
   before it, the same input produced different bytes on every run and the
   diff was pure noise. Run each side in a *fresh process*: within one
   process, rdflib reuses blank-node labels and the hash seed is fixed, so
   two back-to-back generations can agree while two real runs do not.
3. **Validation still fires.** `PipelineValidator("demo:DishacledPipeline")`
   then `compilers[ValidationReportCompiler].conforms is True`. Crucially, also
   snapshot `validated_shapes` before the change and assert the **set is
   identical** afterwards — an un-targeted shape validates nothing and still
   reports `conforms: true`, so conformance alone cannot detect the most likely
   slice-1 failure. The set is the right check, not the length: deleting the 19
   catalog targets and re-minting them should be shape-for-shape neutral, and
   comparing sets catches a shape that silently swapped places with another.
4. **Negative test.** A component with a `tcs:userFacingConfigShape` but no
   `tcs:configTranslation` must raise, not silently identity-copy.
5. **Slice 2.** Remove the three authored wiring keys from
   `pipeline_definition.ttl` and confirm the emitted `rdfc/pipeline.ttl` is
   unchanged.
6. **Slice 3.** A new fixture pipeline with a fan-out from `rdfc:Sdsify`
   (`rdfc:output` + `rdfc:metadataOutput`) — currently inexpressible via
   `tcs:Connection`. Then re-run
   `data/pipelines/pipeline_definition_autobridge.ttl` to check the bonus claim
   in §5.

---

## 8. Prerequisite (done) — deterministic output

Slice 1's exit criterion is "byte-identical output", and when this plan was
written the generator could not meet it *for unchanged code*: the same input
produced different bytes on every run. Two independent causes, both upstream of
any emitted file:

- **Unordered SPARQL / `set` iteration decided minted names.** Every
  `:channel_N`, `:pipelineconfig_N`, `:segment_N`, `:env_N` and
  `:throughputresult_N` is numbered from an enumeration whose order the
  engine does not guarantee, and those names are written straight into the
  emitted configs — `segment_N` is even a *filename*
  (`ldio/pipelines/segment_N.yml`).
- **Blank-node labels are per-parse random.** `PipelineSeeder.name_blind_nodes`
  sorted its rename list with `sorted(set(...))` over rdflib blank-node
  labels, which change on every parse, so the numbering was reshuffled even
  with a fixed hash seed.

Fixed by sorting each such enumeration on a *run-stable* key — named IRIs
where they exist, and for blank nodes a recursive content-derived description
(outgoing predicate/object pairs, descending into nested blank nodes, plus
incoming edges). Sites: `pipeline_seeder`, `pipeline_enricher`,
`segment_tagger`, `semantic_model_mapper`, `validation_report_compiler`,
`nifi/config_compiler`, `rdfc/config_compiler`.

**One of these was a real bug, not cosmetic.**
`DockerComposeCompiler._lookup_container_service_names` built a
container → service-name dict by comprehension, so a container instantiating
two compose-config components kept whichever row arrived last. That is not
hypothetical: `BridgeTransportCompiler` attaches `sw:rdf-ingest-service` to the
triple store's container, so every `depends_on` in the emitted compose file
flipped between `triplestore` and `rdf-ingest` from run to run. Resolved by
ranking a bridge-inserted `tcs:{Entry,Exit}BoundaryComponent` *below* the
component the container was minted for — a passenger does not name its host —
which also happens to be what the hand-built demonstrator's compose file says.

**Status: verified.** All four pipelines in `DEFAULT_PIPELINE_FILES` with a
reachable definition (`demo:DishacledPipeline`, `demo_ab:AutoBridgedPipeline`,
`demo_ln:LdioNifiBridgePipeline`, `demo_nl:NifiLdioBridgePipeline`) generate
byte-identical folders across two fresh processes.

**Known remaining wobble, deliberately left:** three bare `sh:NodeShape`
passthrough shapes are structurally indistinguishable and so tie in the sort.
Which one wins a given index does not reach an emitted file —
`ValidationReportCompiler._unblank_synthetic_shape_ids` renders them back as
blank nodes in the report — so this is inert. If a future change starts
emitting those names, it stops being inert.

**Also worth knowing:** `FileMaterializer` writes but never prunes, so an `out/`
folder regenerated after a segment renumbering keeps the old `segment_N.yml`
alongside the new one. Stale files from before the fix are still sitting in
`out/dishacled-full` and `out/ldio-nifi`; clear the folder before using it as a
diff baseline.
