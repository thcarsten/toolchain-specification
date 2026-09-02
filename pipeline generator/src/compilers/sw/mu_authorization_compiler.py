"""Emit `semantic-works/config/authorization/config.lisp`.

Boilerplate `mu-authorization` ACL: one public graph, everyone can
read/write. Real pipelines will want tighter graph specifications and
role-scoped grants; those go in a `tcs:embedded` override on the step
that uses `sw:mu-authorization`.
"""

from rdflib import Graph
from rdfine import GraphReader

from ..compiler_abc import Compiler
from ..utils import attach_file, read_literal

_COMPONENT_IRI = "sw:mu-authorization"
_CONFIG_IRI = ":MuAuthorizationConfigLispDefault"
_FILEPATH = "semantic-works/config/authorization"
_FILENAME = "config.lisp"


class MuAuthorizationCompiler(Compiler):
    """Attach `mu-authorization`'s stock `config.lisp` to the build."""

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Triggered when a container instantiates `sw:mu-authorization`."""
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
