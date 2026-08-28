# SEMANTiCS 2026 — Practitioners Track — Demo material

Self-contained material for the ≤ 1-minute demo video described in
[`../docs/semantics2026-practitioners-submission-draft.md`](../docs/semantics2026-practitioners-submission-draft.md)
(Appendix — Demo video plan).

Contents:

- `pipeline_definition_demo.ttl` — the three-step cross-framework pipeline
  the demo compiles: LDIO polls a public Turtle URL, LDIO parses it into
  RDF, RDF-Connect logs each incoming record to stdout. The cross-container
  edge between LDIO and RDF-Connect is left implicit — `BridgeTransportCompiler`
  inserts the `LdioHttpOut` ↔ `RdfcHttpServer` pair automatically.
- `catalog_demo.ttl` — pre-enriched single-file catalogue used by the video.
  Merges the framework catalogs from `../data/` with the pipeline definition
  above and applies both inference passes ahead of time so the notebook
  stays a single `Graph().parse(...)` call. Regenerate with
  `python _build_demo_catalog.py` whenever any input changes.
- `_build_demo_catalog.py` — one-shot script that rebuilds `catalog_demo.ttl`.
- `demo.ipynb` — the compile notebook shown in the video: loads
  `catalog_demo.ttl`, calls `PipelineGenerator.compile()`, writes the
  result to `./out/` with `ProjectBuilder`.
- `out/` — target directory for the generated project. Populated by running
  the notebook. After compile, `docker compose up -d && docker compose logs -f rdfc`
  inside `out/` should show data pulses every ~5 seconds.

## Source URL

Tim Berners-Lee's public FOAF card:
<https://www.w3.org/People/Berners-Lee/card.ttl>

Small, stable, pure Turtle, no auth. Poll interval: every 5 s.

## Status

Draft scaffold. The pipeline definition and notebook are the intended
end-state; expect one or two iteration passes to align exactly with
what the current compiler set expects (e.g. whether `SparqlConstructTransformer`
is needed as a shape carrier, whether the log processor requires an
explicit input-shape, etc.).
