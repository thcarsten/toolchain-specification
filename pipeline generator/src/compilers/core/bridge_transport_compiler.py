from rdflib import Graph

from rdfine import GraphReader

from ..base import Compiler


class BridgeTransportCompiler(Compiler):
    """Insert missing Entry/Exit boundary steps for cross-container channels.

    A ``tcs:Channel`` whose writer step and reader step(s) run on
    different ``tcs:DockerContainer``s needs a bridge step on each
    side, whose specialized component is typed
    :class:`tcs:ExitBoundaryComponent` (upstream) or
    :class:`tcs:EntryBoundaryComponent` (downstream). Whether such a
    step is already present on either side is the sole decision
    criterion — a user-declared boundary component is trusted at
    face value, regardless of transport implementation details.

    For each cross-container channel this compiler finds:

    - If both sides already carry a boundary-typed step, the channel
      is left alone. This is how a pipeline author signals "the
      cross-container hop here is my responsibility" — the compiler
      makes no assumptions about how the user's Entry/Exit talk to
      each other.
    - Otherwise, the compiler defaults to an HTTP bridge and looks up
      catalog candidates: an :class:`tcs:ExitBoundaryComponent` /
      :class:`tcs:EntryBoundaryComponent` whose ``tcs:channelType``
      matches :attr:`default_channel_type` and whose ``dct:requires``
      chain reaches the microservice running on the corresponding
      container. A missing step is inserted with
      ``prov:specializationOf`` the catalog component,
      ``p-plan:isStepOfPlan`` the current pipeline, and ``tcs:runs``
      on the correct container. Channel wiring is rewritten so the
      original cross-container channel is only read by the inserted
      Entry and only written by the inserted Exit; fresh intra-
      container companion channels connect the original writer to the
      Exit and the Entry to the original reader(s).
    - Topologies the MVP doesn't handle (writers in multiple
      containers, readers spanning more than two containers total)
      are skipped and left for
      :class:`tcs:UnsupportedChannelTopologyShape` to flag.

    ``tcs:channelType`` on a catalog boundary component is purely
    compiler-facing metadata used to pick a candidate when auto-
    inserting a default HTTP bridge; boundary components need not
    declare it. Non-HTTP bridges (SPARQL update, message queues,
    ...) are the pipeline author's responsibility to wire up
    explicitly.

    Configuration of the inserted steps (transport metadata written
    onto the channel — ``tcs:endpoint`` / ``tcs:port``, plus each
    step's own config body) is the concern of the per-boundary
    config compilers, not this compiler.
    """

    #: Catalog ``tcs:channelType`` used to pick auto-insertion candidates.
    default_channel_type: str = "tcs:HttpChannel"

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        self._next_bridgestep_index = 0
        self._next_channel_index = 0

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Fires once :class:`PipelineAssembler` has assigned steps to
        more than one ``tcs:DockerContainer`` — i.e. the pipeline is
        spread across containers.
        """
        containers = (
            graph_reader.filter(pred="tcs:runs").df["sub"].drop_duplicates().to_list()
        )
        return len(containers) > 1

    def compile(self) -> Graph:
        self.bridge_cross_container_channels()
        return self.output_reader.graph

    def bridge_cross_container_channels(self) -> None:
        for channel in self._list_cross_container_channels():
            self._bridge_one_channel(channel)

    def _list_cross_container_channels(self) -> list[str]:
        rows = self.output_reader.select(
            "?ch",
            """
            ?ch a tcs:Channel .
            ?writer tcs:writesTo ?ch .
            ?reader tcs:readsFrom ?ch .
            ?cW tcs:runs ?writer .
            ?cR tcs:runs ?reader .
            FILTER (?cW != ?cR)
            """,
        )
        return sorted(rows["ch"].drop_duplicates().to_list())

    def _bridge_one_channel(self, channel: str) -> None:
        writer_container = self._lookup_single_writer_container(channel)
        if writer_container is None:
            # Zero writers or writers in more than one container — the
            # MVP can't decide which container "owns" the Exit side.
            # Multi-writer fan-in is flagged by
            # tcs:UnsupportedChannelTopologyShape.
            return

        reader_containers = self._lookup_reader_containers(channel)
        if len(reader_containers) != 1:
            # Zero readers, or readers spanning more than one
            # downstream container — likewise MVP-out-of-scope.
            return
        reader_container = next(iter(reader_containers))

        exit_ok = self._has_exit_side(channel)
        entry_ok = self._has_entry_side(channel)
        if exit_ok and entry_ok:
            return
        if exit_ok != entry_ok:
            present, missing = ("Exit", "Entry") if exit_ok else ("Entry", "Exit")
            raise ValueError(
                f"Cross-container channel {channel} has an {present} "
                f"boundary step but no {missing} boundary step on the "
                "other side. Either declare both sides of the bridge "
                "explicitly or leave both undeclared so "
                "BridgeTransportCompiler can insert an HTTP pair from "
                "the catalog."
            )

        exit_component = self._lookup_boundary_component(
            "tcs:ExitBoundaryComponent", writer_container
        )
        entry_component = self._lookup_boundary_component(
            "tcs:EntryBoundaryComponent", reader_container
        )
        if exit_component is None or entry_component is None:
            # tcs:CatalogMissingBridgeShape flags this after the compile.
            return

        pipeline_id = self._lookup_pipeline_id()
        upstream_channel = self._mint_channel_id()
        exit_step = self._mint_bridgestep_id()
        self._rewrite_writer_side(
            channel,
            upstream_channel,
            exit_step,
            exit_component,
            writer_container,
            pipeline_id,
        )

        downstream_channel = self._mint_channel_id()
        entry_step = self._mint_bridgestep_id()
        self._rewrite_reader_side(
            channel,
            downstream_channel,
            entry_step,
            entry_component,
            reader_container,
            pipeline_id,
        )

    def _has_exit_side(self, channel: str) -> bool:
        return self.output_reader.ask(f"""
            ?w tcs:writesTo {channel} ;
               prov:specializationOf ?c .
            ?c a tcs:ExitBoundaryComponent .
            """)

    def _has_entry_side(self, channel: str) -> bool:
        return self.output_reader.ask(f"""
            ?r tcs:readsFrom {channel} ;
               prov:specializationOf ?c .
            ?c a tcs:EntryBoundaryComponent .
            """)

    def _lookup_single_writer_container(self, channel: str) -> str | None:
        rows = self.output_reader.select(
            "?c",
            f"""
            ?w tcs:writesTo {channel} .
            ?c tcs:runs ?w .
            """,
        )
        containers = set(rows["c"].to_list())
        return next(iter(containers)) if len(containers) == 1 else None

    def _lookup_reader_containers(self, channel: str) -> set[str]:
        rows = self.output_reader.select(
            "?c",
            f"""
            ?r tcs:readsFrom {channel} .
            ?c tcs:runs ?r .
            """,
        )
        return set(rows["c"].to_list())

    def _lookup_boundary_component(
        self, boundary_class: str, container: str
    ) -> str | None:
        rows = self.output_reader.select(
            "?comp",
            f"""
            ?comp a {boundary_class} ;
                  tcs:channelType {self.default_channel_type} ;
                  dct:requires* ?microservice .
            {container} tcs:instantiates ?microservice .
            """,
        )
        candidates = sorted(set(rows["comp"].to_list()))
        return candidates[0] if len(candidates) == 1 else None

    def _lookup_pipeline_id(self) -> str:
        return (
            self.output_reader.filter(pred="rdf:type", obj="tcs:PipelineDefinition")
            .df["sub"]
            .to_list()[0]
        )

    def _rewrite_writer_side(
        self,
        channel: str,
        upstream_channel: str,
        exit_step: str,
        exit_component: str,
        container: str,
        pipeline_id: str,
    ) -> None:
        new_triples = self.output_reader.construct(
            f"""
            {exit_step} a tcs:InstancePipelineComponent ;
                prov:specializationOf {exit_component} ;
                p-plan:isStepOfPlan {pipeline_id} ;
                tcs:readsFrom {upstream_channel} ;
                tcs:writesTo {channel} .
            {container} tcs:runs {exit_step} ;
                tcs:instantiates {exit_component} .
            {upstream_channel} a tcs:Channel , {self.default_channel_type} .
            ?w tcs:writesTo {upstream_channel} .
            """,
            f"?w tcs:writesTo {channel} . {container} tcs:runs ?w .",
        ).graph
        self.output_reader = self.output_reader.add(new_triples)
        remove_triples = self.output_reader.construct(
            f"?w tcs:writesTo {channel} .",
            f"?w tcs:writesTo {channel} . {container} tcs:runs ?w . "
            f"FILTER (?w != {exit_step})",
        ).graph
        self.output_reader = self.output_reader.remove(remove_triples)

    def _rewrite_reader_side(
        self,
        channel: str,
        downstream_channel: str,
        entry_step: str,
        entry_component: str,
        container: str,
        pipeline_id: str,
    ) -> None:
        new_triples = self.output_reader.construct(
            f"""
            {entry_step} a tcs:InstancePipelineComponent ;
                prov:specializationOf {entry_component} ;
                p-plan:isStepOfPlan {pipeline_id} ;
                tcs:readsFrom {channel} ;
                tcs:writesTo {downstream_channel} .
            {container} tcs:runs {entry_step} ;
                tcs:instantiates {entry_component} .
            {downstream_channel} a tcs:Channel , {self.default_channel_type} .
            ?r tcs:readsFrom {downstream_channel} .
            """,
            f"?r tcs:readsFrom {channel} . {container} tcs:runs ?r .",
        ).graph
        self.output_reader = self.output_reader.add(new_triples)
        remove_triples = self.output_reader.construct(
            f"?r tcs:readsFrom {channel} .",
            f"?r tcs:readsFrom {channel} . {container} tcs:runs ?r . "
            f"FILTER (?r != {entry_step})",
        ).graph
        self.output_reader = self.output_reader.remove(remove_triples)

    def _mint_channel_id(self) -> str:
        cid = f":channel_bridge_{self._next_channel_index}"
        self._next_channel_index += 1
        while self.output_reader.check_exists(cid):
            cid = f":channel_bridge_{self._next_channel_index}"
            self._next_channel_index += 1
        return cid

    def _mint_bridgestep_id(self) -> str:
        sid = f":bridgestep_{self._next_bridgestep_index}"
        self._next_bridgestep_index += 1
        while self.output_reader.check_exists(sid):
            sid = f":bridgestep_{self._next_bridgestep_index}"
            self._next_bridgestep_index += 1
        return sid
