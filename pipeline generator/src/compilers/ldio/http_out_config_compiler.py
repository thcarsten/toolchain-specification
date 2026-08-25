from rdflib import Graph

from rdfine import GraphReader, receive_first

from ..base import Compiler


class LdioHttpOutConfigCompiler(Compiler):
    """Fill in a default config on ``ldio:HttpOut`` steps that don't
    have one yet — typically boundary steps inserted by
    :class:`BridgeTransportCompiler`.

    Reads the ``tcs:endpoint`` written onto the shared cross-container
    channel by the paired Entry compiler on the downstream container,
    then attaches ``p-plan:hasInputVar tcs:PipelineConfig`` with an
    ``tcs:embedded`` body carrying ``ldio:endpoint`` (mandatory per
    the catalog configShape) and a default ``ldio:rdf-writer`` block
    posting ``application/ld+json``.

    Fires only when the read channel already carries a
    ``tcs:endpoint`` — so a paired Entry compiler must have run
    first. Hand-authored ``ldio:HttpOut`` steps that already declare
    a ``p-plan:hasInputVar`` are left untouched.
    """

    default_content_type: str = "application/ld+json"

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        self._next_config_index = 0

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        return not graph_reader.select(
            "?step",
            """
            ?step a tcs:InstancePipelineComponent ;
                  prov:specializationOf ldio:HttpOut ;
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
            "?step ?endpoint",
            """
            ?step a tcs:InstancePipelineComponent ;
                  prov:specializationOf ldio:HttpOut ;
                  tcs:writesTo ?channel .
            ?channel tcs:endpoint ?endpoint .
            FILTER NOT EXISTS { ?step p-plan:hasInputVar ?c }
            """,
        )
        for _, row in rows.iterrows():
            self._attach_config(row["step"], row["endpoint"])

    def _attach_config(self, step: str, endpoint: str) -> None:
        config_id = self._mint_config_id()
        new_triples = self.output_reader.construct(
            f"""
            {step} p-plan:hasInputVar {config_id} .
            {config_id} a tcs:PipelineConfig ;
                tcs:embedded [
                    ldio:endpoint "{endpoint}" ;
                    ldio:rdf-writer [ ldio:content-type "{self.default_content_type}" ]
                ] .
            """,
            f"{step} a tcs:InstancePipelineComponent .",
        ).graph
        self.output_reader = self.output_reader.add(new_triples)

    def _mint_config_id(self) -> str:
        cid = f":httpoutconfig_{self._next_config_index}"
        self._next_config_index += 1
        while self.output_reader.check_exists(cid):
            cid = f":httpoutconfig_{self._next_config_index}"
            self._next_config_index += 1
        return cid
