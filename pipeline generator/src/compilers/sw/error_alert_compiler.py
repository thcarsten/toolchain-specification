"""Emit `semantic-works/config/error-alert/{config.json,error.hbs}`.

`loket-error-alert-service` reads:
- `config.json` — base URI, service URI, mail-folder IRI, graph IRI.
- `error.hbs` — Handlebars template for the alert email body.

Different `dct:format`s (`application/json` vs `text/html`)
disambiguate the two on the shared `sw:loket-error-alert-service`
component.
"""

from rdflib import Graph
from rdfine import GraphReader

from ..compiler_abc import Compiler
from ..utils import attach_file, read_literal

_COMPONENT_IRI = "sw:loket-error-alert-service"
_FILEPATH = "semantic-works/config/error-alert"

# (config_iri, filename)
_FILES = [
    (":ErrorAlertConfigJsonDefault", "config.json"),
    (":ErrorAlertTemplateHbsDefault", "error.hbs"),
]


class ErrorAlertCompiler(Compiler):
    """Attach `loket-error-alert-service`'s two config files to the build."""

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Triggered when a container instantiates `sw:loket-error-alert-service`."""
        return not graph_reader.filter(
            pred="tcs:instantiates",
            obj=_COMPONENT_IRI,
        ).df.empty

    def compile(self) -> Graph:
        self.attach_error_alert_config_files()
        return self.output_reader.graph

    def attach_error_alert_config_files(self) -> None:
        """Attach both config files listed in :data:`_FILES`."""
        for config_iri, filename in _FILES:
            self.output_reader = attach_file(
                self.output_reader,
                filename=filename,
                filepath=_FILEPATH,
                content=read_literal(self.output_reader, config_iri),
            )
