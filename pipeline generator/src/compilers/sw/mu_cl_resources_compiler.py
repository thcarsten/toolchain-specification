"""Emit `semantic-works/config/resources/{domain.json,domain.lisp,repository.lisp}`.

mu-cl-resources needs three coordinated config files:
- `domain.json` — the (currently empty) resource declarations.
- `domain.lisp` — the resource-model runtime settings; calls
  `(read-domain-file "domain.json")`.
- `repository.lisp` — declares prefix short-forms usable in `domain.lisp`.

The two `.lisp` files share `dct:format "text/x-common-lisp"`, so
the catalog distinguishes them via two extra `tcs:Config` subclasses
(`tcs:ResourcesDomainConfig` / `tcs:ResourcesRepositoryConfig`).
"""

from rdflib import Graph
from rdfine import GraphReader

from ..base import Compiler
from ..utils import attach_file, read_literal

_COMPONENT_IRI = "sw:mu-cl-resources"
_FILEPATH = "semantic-works/config/resources"

# (config_iri, filename)
_FILES = [
    (":MuResourcesDomainJsonDefault", "domain.json"),
    (":MuResourcesDomainLispDefault", "domain.lisp"),
    (":MuResourcesRepositoryLispDefault", "repository.lisp"),
]


class MuClResourcesCompiler(Compiler):
    """Attach the three `mu-cl-resources` config files to the build."""

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Triggered when a container instantiates `sw:mu-cl-resources`."""
        return not graph_reader.filter(
            pred="tcs:instantiates",
            obj=_COMPONENT_IRI,
        ).df.empty

    def compile(self) -> Graph:
        self.attach_resources_config_files()
        return self.output_reader.graph

    def attach_resources_config_files(self) -> None:
        """Attach all three config files listed in :data:`_FILES`."""
        for config_iri, filename in _FILES:
            self.output_reader = attach_file(
                self.output_reader,
                filename=filename,
                filepath=_FILEPATH,
                content=read_literal(self.output_reader, config_iri),
            )
