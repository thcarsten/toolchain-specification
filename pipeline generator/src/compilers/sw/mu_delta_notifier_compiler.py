"""Emit `semantic-works/config/delta/rules.js`.

Currently ships the demonstrator's `rules.js` verbatim (which includes
both the boilerplate mu-cl-resources fan-out rule *and* the
`rdf:type oslc:Error → error-alert` cross-framework rule). Longer-term,
the second rule should be derived from the pipeline definition once
`tcs:Channel` supports cross-framework transports; see AGENTS.md §8.
"""

from rdflib import Graph
from rdfine import GraphReader

from ..compiler_abc import Compiler
from ..utils import attach_file, read_literal

_COMPONENT_IRI = "sw:mu-delta-notifier"
_CONFIG_IRI = ":MuDeltaNotifierRulesJsDefault"
_FILEPATH = "semantic-works/config/delta"
_FILENAME = "rules.js"


class MuDeltaNotifierCompiler(Compiler):
    """Attach `mu-delta-notifier`'s stock `rules.js` to the build."""

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Triggered when a container instantiates `sw:mu-delta-notifier`."""
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
