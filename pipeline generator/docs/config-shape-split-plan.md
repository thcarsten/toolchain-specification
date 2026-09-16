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
`?step` and `?component` into the query before running it via
`GraphReader.sparql()`, which already dispatches `CONSTRUCT` to a new reader
([`graph_reader.py:490`](../src/rdfine/graph_reader.py)). Binding by textual
substitution matches how `RdfcConfigCompiler` and `ValidationReportCompiler`
already build queries. `?target` must be a named IRI, not a blank node, so the
CONSTRUCT template stays safe across multi-row WHERE results — the same
constraint documented at
[`validation_report_compiler.py:127-131`](../src/compilers/core/validation_report_compiler.py).

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
switches from `p-plan:hasInputVar` to `tcs:compilerConfig`:
`rdfc/config_compiler.py:190,197,282`, `ldio/config_compiler.py:140,143,201`,
`nifi/config_compiler.py:221,737`, `nifi/remote_compiler.py:117`,
`sw/env_var_compiler.py:95`, `nifi/dockerfile_compiler.py:48`,
`rdfc/dockerfile_compiler.py:170`. The `FILTER NOT EXISTS { ?step
p-plan:hasInputVar ?c }` *minting* guards in the boundary compilers stay on
`hasInputVar` — they run before translation and mint authored-side configs.

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
  acceptance test. Two small fixes first: `demo:TriggerAlert` uses
  `a tcs:Config` where every other step uses `a tcs:PipelineConfig` (**[D]**
  clerical — strictly optional, since `inference_rules.yaml` can derive it, but
  worth correcting at source), and `demo:DeliverEmail` has no
  `p-plan:hasInputVar` at all. Its `tcs:Connection` wiring is *not* a problem:
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
