# Plan: SHACL validation as a static pre-generator test suite

Validation runs **once, statically, before the pipeline generator is called**.
It is a test suite over the catalog + pipeline definition combined: SHACL
shapes attached to `PipelineComponent`s, `InstancePipelineComponent`s, and
`Compiler`s via `dcat:qualifiedRelation` + `dcat:hadRole` are validated against
the graph, and input/output *data* shapes are matched between adjacent steps
using the external shape-matching algorithm. Only if every check passes does
the graph get handed to `PipelineGenerator.compile()`.

## Rationale

- **Pipeline generator gets a validated baseline.** No mid-compile failures;
  every `compile()` starts from a graph that has already passed every
  applicable static check.
- **One idiom for attaching shapes.** `dcat:qualifiedRelation` +
  `dcat:hadRole`, exactly as the catalog already uses. Adding a role is
  optional — an unroled shape is still validated; the role only *hints* at
  what the constraint concerns and *how* it should be checked. Linking a
  shape to an entity signals *whose* constraint it is (compiler → constraint
  the compiler needs met; component → constraint introduced by using that
  component).
- **Constraints stay declarative.** Every shape lives in RDF next to the
  entity it concerns; no Python code needs to know about individual shapes.

## Role vocabulary

The role string is optional metadata carried by the qualified relation. Roles
are extensible — new roles can be added without touching validation logic that
concerns other roles.

| Role | Attached to | What it constrains | How it is checked |
| --- | --- | --- | --- |
| `"inputShape"` | `PipelineComponent` **or** `InstancePipelineComponent` | schema of data flowing **into** the step | shape-matching algorithm (§3) |
| `"outputShape"` | `PipelineComponent` **or** `InstancePipelineComponent` | schema of data flowing **out of** the step | shape-matching algorithm (§3) |
| `"configShape"` | `PipelineComponent` | schema of the component's `tcs:Config` | pySHACL (also drives the SHACL UI) |
| _(no role)_ | anything (component, instance, compiler) | free-form constraint on the graph | pySHACL |

**Scope of input/output shapes.**
- Attached to a `PipelineComponent`: valid for *every*
  `InstancePipelineComponent` that specializes that component.
- Attached to an `InstancePipelineComponent`: valid **only** for that specific
  step's position in the pipeline. Overrides / narrows the component-level
  shape if any.

The previously-considered roles `"requiredShape"` and `"buildShape"` are
dropped — they were tied to a per-compiler validation gate in the fixpoint
loop, which this plan replaces with static pre-generator validation.

## Architecture

### 1. `GraphReader.validate()` — pySHACL wrapper (rdfine)

Location: `src/rdfine/graph_reader.py`.

```python
GraphReader.validate(
    shapes: GraphReader | Graph | None = None,
    **pyshacl_kwargs,
) -> ValidationResult
```

- Wraps `pyshacl.validate`.
- Returns `ValidationResult(conforms: bool, report: GraphReader)` — boolean is
  the primary output; report is inspectable when it isn't.
- `shapes=None` uses the reader's own graph as the shapes graph (SHACL
  default) so pure "graph carries its own shapes" cases work.
- Adds `pyshacl` to `src/rdfine/pyproject.toml` (either hard dep or `[shacl]`
  extra).

### 2. Static test suite (new module)

Recommended location: `src/validation/` (new package), independent of
`compilers`. Runs before `PipelineGenerator(...)` is instantiated.

Flow:

```python
def validate_pipeline_definition(catalog_graph: Graph, pipeline_id: str) -> None:
    reader = GraphReader(catalog_graph)

    # (a) Collect all qualified-relation shapes whose role is not
    #     inputShape / outputShape (i.e. configShape + unroled shapes).
    #     Validate each subject's subgraph against its shapes via pySHACL.
    for subject, shapes in _collect_pyshacl_shapes(reader).items():
        result = reader.traverse(subject).validate(shapes)
        if not result.conforms:
            raise ShaclValidationError(subject, result.report)

    # (b) Walk the pipeline step chain. For each adjacent pair (upstream,
    #     downstream), resolve inputShape / outputShape — instance-level
    #     first, falling back to component-level. Call the shape-matching
    #     bridge to check downstream input ⊆ upstream output.
    _run_shape_matching(reader, pipeline_id)
```

Raises `ShaclValidationError` on the first failure. Success is silent — the
graph is now a validated baseline for the generator.

### 3. Shape-matching algorithm — input/output shapes

Input and output shapes describe *data* that has not been ingested yet, so
plain SHACL validation does not apply. Instead we use the DiSHACLed query
shape-matching algorithm:

