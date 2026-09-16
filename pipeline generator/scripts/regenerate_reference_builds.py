"""Regenerate the reference builds tracked under ``out/``.

Three generated projects are committed to the repo (``.gitignore``
whitelists them): they are the byte-level regression baseline for the
generator. ``tests/test_docker_compose_determinism.py`` already asserts
that a fresh run reproduces the committed ``docker-compose.yml``; this
script extends the same idea to every emitted file, by making the
regeneration itself a one-command, repeatable step instead of a cell
buried in ``src/demo.ipynb``.

Usage, from the pipeline generator root::

    ~/anaconda3/envs/pipeline_generator/python.exe scripts/regenerate_reference_builds.py
    git diff --stat out/

For a change that is meant to be behaviour-neutral, ``git diff out/``
must come back empty. ``--check`` reports drift without writing, for the
same reason ``python -m rdfc_catalog_harvest generate --check`` exists.
"""

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from compilers import FileMaterializer, PipelineGenerator  # noqa: E402

#: ``out/`` subdirectory → the pipeline id that produces it. Keep in
#: sync with the directories ``.gitignore`` whitelists; a build that is
#: not tracked does not belong here, since the point of the script is
#: to refresh the committed baseline.
REFERENCE_BUILDS: dict[str, str] = {
    "dishacled-full": "demo:DishacledPipeline",
    "ldio-nifi": "demo_ln:LdioNifiBridgePipeline",
    "nifi-ldio": "demo_nl:NifiLdioBridgePipeline",
}


def build_files(pipeline_id: str) -> dict[Path, str]:
    """Compile ``pipeline_id`` and return ``relative path -> content``.

    Goes through :class:`FileMaterializer`'s ``files`` DataFrame rather
    than writing to disk, so ``--check`` can compare without touching
    the working tree. The relative path is built exactly as
    :meth:`FileMaterializer.write` builds it — ``Path(filepath) /
    filename`` — so a ``tcs:filepath`` of ``.`` normalizes the same way
    here as it does on the write path.
    """
    build = PipelineGenerator(pipeline_id).compile()
    materializer = FileMaterializer(build)
    return {
        Path(str(row["filepath"])) / str(row["filename"]): row["content"]
        for _, row in materializer.files.iterrows()
    }


def check_build(name: str, pipeline_id: str) -> list[str]:
    """Return a list of human-readable drift descriptions for one build."""
    target = _ROOT / "out" / name
    fresh = build_files(pipeline_id)

    drift: list[str] = []
    for relative, content in sorted(fresh.items(), key=lambda item: str(item[0])):
        path = target / relative
        if not path.exists():
            drift.append(f"{name}/{relative.as_posix()}: missing on disk")
        elif path.read_text(encoding="utf-8") != content:
            drift.append(f"{name}/{relative.as_posix()}: differs")
    return drift


def write_build(name: str, pipeline_id: str) -> int:
    """Materialize one build to ``out/<name>``; return the file count."""
    target = _ROOT / "out" / name
    build = PipelineGenerator(pipeline_id).compile()
    written = FileMaterializer(build).write(str(target))
    return len(written)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="regenerate_reference_builds.py",
        description="Rewrite (or check) the reference builds tracked under out/.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if a build on disk differs, without writing.",
    )
    parser.add_argument(
        "--only",
        choices=sorted(REFERENCE_BUILDS),
        help="Regenerate a single build instead of all three.",
    )
    args = parser.parse_args(argv)

    selected = (
        {args.only: REFERENCE_BUILDS[args.only]} if args.only else REFERENCE_BUILDS
    )

    if args.check:
        drift = [
            line
            for name, pipeline_id in selected.items()
            for line in check_build(name, pipeline_id)
        ]
        for line in drift:
            print(line)
        print(f"{len(drift)} file(s) differ" if drift else "up to date")
        return 1 if drift else 0

    for name, pipeline_id in selected.items():
        count = write_build(name, pipeline_id)
        print(f"out/{name}: wrote {count} file(s) from {pipeline_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
