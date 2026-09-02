from rdflib import Graph

from rdfine import GraphReader

from ..compiler_abc import Compiler
from ..utils import lookup_container_service_name


class LdioHttpInConfigCompiler(Compiler):
    """Fill in a default config on ``ldio:HttpIn`` steps that don't
    have one yet — typically boundary steps inserted by
    :class:`BridgeTransportCompiler`.

    ``ldio:HttpIn`` has no per-instance config properties: the port
    is fixed by the orchestrator (:attr:`orchestrator_port`) and the
    path is derived from the pipeline name by LDIO convention. The
    compiler still attaches an empty ``tcs:PipelineConfig`` so
    downstream compilers can rely on ``p-plan:hasInputVar`` being
    present on every step, and writes the resulting HTTP endpoint
    onto the shared cross-container channel the step reads from via
    ``tcs:endpoint`` and ``tcs:port``. The paired Exit compiler on
    the upstream side reads those triples off the same channel
    (which it writes to) and populates its own step's config.

    Hand-authored ``ldio:HttpIn`` steps that already declare a
    ``p-plan:hasInputVar`` are left untouched — the trigger skips
    them.
    """

    orchestrator_port: int = 8080
    #: Content-Type advertised to the paired Exit compiler through the
    #: channel's ``tcs:contentType``; the Exit compiler forwards it as the
    #: outbound HTTP request's Content-Type header so LDIO's RdfAdapter
    #: can pick a parser. N-Triples is the smallest RDF serialisation the
    #: adapter accepts and a safe default for auto-inserted bridges.
    default_content_type: str = "application/n-triples"

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        self._next_config_index = 0

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        segments_tagged = not graph_reader.filter(
            pred="dct:creator", obj="tcs:SegmentTagger"
        ).df.empty
        if not segments_tagged:
            return False
        return not graph_reader.select(
            "?step",
            """
            ?step a tcs:InstancePipelineComponent ;
                  prov:specializationOf ldio:HttpIn .
            FILTER NOT EXISTS { ?step p-plan:hasInputVar ?c }
            """,
        ).empty

    def compile(self) -> Graph:
        self.configure_unconfigured_steps()
        return self.output_reader.graph

    def configure_unconfigured_steps(self) -> None:
        rows = self.output_reader.select(
            "?step ?container",
            """
            ?step a tcs:InstancePipelineComponent ;
                  prov:specializationOf ldio:HttpIn .
            ?container tcs:runs ?step .
            FILTER NOT EXISTS { ?step p-plan:hasInputVar ?c }
            """,
        )
        for _, row in rows.iterrows():
            step, container = row["step"], row["container"]
            path = self._derive_path(step)
            self._attach_config(step)
            service = lookup_container_service_name(self.output_reader, container)
            channel = self._lookup_shared_channel(step)
            if service is not None and channel is not None:
                self._annotate_channel(channel, service, path)

    def _derive_path(self, step: str) -> str:
        # LDIO serves each pipeline at ``/{pipeline_name}`` where pipeline_name
        # is the ``name:`` key in its yaml — LdioConfigCompiler emits that as
        # the step's ``tcs:segment`` local name.
        segments = (
            self.output_reader.filter(sub=step, pred="tcs:segment").df["obj"].to_list()
        )
        source = segments[0] if segments else step
        local = source.rsplit(":", 1)[-1].rsplit("/", 1)[-1]
        return f"/{local}"

    def _lookup_shared_channel(self, step: str) -> str | None:
        channels = (
            self.output_reader.filter(sub=step, pred="tcs:readsFrom")
            .df["obj"]
            .to_list()
        )
        return channels[0] if len(channels) == 1 else None

    def _attach_config(self, step: str) -> None:
        config_id = self._mint_config_id()
        new_triples = self.output_reader.construct(
            f"""
            {step} p-plan:hasInputVar {config_id} .
            {config_id} a tcs:PipelineConfig ;
                tcs:embedded [] .
            """,
            f"{step} a tcs:InstancePipelineComponent .",
        ).graph
        self.output_reader = self.output_reader.add(new_triples)

    def _annotate_channel(self, channel: str, service: str, path: str) -> None:
        endpoint = f"http://{service}:{self.orchestrator_port}{path}"
        new_triples = self.output_reader.construct(
            f"""
            {channel} tcs:endpoint "{endpoint}" ;
                      tcs:port {self.orchestrator_port} ;
                      tcs:contentType "{self.default_content_type}" .
            """,
            f"{channel} a tcs:Channel .",
        ).graph
        self.output_reader = self.output_reader.add(new_triples)

    def _mint_config_id(self) -> str:
        cid = f":httpinconfig_{self._next_config_index}"
        self._next_config_index += 1
        while self.output_reader.check_exists(cid):
            cid = f":httpinconfig_{self._next_config_index}"
            self._next_config_index += 1
        return cid