- Repo: <https://github.com/DiSHACLed/query-shape-matching-algorithm>
- Language: **not Python** — needs a callable bridge (subprocess, containerised
  service, or language-native bindings — TBD; see Open questions).
- Purpose: given an upstream step's `outputShape` and a downstream step's
  `inputShape`, determine whether the downstream's expected input schema is
  satisfied by the upstream's declared output schema.
- Resolution rule at each step: instance-level shape takes precedence over
  component-level shape. If neither is declared, the pair is skipped
  (unconstrained edge).

## Integration point

`PipelineGenerator.compile()` **does not** call the test suite. Callers
(demo notebook, CLI, CI) run it first and only construct `PipelineGenerator`
on success. This keeps the generator pure graph-in/graph-out and makes
validation observable as its own step.

Illustrative caller:

```python
from validation import validate_pipeline_definition
from compilers import PipelineGenerator, ProjectBuilder

validate_pipeline_definition(catalog_graph, ":DemonstratorPipeline")
gen = PipelineGenerator(":DemonstratorPipeline", catalog_graph)
ProjectBuilder(gen.compile()).write("./out/demonstrator")
```

## Relevant files

- `src/rdfine/graph_reader.py` — add `validate()` (mirror the style of
  `select` / `construct` at line ~352).
- `src/rdfine/pyproject.toml` — add `pyshacl` dep (optionally as `[shacl]`
  extra).
- `src/validation/` (new package):
  - `test_suite.py` — the `validate_pipeline_definition` entry point.
  - `exceptions.py` — `ShaclValidationError`.
  - `shape_matching.py` — bridge to the external algorithm.
- `data/catalog.ttl` — populate `dcat:qualifiedRelation` blocks with the four
  roles. Start with `configShape` on existing components; add `inputShape` /
  `outputShape` where data schemas are known.
- `README.md` §4 — new subsection on the static test suite.
- `../semantic model/README.md` — cross-link the role vocabulary from the
  `sh:NodeShape` section.

## Verification

1. **Rdfine unit tests.** Shape-passing / shape-failing fixtures against
   `GraphReader.validate` (empty shapes, minCount violation, targetClass miss,
   external shapes graph).
2. **Test-suite happy path.** A well-formed pipeline definition passes
   silently.
3. **Test-suite sad path.** A component with a broken `configShape` (e.g.
   missing `ldio:type`) → `ShaclValidationError` naming the offending subject
   IRI and citing the shape.
4. **Shape matching happy path.** Two adjacent components with compatible
   `outputShape` / `inputShape` — matcher returns compatible; suite passes.
5. **Shape matching sad path.** Downstream `inputShape` requires a property
   the upstream `outputShape` does not declare — suite raises with both step
   IRIs in the message.
6. **Regression.** `demo.ipynb` continues to compile because the demonstrator
   pipeline is well-formed.

## Decisions

- Validation is **static, pre-generator**, not a per-compiler gate inside the
  fixpoint loop. `PipelineGenerator` stays unchanged.
- Roles are **optional**. Unroled shapes are still validated (pySHACL branch).
- Only `inputShape` / `outputShape` need the external matcher. Everything else
  goes through pySHACL.
- Input/output shapes at the `PipelineComponent` level apply to every
  specialization; the `InstancePipelineComponent`-level shape overrides /
  narrows.
- Shape matching is delegated to the external algorithm rather than
  reimplemented in Python.
- `ShaclValidationError` lives in `src/validation/exceptions.py`. The
  `compilers/` package stays unaware of validation.
- No audit-trail triples on the build (previously proposed `dct:conformsTo`) —
  validation happens on the *catalog + definition*, not on the build. If an
  audit trail is wanted later, it can be recorded separately by the caller.

## Open questions

1. **Bridge to the shape-matching algorithm.** Subprocess call to a pre-built
   binary, a small HTTP service, or a language-native binding? Blocked on the
   algorithm's own API surface.
2. **`configShape` and the SHACL UI.** The UI is a separate deliverable; the
   test suite validates configs against `configShape`, but the UI's authoring
   loop is out of scope here.
3. **Package layout.** New standalone `src/validation/` package (recommended,
   so it can also run in CI without pulling in `compilers`) vs. folded into
   `compilers` or `rdfine`.
4. **Optional `pyshacl` install.** Hard dependency for `rdfine`, or an
   optional extra `pip install rdfine[shacl]` with a clear `ImportError` from
   `.validate()` when missing?
5. **Notebook UX on failure.** Raising is right for CI/CD; for `demo.ipynb` it
   halts the run. Consider a `strict=False` mode on the test-suite entry
   point that records failures and returns them, so `demo.ipynb` can show
   partial results.
