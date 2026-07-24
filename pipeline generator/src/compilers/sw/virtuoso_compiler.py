"""Emit `semantic-works/config/virtuoso/virtuoso.ini`.

Reads the verbatim body of the `tcs:DefaultConfig` attached to
`sw:triple-store` and drops it at the path that the demonstrator's
compose file mounts as `/data/virtuoso.ini`.
"""

from rdflib import Graph
from rdfine import GraphReader

from ..base import Compiler
from ..utils import attach_file, read_literal

_COMPONENT_IRI = "sw:triple-store"
_CONFIG_IRI = ":VirtuosoIniDefault"
_FILEPATH = "semantic-works/config/virtuoso"
_FILENAME = "virtuoso.ini"


class VirtuosoCompiler(Compiler):
    """Attach Virtuoso's stock `virtuoso.ini` to the build."""

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Triggered when a container instantiates `sw:triple-store`."""
        return not graph_reader.filter(
            pred="tcs:instantiates",
            obj=_COMPONENT_IRI,
        ).df.empty

    def compile(self) -> Graph:
        self.output_reader = attach_file(
            self.output_reader,
            filename=_FILENAME,
            filepath=_FILEPATH,
            content=read_literal(self.output_reader, _CONFIG_IRI),
        )
        return self.output_reader.graph
