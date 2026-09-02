"""Materialize a compiled build graph to disk.

``PipelineGenerator`` produces a self-describing build graph in which
every generated artifact is attached to the ``tcs:PipelineBuild`` as an
``spdx:File`` node carrying ``tcs:filename``, ``tcs:filepath`` and
``tcs:literal``. :class:`FileMaterializer` is the IO-side counterpart:
it walks those nodes and writes each file out under a target directory.

It sits outside the :class:`Compiler` hierarchy because compilers are
graph-to-graph transformations, while ``FileMaterializer`` crosses the
filesystem boundary.
"""

from pathlib import Path

import pandas as pd
from rdflib import Graph

from rdfine import GraphReader


class FileMaterializer:
    """Write the ``spdx:File`` nodes of a build graph to a target directory.

    Typical use::

        gen = PipelineGenerator("demo:DishacledPipeline")
        build_graph = gen.compile()

        materializer = FileMaterializer(build_graph)
        materializer.write("./out/dishacled-full")

    The collected file records are exposed on :attr:`files` as a
    ``pandas.DataFrame`` with columns ``filepath``, ``filename`` and
    ``content`` — useful for inspecting what would be written without
    touching the filesystem.
    """

    def __init__(self, build_graph: Graph) -> None:
        self.graph_reader = GraphReader(build_graph)
        self.files: pd.DataFrame = self._collect_files()

    def _collect_files(self) -> pd.DataFrame:
        return self.graph_reader.select(
            "?filepath ?filename ?content",
            (
                "?file a spdx:File ;"
                "      tcs:filename ?filename ;"
                "      tcs:filepath ?filepath ;"
                "      tcs:literal  ?content ."
            ),
        )

    def write(self, target_dir: str | Path) -> list[Path]:
        """Write every collected file under ``target_dir``.

        The target directory is created if it does not exist. Existing
        files at the same path are overwritten. Returns the absolute
        paths that were written, in iteration order.

        Raises ``ValueError`` if a ``tcs:filepath`` would resolve to a
        location outside ``target_dir`` (path-traversal guard).
        """
        target_root = Path(target_dir).resolve()
        target_root.mkdir(parents=True, exist_ok=True)

        written: list[Path] = []
        for _, row in self.files.iterrows():
            rel = Path(str(row["filepath"])) / str(row["filename"])
            full = (target_root / rel).resolve()
            try:
                full.relative_to(target_root)
            except ValueError as exc:
                raise ValueError(
                    f"file path {rel!s} escapes target directory {target_root!s}"
                ) from exc
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(str(row["content"]), encoding="utf-8")
            written.append(full)
        if ".env.example" in self.files["filename"].astype(str).values:
            env_file = target_root / ".env"
            if not env_file.exists():
                print("Deployment secrets are required. " f"Supply a {env_file}. ")
        return written
