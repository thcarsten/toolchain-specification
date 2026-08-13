"""A local web UI for stepping the compiler fixpoint loop one compiler
at a time and watching what each one does to the build graph.

``PipelineGenerator.compile()`` runs the whole loop and hands back a
finished graph, which is what you want in production and useless when a
compiler misbehaves: by the time you can inspect the result, fourteen
compilers have each had a turn. This tool drives the same loop manually
— pick the next compiler, run it, see exactly which triples it added and
removed, then browse the whole store.

Run it::

    cd "pipeline generator"
    python tools/compiler_inspector.py

    # to exercise the opt-in RdfcImportExpander as well:
    python tools/compiler_inspector.py --rdfc-root ../../demonstrator/RDFC

then open http://127.0.0.1:8765.

Stdlib only — no Flask, no new dependency in ``requirements.txt`` for a
debugging aid.

**It reuses the real generator rather than reimplementing it.**
:class:`SteppableGenerator` subclasses :class:`PipelineGenerator` and
calls its ``_record_creator`` / ``_set_finishing`` / ``_strip_finishing``
helpers, so provenance triples and the ``tcs:isFinishing`` flag behave
exactly as they do in a normal run. A stepper that reimplemented the
loop would eventually drift from it and quietly lie to you.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_TOOLS_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _TOOLS_DIR.parent
sys.path.insert(0, str(_PROJECT_DIR / "src"))

from rdflib import Graph  # noqa: E402

from rdfine import GraphReader  # noqa: E402

import compilers as compilers_pkg  # noqa: E402  (populates Compiler._registry)
from compilers import PipelineGenerator  # noqa: E402
from compilers.base import Compiler  # noqa: E402
from compilers.core.pipeline_extractor import PipelineExtractor  # noqa: E402

DATA_DIR = _PROJECT_DIR / "data"
CATALOG_FILES = (
    "catalog-core.ttl",
    "catalog-ldio.ttl",
    "catalog-rdfc.ttl",
    "catalog-sw.ttl",
)
SHAPES_FILE = "catalog-application-profile-shapes.ttl"
PIPELINE_FILE = "pipeline_definition.ttl"
INFERENCE_RULES = "inference_rules.yaml"
PUBLIC_ID = "file:///workspace/pipeline/"


def load_catalog() -> Graph:
    """Catalog + application-profile shapes + pipeline definition, with
    the inference rules applied — the same entry path
    ``testing_helpers.compile_pipeline`` and ``demo.ipynb`` use."""
    graph = Graph()
    for filename in (*CATALOG_FILES, SHAPES_FILE, PIPELINE_FILE):
        graph.parse(str(DATA_DIR / filename), publicID=PUBLIC_ID)
    return GraphReader(graph).infer(str(DATA_DIR / INFERENCE_RULES)).graph


@dataclass
class Step:
    """One recorded compiler run, kept so the UI can page back through
    history instead of only showing the most recent delta."""

    index: int
    name: str
    kind: str  # "bootstrap" | "compile" | "finishing"
    added: list[list[str]] = field(default_factory=list)
    removed: list[list[str]] = field(default_factory=list)
    total_before: int = 0
    total_after: int = 0

    def as_json(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "kind": self.kind,
            "added": self.added,
            "removed": self.removed,
            "addedCount": len(self.added),
            "removedCount": len(self.removed),
            "totalBefore": self.total_before,
            "totalAfter": self.total_after,
        }


def _triples(reader: GraphReader) -> list[list[str]]:
    """Compacted ``[subject, predicate, object]`` rows, prefix-shortened
    via the graph's own prefix store so the UI shows ``tcs:instantiates``
    rather than the full IRI."""
    frame = reader.df
    if frame.empty:
        return []
    return [
        [str(row["sub"]), str(row["pred"]), str(row["obj"])]
        for _, row in frame.iterrows()
    ]


class SteppableGenerator(PipelineGenerator):
    """:class:`PipelineGenerator` with the fixpoint loop turned inside
    out: instead of looping to convergence, expose "what could run next"
    and "run exactly this one"."""

    def __init__(self, pipeline_id: str, catalog_graph: Graph) -> None:
        super().__init__(pipeline_id, catalog_graph)
        self.ran: set[type[Compiler]] = set()
        self.settling = False
        self.history: list[Step] = []
        self.reset()

    # -- lifecycle ---------------------------------------------------

    def reset(self) -> None:
        """Back to a freshly bootstrapped build."""
        self.compilers = {}
        self.ran = set()
        self.settling = False
        self.history = []
        self.build = Graph()
        self._bootstrap()

    def _bootstrap(self) -> None:
        """Run ``PipelineExtractor`` exactly as ``compile()`` does.

        Recorded as a step like any other, which makes the extraction
        itself inspectable: its ``removed_triples`` is everything the
        traversal-based narrowing dropped from the catalog, usually the
        single largest delta of a whole run.
        """
        extractor = PipelineExtractor(self.pipeline_id, self.catalog_graph)
        total_before = len(self.catalog_graph)
        self.build = extractor.compile()
        self.compilers[PipelineExtractor] = extractor
        self.ran.add(PipelineExtractor)
        self._record_creator(PipelineExtractor)
        self._set_finishing(False)
        self._record(extractor, "PipelineExtractor", "bootstrap", total_before)

    # -- introspection -----------------------------------------------

    def registry(self) -> list[type[Compiler]]:
        return list(Compiler._registry)

    def eligible(self) -> list[type[Compiler]]:
        """Compilers the real loop would pick on this iteration."""
        reader = GraphReader(self.build)
        return [
            cls
            for cls in Compiler._registry
            if cls not in self.ran and cls.applies_to(reader)
        ]

    # -- actions ------------------------------------------------------

    def step(self, name: str) -> Step:
        """Run the named compiler against the current build graph."""
        matches = [cls for cls in Compiler._registry if cls.__name__ == name]
        if not matches:
            raise KeyError(f"no compiler named {name!r}")
        cls = matches[0]
        if cls in self.ran:
            raise ValueError(f"{name} has already run")

        total_before = len(self.build)
        instance = cls(self.build)
        self.build = instance.compile()
        self.compilers[cls] = instance
        self.ran.add(cls)
        self._record_creator(cls)

        # Mirror the real loop: progress during a finishing pass drops
        # back to regular shaping so newly-eligible compilers still fire.
        if self.settling:
            self._set_finishing(False)
            self.settling = False

        return self._record(instance, name, "compile", total_before)

    def enter_finishing(self) -> Step:
        """Flip ``tcs:isFinishing`` to true — what the real loop does
        when a shaping scan finds nothing eligible, and what unlocks
        finalization-style compilers like ``DockerComposeCompiler``."""
        total_before = len(self.build)
        self._set_finishing(True)
        self.settling = True
        step = Step(
            index=len(self.history),
            name="(finishing pass)",
            kind="finishing",
            total_before=total_before,
            total_after=len(self.build),
        )
        self.history.append(step)
        return step

    def _record(
        self, instance: Compiler, name: str, kind: str, total_before: int
    ) -> Step:
        """Snapshot a compiler's delta into history.

        Captured now rather than lazily: ``added_triples`` /
        ``removed_triples`` recompute a full graph difference on every
        access, which is fine once and wasteful on every page load.

        Note the delta is the *compiler's own* contribution — the
        ``dct:creator`` provenance triples are written by the generator
        afterwards, against ``self.build`` rather than the instance's
        readers, so they deliberately do not show up here.
        """
        step = Step(
            index=len(self.history),
            name=name,
            kind=kind,
            added=_triples(instance.added_triples),
            removed=_triples(instance.removed_triples),
            total_before=total_before,
            total_after=len(self.build),
        )
        self.history.append(step)
        return step

    # -- serialization ------------------------------------------------

    def state(self) -> dict[str, Any]:
        eligible = {cls.__name__ for cls in self.eligible()}
        ran_order = [cls.__name__ for cls in self.compilers]
        return {
            "pipelineId": self.pipeline_id,
            "settling": self.settling,
            "totalTriples": len(self.build),
            "ran": ran_order,
            "compilers": [
                {
                    "name": cls.__name__,
                    "module": cls.__module__.rsplit(".", 1)[-1],
                    "ran": cls in self.ran,
                    "eligible": cls.__name__ in eligible,
                    "order": ran_order.index(cls.__name__)
                    if cls.__name__ in ran_order
                    else None,
                }
                for cls in self.registry()
            ],
            "history": [step.as_json() for step in self.history],
            "triples": _triples(GraphReader(self.build)),
        }


# ----------------------------------------------------------------------
# HTTP layer
# ----------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    generator: SteppableGenerator  # injected by ``serve``

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        pass  # keep the console readable; errors still surface in responses

    def _send(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?")[0] in ("/", "/index.html"):
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/state":
            self._send(self.generator.state())
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        try:
            if self.path == "/api/step":
                self.generator.step(payload["compiler"])
            elif self.path == "/api/finishing":
                self.generator.enter_finishing()
            elif self.path == "/api/reset":
                self.generator.reset()
            else:
                self._send({"error": "not found"}, 404)
                return
        except Exception as exc:  # surfaced in the UI, not the console
            import traceback

            self._send(
                {
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                    "state": self.generator.state(),
                },
                500,
            )
            return
        self._send(self.generator.state())


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Compiler Inspector</title>
<style>
  :root {
    --bg: #ffffff; --panel: #f6f7f9; --border: #d8dce2; --text: #1c2024;
    --muted: #666f7a; --accent: #b46504; --added: #1a7f37; --added-bg: #e8f5ec;
    --removed: #b8302a; --removed-bg: #fdecea; --chip: #e6e9ed;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #16181c; --panel: #1e2126; --border: #333941; --text: #e6e8eb;
      --muted: #98a1ac; --accent: #e39a4a; --added: #4ec36f; --added-bg: #16301f;
      --removed: #f0736c; --removed-bg: #34191a; --chip: #2a2f36;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font: 14px/1.5 ui-sans-serif, system-ui, -apple-system, sans-serif; }
  header { padding: 12px 18px; border-bottom: 1px solid var(--border);
           display: flex; gap: 16px; align-items: baseline; flex-wrap: wrap; }
  h1 { font-size: 15px; margin: 0; font-weight: 650; }
  .muted { color: var(--muted); }
  .wrap { display: grid; grid-template-columns: 300px 1fr; gap: 0;
          height: calc(100vh - 50px); }
  aside { border-right: 1px solid var(--border); overflow-y: auto;
          padding: 14px; background: var(--panel); }
  main { overflow-y: auto; padding: 14px 18px; }
  button { font: inherit; padding: 5px 10px; border-radius: 6px;
           border: 1px solid var(--border); background: var(--bg);
           color: var(--text); cursor: pointer; }
  button:hover:not(:disabled) { border-color: var(--accent); }
  button:disabled { opacity: .45; cursor: not-allowed; }
  button.go { border-color: var(--accent); color: var(--accent); font-weight: 600; }
  .comp { display: flex; align-items: center; gap: 8px; padding: 5px 0;
          border-bottom: 1px solid var(--border); }
  .comp:last-child { border-bottom: 0; }
  .comp .nm { flex: 1; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
              font-size: 12px; word-break: break-word; }
  .comp.done .nm { color: var(--muted); text-decoration: line-through; }
  .ord { font-size: 11px; background: var(--chip); border-radius: 10px;
         padding: 1px 7px; color: var(--muted); }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .05em;
       color: var(--muted); margin: 0 0 8px; }
  section { margin-bottom: 22px; }
  table { border-collapse: collapse; width: 100%; font-size: 12px;
          font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  td { padding: 2px 8px 2px 0; vertical-align: top;
       border-bottom: 1px solid var(--border); word-break: break-all; }
  td:nth-child(2) { color: var(--accent); }
  .added td { background: var(--added-bg); }
  .removed td { background: var(--removed-bg); }
  .sign { width: 14px; font-weight: 700; }
  .added .sign { color: var(--added); }
  .removed .sign { color: var(--removed); }
  .scroll { max-height: 44vh; overflow: auto; border: 1px solid var(--border);
            border-radius: 6px; padding: 6px 10px; }
  input[type=search] { font: inherit; padding: 5px 9px; width: 100%;
      max-width: 380px; border: 1px solid var(--border); border-radius: 6px;
      background: var(--bg); color: var(--text); }
  .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
         margin-bottom: 8px; }
  .pill { font-size: 12px; background: var(--chip); border-radius: 10px;
          padding: 2px 9px; }
  .err { background: var(--removed-bg); color: var(--removed);
         border: 1px solid var(--removed); border-radius: 6px;
         padding: 10px 12px; white-space: pre-wrap; font-size: 12px;
         font-family: ui-monospace, monospace; margin-bottom: 14px; }
  .hist { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
  .hist button { font-size: 12px; }
  .hist button[aria-pressed=true] { border-color: var(--accent);
                                    background: var(--chip); font-weight: 600; }
</style>
</head>
<body>
<header>
  <h1>Compiler Inspector</h1>
  <span class="muted" id="pid"></span>
  <span class="pill" id="total"></span>
  <span class="pill" id="finishing" hidden>tcs:isFinishing true</span>
  <span style="flex:1"></span>
  <button id="reset">Reset to bootstrap</button>
</header>
<div class="wrap">
  <aside>
    <h2>Compilers</h2>
    <div id="list"></div>
    <button id="finish" style="margin-top:12px;width:100%">
      Enter finishing pass
    </button>
  </aside>
  <main>
    <div id="err" class="err" hidden></div>
    <section>
      <h2>Steps</h2>
      <div class="hist" id="hist"></div>
      <div class="row">
        <strong id="stepName"></strong>
        <span class="pill" id="stepCounts"></span>
      </div>
      <div class="scroll" id="delta"></div>
    </section>
    <section>
      <h2>Store</h2>
      <div class="row">
        <input type="search" id="filter" placeholder="filter triples (substring, all three columns)">
        <span class="muted" id="shown"></span>
      </div>
      <div class="scroll" id="store"></div>
    </section>
  </main>
</div>
<script>
let state = null, selected = null, MAX = 1500;

const esc = s => s.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const rows = (list, cls, sign) => list.map(t =>
  `<tr class="${cls}"><td class="sign">${sign}</td><td>${esc(t[0])}</td>` +
  `<td>${esc(t[1])}</td><td>${esc(t[2])}</td></tr>`).join('');

async function call(path, body) {
  const r = await fetch(path, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {})
  });
  const data = await r.json();
  const err = document.getElementById('err');
  if (!r.ok) {
    err.hidden = false;
    err.textContent = data.traceback || data.error;
    if (data.state) { state = data.state; selected = null; render(); }
    return;
  }
  err.hidden = true;
  state = data; selected = null; render();
}

async function load() {
  state = await (await fetch('/api/state')).json();
  render();
}

function render() {
  document.getElementById('pid').textContent = state.pipelineId;
  document.getElementById('total').textContent = state.totalTriples + ' triples';
  document.getElementById('finishing').hidden = !state.settling;

  document.getElementById('list').innerHTML = state.compilers.map(c => `
    <div class="comp ${c.ran ? 'done' : ''}">
      ${c.order !== null ? `<span class="ord">${c.order}</span>` : ''}
      <span class="nm" title="${esc(c.module)}">${esc(c.name)}</span>
      ${c.ran ? '' :
        `<button class="${c.eligible ? 'go' : ''}" data-run="${esc(c.name)}"
           ${c.eligible ? '' : 'disabled'}
           title="${c.eligible ? 'applies_to() is true' : 'applies_to() is false'}"
         >Run</button>`}
    </div>`).join('');
  document.querySelectorAll('[data-run]').forEach(b =>
    b.onclick = () => call('/api/step', {compiler: b.dataset.run}));

  const h = state.history;
  if (selected === null) selected = h.length - 1;
  document.getElementById('hist').innerHTML = h.map(s =>
    `<button data-step="${s.index}" aria-pressed="${s.index === selected}">
       ${s.index}. ${esc(s.name)}</button>`).join('');
  document.querySelectorAll('[data-step]').forEach(b =>
    b.onclick = () => { selected = +b.dataset.step; render(); });

  const step = h[selected];
  if (step) {
    document.getElementById('stepName').textContent = step.name;
    document.getElementById('stepCounts').textContent =
      `+${step.addedCount} / −${step.removedCount}  ·  ` +
      `${step.totalBefore} → ${step.totalAfter} triples`;
    document.getElementById('delta').innerHTML =
      (step.addedCount + step.removedCount)
        ? `<table>${rows(step.removed,'removed','−')}${rows(step.added,'added','+')}</table>`
        : '<p class="muted">No triples changed.</p>';
  }
  renderStore();
}

function renderStore() {
  const q = document.getElementById('filter').value.toLowerCase();
  const all = state.triples;
  const hits = q ? all.filter(t => t.join(' ').toLowerCase().includes(q)) : all;
  const cut = hits.slice(0, MAX);
  document.getElementById('shown').textContent =
    `showing ${cut.length} of ${hits.length}` +
    (hits.length !== all.length ? ` (filtered from ${all.length})` : '') +
    (hits.length > MAX ? ' — narrow the filter to see the rest' : '');
  document.getElementById('store').innerHTML =
    `<table>${cut.map(t =>
      `<tr><td class="sign"></td><td>${esc(t[0])}</td><td>${esc(t[1])}</td>` +
      `<td>${esc(t[2])}</td></tr>`).join('')}</table>`;
}

document.getElementById('filter').oninput = renderStore;
document.getElementById('reset').onclick = () => call('/api/reset');
document.getElementById('finish').onclick = () => call('/api/finishing');
load();
</script>
</body>
</html>
"""


