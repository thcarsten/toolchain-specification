---
name: run-pipeline-generator-tests
description: 'Run the full pytest unit test suite for the "pipeline generator" project. Use when the user explicitly asks to run all unit tests, the full test suite, or pytest for this repo. Requires explicit user permission before running — never invoke automatically.'
---

# Run pipeline generator test suite

## When to use

Only when the user has explicitly asked, in the current turn, to run the
full unit test suite (or all tests / pytest) for the `pipeline generator`
project. Do not run this skill speculatively, as a side effect of another
task, or because tests "should" be verified — always ask for and receive
explicit confirmation first if it hasn't already been given.

## Procedure

1. Confirm explicit permission was given for this run. If not, ask before proceeding.
2. Start the suite in background/async terminal mode, redirecting output to
   a file instead of letting it print to the terminal. Use the **absolute
   path** to the `pipeline generator` directory in the `cd`, not a relative
   one — a fresh async terminal does not inherit the working directory of
   any prior terminal session, sync or async, so `cd "pipeline generator"`
   silently runs from whatever the new terminal's default directory happens
   to be (observed: the repo root), producing a handful of immediate
   collection errors instead of the real suite:
   ```
   cd "<absolute path to repo>\pipeline generator"; pytest -q > test_output.txt 2>&1
   ```
   Redirecting to a file is required here, not optional: async mode's
   completion notification replays the terminal's full captured stdout
   back into the conversation with no truncation (unlike sync mode, which
   spills output over ~20KB to a file automatically). Redirecting leaves
   the terminal with (almost) nothing to capture, so the notification
   stays small regardless of how verbose or long the run is.

   Do not attempt other `pytest` invocations (e.g. `where pytest`, a
   `--collect-only` sanity check) to work around a problem — a tool-level
   guard blocks any raw `pytest` command that doesn't match this exact
   redirect-to-file pattern. Use non-pytest commands (`python -c ...`,
   `Get-Item`, etc.) for any other diagnostics.
3. Do not poll the terminal and do not call `get_terminal_output` while
   waiting. The tool automatically resumes the conversation once the run
   finishes.
4. The completion notification's own text is the fastest signal: no exit
   code mentioned means the run exited 0 (all passed); "exit code 1" means
   some tests failed or errored. Either way, still read the file — do not
   rely on the notification body for details, it does not include one.
5. Before trusting `test_output.txt`'s contents, confirm the file was
   actually rewritten by *this* run, not stale from an earlier one (e.g. a
   run that silently failed in the wrong directory, per step 2, leaves the
   previous run's file completely untouched). Check with
   `Get-Item test_output.txt | Select-Object LastWriteTime, Length` and
   compare against when this run started — do not just trust that the
   content "looks like" a fresh run.
6. Read only the tail of `test_output.txt` (e.g.
   `Get-Content -Tail 150 test_output.txt`) — the
   `N passed, N failed, N skipped in Xs` summary line and any failure
   tracebacks — never dump the whole file into context. Two anomalies to
   watch for in that tail, both signs the run aborted early rather than
   completing normally: a `!!! Interrupted: N errors during collection !!!`
   banner, or an elapsed time of a few seconds instead of the usual tens of
   minutes.
7. The completion notification only wakes the agent, not the user — report
   the summary back to the user in a chat message as soon as it's read;
   don't leave the result sitting unreported.

## Safety notes

- The suite is read-only against the catalog/pipeline fixtures; it only
  writes to test-local temp/output directories, so background execution
  carries no risk to the working tree.
- A full run takes several minutes (inference + compiler fixpoint cost per
  compiled pipeline across many test files) — this is expected, not a hang.
