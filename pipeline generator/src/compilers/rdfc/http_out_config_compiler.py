from rdflib import Graph

from rdfine import GraphReader

from ..compiler_abc import Compiler


class RdfcHttpOutConfigCompiler(Compiler):
    """Fill in a default config on ``rdfc:HttpOut`` steps that don't
    have one yet — typically boundary steps inserted by
    :class:`BridgeTransportCompiler`.

    Reads the ``tcs:endpoint`` written onto the shared cross-container
    channel by the paired Entry compiler on the downstream container,
    then attaches ``p-plan:hasInputVar tcs:PipelineConfig`` with an
    ``tcs:embedded`` body carrying ``rdfc:endpoint``. The RDF-Connect
    ``rdfc:HttpOut`` component has no configShape declared in the
    catalog, so the default field name is a best guess; a pipeline
    that needs different behaviour should hand-author the config.

    Fires only when the write channel already carries a
    ``tcs:endpoint`` — so a paired Entry compiler must have run
    first. Hand-authored ``rdfc:HttpOut`` steps that already declare
    a ``p-plan:hasInputVar`` are left untouched.
    """

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        self._next_config_index = 0

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        return not graph_reader.select(
            "?step",
            """
            ?step a tcs:InstancePipelineComponent ;
                  prov:specializationOf rdfc:HttpOut ;
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
                  prov:specializationOf rdfc:HttpOut ;
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
                    rdfc:endpoint "{endpoint}"
                ] .
            """,
            f"{step} a tcs:InstancePipelineComponent .",
        ).graph
        self.output_reader = self.output_reader.add(new_triples)

    def _mint_config_id(self) -> str:
        cid = f":rdfchttpoutconfig_{self._next_config_index}"
        self._next_config_index += 1
        while self.output_reader.check_exists(cid):
            cid = f":rdfchttpoutconfig_{self._next_config_index}"
            self._next_config_index += 1
        return cid
