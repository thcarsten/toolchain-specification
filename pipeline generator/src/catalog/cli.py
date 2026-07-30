"""Command line entry point: ``python -m catalog {harvest,generate}``.

Two commands, deliberately separate:

``harvest``
    Talks to npm / PyPI, refreshes ``data/harvest/``. Needs network.
``generate``
    Rewrites the catalog file from the snapshot. Offline and
    deterministic, so it is safe to run in CI and to diff.

Splitting them is the same discipline as committing a lockfile: the
network result is reviewed once, and every later build is reproducible.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import emitter, harvester
from .requests import load_requests

DEFAULT_REQUESTS = "data/catalog-rdfc-requests.ttl"
DEFAULT_SNAPSHOT = "data/harvest"
DEFAULT_OUTPUT = "data/catalog-rdfc.ttl"


def _repo_root(explicit: str | None) -> Path:
    """Directory that request paths and defaults resolve against.

    Defaults to the ``pipeline generator`` directory — two levels up
    from this file — so the commands work from any working directory.
    """
    if explicit:
        return Path(explicit).resolve()
    return Path(__file__).resolve().parents[2]


def _cmd_harvest(args: argparse.Namespace) -> int:
    root = _repo_root(args.root)
    requests = load_requests(root / args.requests)
    records, failures = harvester.harvest(requests, root / args.snapshot, root)

    for record in records:
        version = (
            f"@{record.resolved_version}" if record.resolved_version else " (local)"
        )
        print(
            f"  harvested {record.component:32} "
            f"{record.source}:{record.package}{version} -> {record.source_file}"
        )
    print(f"{len(records)} harvested into {args.snapshot}")

    for request, error in failures:
        print(
            f"  FAILED {request.component}: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
    if failures:
        print(f"{len(failures)} request(s) failed", file=sys.stderr)
        return 1
    return 0


def _cmd_generate(args: argparse.Namespace) -> int:
    root = _repo_root(args.root)
    requests = load_requests(root / args.requests)
    text = emitter.generate(requests, root / args.snapshot)

    # Re-parse before writing. The emitter builds Turtle as text, so a
    # rendering bug would otherwise land in a committed file; failing
    # here keeps a broken catalog from ever reaching disk.
    from rdflib import Graph

    Graph().parse(data=text, format="turtle")

    output = root / args.output
    if args.check:
        current = output.read_text(encoding="utf-8") if output.exists() else ""
        if current != text:
            print(
                f"{args.output} is stale - re-run `python -m catalog generate`",
                file=sys.stderr,
            )
            return 1
        print(f"{args.output} is up to date")
        return 0

    output.write_text(text, encoding="utf-8")
    print(f"wrote {args.output} ({len(text.splitlines())} lines, {len(requests)} components)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m catalog",
        description="Generate the RDF-Connect section of the component catalog.",
    )
    parser.add_argument(
        "--root",
        help="Directory paths resolve against (default: the pipeline generator root).",
    )
    parser.add_argument("--requests", default=DEFAULT_REQUESTS)
    parser.add_argument("--snapshot", default=DEFAULT_SNAPSHOT)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("harvest", help="Fetch upstream definitions (network).")

    generate_parser = subparsers.add_parser(
        "generate", help="Rewrite the catalog from the snapshot (offline)."
    )
    generate_parser.add_argument("--output", default=DEFAULT_OUTPUT)
    generate_parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the file on disk differs, without writing.",
    )

    args = parser.parse_args(argv)
    if args.command == "harvest":
        return _cmd_harvest(args)
    args.output = getattr(args, "output", DEFAULT_OUTPUT)
    return _cmd_generate(args)
