from rdflib import Graph

import pandas as pd
from rdfine import GraphReader

from ..compiler_abc import Compiler


class NifiInvokeHttpConfigCompiler(Compiler):
    """Fill in a default config on ``nifi:InvokeHTTP`` steps that don't
    have one yet — typically boundary steps inserted by
    :class:`BridgeTransportCompiler`.

    Reads the ``tcs:endpoint`` written onto the shared cross-container
    channel by the paired Entry compiler on the downstream container,
    then attaches ``p-plan:hasInputVar tcs:PipelineConfig`` with an
    ``tcs:embedded`` body carrying ``nifi:httpMethod`` (defaults to
    ``POST``) and ``nifi:httpUrl``.

    All five InvokeHTTP relationships (``Response``, ``Failure``,
    ``Retry``, ``No Retry``, ``Original``) are declared as
    ``nifi:autoTerminatedRelationship`` on the catalog component, and
    the bridge-inserted step has no in-container reader on its
    outgoing channel, so ``NifiConfigCompiler`` emits no CONNECTION
    for it and every relationship stays auto-terminated. No
    ``nifi:route`` is authored here for that reason.

    Fires only when the write channel already carries a
    ``tcs:endpoint`` — so a paired Entry compiler must have run
    first. Hand-authored ``nifi:InvokeHTTP`` steps that already
    declare a ``p-plan:hasInputVar`` are left untouched.
    """

    default_http_method: str = "POST"

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        self._next_config_index = 0

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        return not graph_reader.select(
            "?step",
            """
            ?step a tcs:InstancePipelineComponent ;
                  prov:specializationOf nifi:InvokeHTTP ;
                  tcs:writesTo ?channel .
            ?channel tcs:endpoint ?endpoint .
            FILTER NOT EXISTS { ?step p-plan:hasInputVar ?c }
            """,
        ).empty

    def compile(self) -> Graph:
        self.configure_unconfigured_steps()
        return self.output_reader.graph

    def configure_unconfigured_steps(self) -> None:
        rows = self.output_reader.select(
            "?step ?endpoint ?content_type",
            """
            ?step a tcs:InstancePipelineComponent ;
                  prov:specializationOf nifi:InvokeHTTP ;
                  tcs:writesTo ?channel .
            ?channel tcs:endpoint ?endpoint .
            OPTIONAL { ?channel tcs:contentType ?content_type . }
            FILTER NOT EXISTS { ?step p-plan:hasInputVar ?c }
            """,
        )
        for _, row in rows.iterrows():
            content_type = row["content_type"]
            self._attach_config(
                row["step"],
                row["endpoint"],
                None if pd.isna(content_type) else str(content_type),
            )

    def _attach_config(
        self, step: str, endpoint: str, content_type: str | None
    ) -> None:
        config_id = self._mint_config_id()
        content_type_line = (
            f'; nifi:contentType "{content_type}"' if content_type else ""
        )
        new_triples = self.output_reader.construct(
            f"""
            {step} p-plan:hasInputVar {config_id} .
            {config_id} a tcs:PipelineConfig ;
                tcs:embedded [
                    nifi:httpMethod "{self.default_http_method}" ;
                    nifi:httpUrl "{endpoint}"
                    {content_type_line}
                ] .
            """,
            f"{step} a tcs:InstancePipelineComponent .",
        ).graph
        self.output_reader = self.output_reader.add(new_triples)

    def _mint_config_id(self) -> str:
        cid = f":nifiinvokehttpconfig_{self._next_config_index}"
        self._next_config_index += 1
        while self.output_reader.check_exists(cid):
            cid = f":nifiinvokehttpconfig_{self._next_config_index}"
            self._next_config_index += 1
        return cid