def serve(pipeline_id: str, port: int, rdfc_root: str | None) -> None:
    if rdfc_root:
        # Opt in to RdfcImportExpander, which is inert until its roots
        # are configured. The prefix is the container-side path the
        # catalog's owl:imports are written against.
        #
        # Looked up dynamically rather than imported: that compiler is
        # not on every branch, and a debugging tool should still start
        # without it rather than fail at import time.
        expander = getattr(compilers_pkg, "RdfcImportExpander", None)
        if expander is None:
            print(
                "warning: --rdfc-root given but this checkout has no "
                "RdfcImportExpander; owl:imports will not be expanded."
            )
        else:
            expander.import_roots = {PUBLIC_ID: str(Path(rdfc_root).resolve())}
            print(
                f"RdfcImportExpander roots: {PUBLIC_ID} -> "
                f"{Path(rdfc_root).resolve()}"
            )

    print("Loading catalog and applying inference rules...")
    Handler.generator = SteppableGenerator(pipeline_id, load_catalog())
    print(f"Bootstrapped {pipeline_id}: {len(Handler.generator.build)} triples")
    print(f"  http://127.0.0.1:{port}\n")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--pipeline", default="demo:DishacledPipeline")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--rdfc-root",
        default=None,
        help="local directory standing in for the container's "
        "/workspace/pipeline (enables RdfcImportExpander)",
    )
    args = parser.parse_args()
    try:
        serve(args.pipeline, args.port, args.rdfc_root)
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
