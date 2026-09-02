"""Emit `semantic-works/config/dispatcher/dispatcher.ex`.

Boilerplate `mu-dispatcher` routes: forwards `/sparql` to the
`database` service; 404 on everything else. Pipelines that need
richer routing would ship their own `tcs:embedded` overrides —
still TODO in the semantic model.
"""

from rdflib import Graph
from rdfine import GraphReader

from ..compiler_abc import Compiler
from ..utils import attach_file, read_literal

_COMPONENT_IRI = "sw:mu-dispatcher"
_CONFIG_IRI = ":MuDispatcherExDefault"
_FILEPATH = "semantic-works/config/dispatcher"
_FILENAME = "dispatcher.ex"


class MuDispatcherCompiler(Compiler):
    """Attach `mu-dispatcher`'s stock `dispatcher.ex` to the build."""

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Triggered when a container instantiates `sw:mu-dispatcher`."""
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
