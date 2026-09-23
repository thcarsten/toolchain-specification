from rdflib import Graph

from rdfine import GraphReader

from ..compiler_abc import Compiler
from ..utils import lookup_seeded_pipeline_id


class BridgeTransportCompiler(Compiler):
    """Insert missing Entry/Exit boundary steps for cross-container
    Connections.

    A ``tcs:Connection`` whose ``tcs:from`` step and ``tcs:to`` step run
    on different ``tcs:DockerContainer``s needs a bridge step on each
    side, whose specialized component is typed
    :class:`tcs:ExitBoundaryComponent` (upstream) or
    :class:`tcs:EntryBoundaryComponent` (downstream). Whether such a
    step is already present on either side is the sole decision
    criterion — a user-declared boundary component is trusted at
    face value, regardless of transport implementation details.

    For each cross-container Connection this compiler finds:

    - If both sides already carry a boundary-typed step, the Connection
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
      on the correct container. The Connection is **split** into three:
      the original Connection is repointed so its ``tcs:from`` becomes
      the inserted Exit and its ``tcs:to`` the inserted Entry — this is
      what keeps its own IRI resolvable wherever a framework config
      still names it directly (see the channel-to-connection plan's
      [D8]) — and two fresh intra-container Connections wire the
      original ``tcs:from`` step to the Exit, and the Entry to the
      original ``tcs:to`` step.
    - A Connection is always 1:1, so there is no multi-writer/
      multi-reader ambiguity left for this compiler to reject — unlike
      the ``tcs:Channel`` model it replaces. Topology concerns now live
      one level up, over *groups* of Connections sharing an endpoint —
      see :class:`tcs:UnsupportedChannelTopologyShape`.

    ``tcs:channelType`` on a catalog boundary component is purely
    compiler-facing metadata used to pick a candidate when auto-
    inserting a default HTTP bridge; boundary components need not
    declare it. Non-HTTP bridges (SPARQL update, message queues,
    ...) are the pipeline author's responsibility to wire up
    explicitly.

    Configuration of the inserted steps (transport metadata written
    onto the Connection — ``tcs:endpoint`` / ``tcs:port``, plus each
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
            ?ch a tcs:Connection ;
                tcs:from ?writer ;
                tcs:to ?reader .
            ?cW tcs:runs ?writer .
            ?cR tcs:runs ?reader .
            FILTER (?cW != ?cR)
            """,
        )
        return sorted(rows["ch"].drop_duplicates().to_list())

    def _bridge_one_channel(self, channel: str) -> None:
        writer, reader = self._lookup_endpoints(channel)
        writer_container = self._lookup_container(writer)
        reader_container = self._lookup_container(reader)

        exit_ok = self._has_exit_side(writer)
        entry_ok = self._has_entry_side(reader)
        if exit_ok and entry_ok:
            return
        if exit_ok != entry_ok:
            present, missing = ("Exit", "Entry") if exit_ok else ("Entry", "Exit")
            raise ValueError(
                f"Cross-container Connection {channel} has an {present} "
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
            writer,
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
            reader,
            downstream_channel,
            entry_step,
            entry_component,
            reader_container,
            pipeline_id,
        )

    def _lookup_endpoints(self, channel: str) -> tuple[str, str]:
        row = self.output_reader.select(
            "?writer ?reader",
            f"{channel} tcs:from ?writer ; tcs:to ?reader .",
        ).iloc[0]
        return row["writer"], row["reader"]

    def _has_exit_side(self, step: str) -> bool:
        return self.output_reader.ask(f"""
            {step} prov:specializationOf ?c .
            ?c a tcs:ExitBoundaryComponent .
            """)

    def _has_entry_side(self, step: str) -> bool:
        return self.output_reader.ask(f"""
            {step} prov:specializationOf ?c .
            ?c a tcs:EntryBoundaryComponent .
            """)

    def _lookup_container(self, step: str) -> str:
        row = self.output_reader.select(
            "?c", f"?c tcs:runs {step} ."
        ).iloc[0]
        return row["c"]

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
        return lookup_seeded_pipeline_id(self.output_reader)

    def _rewrite_writer_side(
        self,
        channel: str,
        writer: str,
        upstream_channel: str,
        exit_step: str,
        exit_component: str,
        container: str,
        pipeline_id: str,
    ) -> None:
        """Split ``channel`` on its writer side: a fresh intra-container
        Connection carries ``writer -> exit_step``, and ``channel``
        itself is repointed to start at ``exit_step`` instead — keeping
        its own IRI as the cross-container hop, which is what lets a
        framework config that still names it directly (e.g.
        ``rdfc:output``) keep resolving. See the class docstring.
        """
        new_triples = self.output_reader.construct(
            f"""
            {exit_step} a tcs:InstancePipelineComponent ;
                prov:specializationOf {exit_component} ;
                p-plan:isStepOfPlan {pipeline_id} .
            {container} tcs:runs {exit_step} ;
                tcs:instantiates {exit_component} .
            {upstream_channel} a tcs:Connection , {self.default_channel_type} ;
                tcs:from {writer} ; tcs:to {exit_step} .
            {channel} tcs:from {exit_step} .
            """,
            "?s ?p ?o .",
        ).graph
        self.output_reader = self.output_reader.add(new_triples)
        remove_triples = self.output_reader.construct(
            f"{channel} tcs:from {writer} .", "?s ?p ?o ."
        ).graph
        self.output_reader = self.output_reader.remove(remove_triples)

    def _rewrite_reader_side(
        self,
        channel: str,
        reader: str,
        downstream_channel: str,
        entry_step: str,
        entry_component: str,
        container: str,
        pipeline_id: str,
    ) -> None:
        """Symmetric split on the reader side — see
        :meth:`_rewrite_writer_side`.
        """
        new_triples = self.output_reader.construct(
            f"""
            {entry_step} a tcs:InstancePipelineComponent ;
                prov:specializationOf {entry_component} ;
                p-plan:isStepOfPlan {pipeline_id} .
            {container} tcs:runs {entry_step} ;
                tcs:instantiates {entry_component} .
            {downstream_channel} a tcs:Connection , {self.default_channel_type} ;
                tcs:from {entry_step} ; tcs:to {reader} .
            {channel} tcs:to {entry_step} .
            """,
            "?s ?p ?o .",
        ).graph
        self.output_reader = self.output_reader.add(new_triples)
        remove_triples = self.output_reader.construct(
            f"{channel} tcs:to {reader} .", "?s ?p ?o ."
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
