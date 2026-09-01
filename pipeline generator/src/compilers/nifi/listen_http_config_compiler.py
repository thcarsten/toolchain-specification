from rdflib import Graph

from rdfine import GraphReader

from ..base import Compiler
from ..utils import lookup_container_service_name


class NifiListenHttpConfigCompiler(Compiler):
    """Fill in a default config on ``nifi:ListenHTTP`` steps that don't
    have one yet — typically boundary steps inserted by
    :class:`BridgeTransportCompiler`.

    For each unconfigured step:

    - Allocates a port on its container. The default
      (:attr:`default_port`) is used first; on collision with another
      already-configured ``nifi:listeningPort`` on the same container
      the value is bumped by 1 until a free port is found.
    - Attaches ``p-plan:hasInputVar tcs:PipelineConfig`` with an
      ``tcs:embedded`` body carrying ``nifi:listeningPort`` and
      ``nifi:basePath`` (derived from the step's local name).
    - Writes the resulting HTTP endpoint back onto the shared
      cross-container channel the step reads from via
      ``tcs:endpoint`` and ``tcs:port``. The paired Exit compiler on
      the upstream side reads those triples off the same channel
      (which it writes to) and populates its own step's config with
      them.

    Hand-authored ``nifi:ListenHTTP`` steps that already declare a
    ``p-plan:hasInputVar`` are left untouched — the trigger skips
    them.
    """

    default_port: int = 9000

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        self._next_config_index = 0

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        return not graph_reader.select(
            "?step",
            """
            ?step a tcs:InstancePipelineComponent ;
                  prov:specializationOf nifi:ListenHTTP .
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
                  prov:specializationOf nifi:ListenHTTP .
            ?container tcs:runs ?step .
            FILTER NOT EXISTS { ?step p-plan:hasInputVar ?c }
            """,
        )
        used_ports: dict[str, set[int]] = {}
        for _, row in rows.iterrows():
            step, container = row["step"], row["container"]
            port = self._allocate_port(container, used_ports)
            base_path = self._derive_base_path(step)
            self._attach_config(step, port, base_path)
            service = lookup_container_service_name(self.output_reader, container)
            channel = self._lookup_shared_channel(step)
            if service is not None and channel is not None:
                self._annotate_channel(channel, service, port, base_path)

    def _allocate_port(self, container: str, used: dict[str, set[int]]) -> int:
        already_in_use = self._lookup_existing_ports(container)
        taken = used.setdefault(container, set()) | already_in_use
        port = self.default_port
        while port in taken:
            port += 1
        used[container].add(port)
        return port

    def _lookup_existing_ports(self, container: str) -> set[int]:
        rows = self.output_reader.select(
            "?port",
            f"""
            {container} tcs:runs ?step .
            ?step p-plan:hasInputVar/tcs:embedded ?embedded .
            ?embedded nifi:listeningPort ?port .
            """,
        )
        result: set[int] = set()
        for value in rows["port"].to_list():
            try:
                result.add(int(value))
            except (TypeError, ValueError):
                continue
        return result

    def _derive_base_path(self, step: str) -> str:
        return step.rsplit(":", 1)[-1].rsplit("/", 1)[-1]

    def _lookup_shared_channel(self, step: str) -> str | None:
        channels = (
            self.output_reader.filter(sub=step, pred="tcs:readsFrom")
            .df["obj"]
            .to_list()
        )
        return channels[0] if len(channels) == 1 else None

    def _attach_config(self, step: str, port: int, base_path: str) -> None:
        config_id = self._mint_config_id()
        new_triples = self.output_reader.construct(
            f"""
            {step} p-plan:hasInputVar {config_id} .
            {config_id} a tcs:PipelineConfig ;
                tcs:embedded [
                    nifi:listeningPort "{port}" ;
                    nifi:basePath "{base_path}"
                ] .
            """,
            f"{step} a tcs:InstancePipelineComponent .",
        ).graph
        self.output_reader = self.output_reader.add(new_triples)

    def _annotate_channel(
        self, channel: str, service: str, port: int, base_path: str
    ) -> None:
        endpoint = f"http://{service}:{port}/{base_path}"
        new_triples = self.output_reader.construct(
            f"""
            {channel} tcs:endpoint "{endpoint}" ;
                      tcs:port {port} .
            """,
            f"{channel} a tcs:Channel .",
        ).graph
        self.output_reader = self.output_reader.add(new_triples)

    def _mint_config_id(self) -> str:
        cid = f":nifilistenhttpconfig_{self._next_config_index}"
        self._next_config_index += 1
        while self.output_reader.check_exists(cid):
            cid = f":nifilistenhttpconfig_{self._next_config_index}"
            self._next_config_index += 1
        return cid
