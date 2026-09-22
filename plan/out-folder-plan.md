# The `out/` folder — design plan

> Status: **implemented** 2026-09-22, slices 1-5. Written in response to
> "I have the inclination to gitignore `out/`, but sometimes I want to be
> able to show what kind of output is being produced."
>
> Decisions taken: `reference/` confirmed as the name. `temp/out/` deleted
> and `temp/` renamed to `semantics-2026-demo/` -- see §6.

---

## 1. Context

[`pipeline generator/out/`](../pipeline%20generator/out/) is where
`FileMaterializer.write()` lands. Today it holds four builds on disk:

| Build | Tracked? | How it got there |
| --- | --- | --- |
| `dishacled-full/` | yes (19 files) | the default target of `PipelineGenerator` and `demo.ipynb` |
| `ldio-nifi/` | yes (5 files) | committed before the ignore rule existed |
| `nifi-ldio/` | yes (6 files) | idem |
| `semantics-demo/` | no | ignored, as intended |

The current [`.gitignore`](../.gitignore) rule is

```
**/out/*
!**/out/dishacled-full/
```

which says "ignore everything under any `out/` except the full reference
build." That is not what the repo actually does: `ldio-nifi/` and
`nifi-ldio/` were already tracked when the rule was added, and `.gitignore`
has no effect on tracked files. So 11 files are being maintained by
accident — every regeneration of those two builds silently dirties the
working tree, and nothing tests them.

## 2. The real problem

`out/` is one name over two different kinds of thing, and that is why the
"ignore it / but I want to show it" tension has no clean answer:

1. **Scratch output.** `semantics-demo/`, `out/fietsstallingen/`,
   `out/nifi/`, `temp/out/` — whatever the last notebook run happened to
   produce. This is the Downloads folder. Genuinely disposable.

2. **A golden fixture.**
   [`test_docker_compose_determinism.py:42`](../pipeline%20generator/tests/test_docker_compose_determinism.py#L42)
   reads `out/dishacled-full/docker-compose.yml` and asserts a fresh
   compile is byte-identical to it — "guards the checked-in artifact
   against silent drift." That file is a test fixture that happens to live
   in the output directory. Gitignoring `out/` wholesale breaks this test,
   which is the thing that has been blocking the obvious answer.

3. **Showcase material.** The need to point someone at a complete,
   browsable example of generated output without asking them to install
   anything and run it.

(2) and (3) are the same artifact seen from two angles, and neither is
"output" in the sense (1) means. Once they are moved out of `out/`, the
folder becomes purely disposable and the instinct to ignore it wholesale
is simply correct.

## 3. The proposed design

**Split the reference build out of `out/`, then ignore `out/` entirely.**

- `pipeline generator/reference/dishacled-full/` — the tracked, reviewable
  reference build. New home for what is now `out/dishacled-full/`. The name
  says what it is: not a leftover, a fixture and an exhibit.
- `pipeline generator/out/` — fully gitignored, no exceptions. Every run
  lands here. Never reviewed, never committed, safe to delete at any time.
- **Promotion is an explicit act.** A run does not touch `reference/`.
  When output legitimately changes, you copy `out/dishacled-full/` over
  `reference/dishacled-full/` and commit that diff on purpose.

This is the standard golden-file workflow, and it buys the property the
current scheme lacks: *a casual run can never dirty a tracked file.* The
determinism work in `DockerComposeCompiler` (canonical service ordering)
was done precisely so that this diff would be reviewable — promotion is
what that work was for.

It also answers (3) without extra machinery. `reference/dishacled-full/`
is a complete four-framework output, browsable on GitHub, linked from the
README — which is a better answer to "show me what it produces" than three
half-maintained builds.

### What happens to `ldio-nifi/` and `nifi-ldio/`

Untrack them. They are two-framework subsets of a build no test exercises
and no doc links, and they are already ignored-in-intent. If either turns
out to be worth showing, it can be promoted into `reference/` later on
purpose, under the same rule as `dishacled-full`.

## 4. Slices

### Slice 1 — untrack the accidental builds

- `git rm -r --cached "pipeline generator/out/ldio-nifi" "pipeline generator/out/nifi-ldio"`
  (leaves them on disk).
- No code or doc changes; nothing references them.

### Slice 2 — move the reference build

- `git mv "pipeline generator/out/dishacled-full" "pipeline generator/reference/dishacled-full"`.
- Update
  [`test_docker_compose_determinism.py:42`](../pipeline%20generator/tests/test_docker_compose_determinism.py#L42)
  to read `reference/dishacled-full/docker-compose.yml`; update its module
  docstring, which says "the committed `out/` artifact."
- Leave the *write* targets alone —
  [`pipeline_generator.py:18`](../pipeline%20generator/src/compilers/pipeline_generator.py#L18),
  [`file_materializer.py:31`](../pipeline%20generator/src/compilers/file_materializer.py#L31)
  and `demo.ipynb` keep writing to `./out/dishacled-full`. That is the
  point: runs go to scratch, promotion is manual.

### Slice 3 — simplify `.gitignore`

Replace the two-line exception rule with:

```
# Generator output. Everything the generator materializes is scratch;
# the reviewable build lives in `pipeline generator/reference/`.
**/out/
```

### Slice 4 — the promote step

Add a one-liner to the generator README under the existing `out/` prose:

> **Refreshing the reference build.** Run the full pipeline, then copy
> `out/dishacled-full/` over `reference/dishacled-full/` and review the
> diff before committing. `test_committed_output_matches_a_fresh_run`
> fails until you do.

Optional, only if the copy becomes tedious: a `scripts/promote.ps1`. Not
worth writing up front for a directory copy.

### Slice 5 — documentation

- README: point the "what does it produce" prose at `reference/`.
- [`CLAUDE.md`](../CLAUDE.md): one line in §2 noting `out/` is scratch and
  `reference/` is the tracked build, so a future session does not try to
  commit output.
- `dishacled-context` skill: update the `out/` mention in the roadmap if
  one exists.

## 5. Alternatives considered

- **Keep the exception-based scheme, just fix its inconsistencies**
  (untrack the two stray builds, leave `dishacled-full` in `out/`). Half
  the work, and everything keeps functioning. Rejected because it
  preserves the trap: the default write target is a tracked path, so
  running the generator dirties the working tree, and the only defence is
  remembering not to commit. The whole point of the instinct to ignore
  `out/` is to stop having to remember.

- **Ignore `out/` entirely and delete the determinism test.** Cheapest,
  but it throws away the drift guard that the canonical-ordering work
  exists to serve.

- **Generate the showcase into the docs at publish time.** More machinery
  than three tracked directories justify.

## 6. Open questions

- ~~`reference/` vs `fixtures/reference/` vs `data/reference/`~~ ->
  **`reference/`**, confirmed 2026-09-22.
- ~~**`temp/` is not scratch.**~~ Resolved 2026-09-22. `temp/out/` did want
  the same treatment as `out/` and was deleted. The rest of the folder was
  never scratch: it is the SEMANTiCS 2026 demo-video material -- five
  tracked files (`README.md`, `demo.ipynb`, `_build_demo_catalog.py`, two
  `.ttl` inputs) supporting
  [`docs/semantics2026-practitioners-submission-draft.md`](../pipeline%20generator/docs/semantics2026-practitioners-submission-draft.md).
  Renamed to
  [`pipeline generator/semantics-2026-demo/`](../pipeline%20generator/semantics-2026-demo/),
  which says what it is. Its own `out/` stays gitignored under the
  `**/out/` rule, so the demo can be recompiled freely.
