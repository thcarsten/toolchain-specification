"""One-shot builder for the SEMANTiCS demo's pre-enriched single-file catalog.

Merges the framework catalogs + the demo pipeline definition, runs both
inference passes, and writes the result to `catalog_demo.ttl` next to this
script. Re-run whenever the source catalogs, inference rules, or the demo
pipeline definition change.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = (HERE / ".." / "src").resolve()
DATA = (HERE / ".." / "data").resolve()
sys.path.insert(0, str(SRC))

from rdflib import Graph  # noqa: E402
from rdfine import GraphReader  # noqa: E402

CATALOG_FILES = [
    "catalog-core.ttl",
    "catalog-ldio.ttl",
    "catalog-rdfc.ttl",
    "catalog-rdfc-manual.ttl",
    "catalog-application-profile-shapes.ttl",
]

PUBLIC_ID = "file:///workspace/pipeline/"


def main() -> None:
    g = Graph()
    for name in CATALOG_FILES:
        g.parse(DATA / name, publicID=PUBLIC_ID)
    g.parse(HERE / "pipeline_definition_demo.ttl", publicID=PUBLIC_ID)

    enriched = (
        GraphReader(g)
        .infer(str(DATA / "inference_rules.yaml"))
        .infer(str(DATA / "rdfc_inference_rules.yaml"))
    )

    out = HERE / "catalog_demo.ttl"
    enriched.graph.serialize(destination=out, format="turtle")
    print(f"Wrote {out} ({len(enriched.graph)} triples)")


if __name__ == "__main__":
    main()
