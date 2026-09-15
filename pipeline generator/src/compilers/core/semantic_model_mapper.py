from collections import Counter

from rdflib import Graph

from rdfine import GraphReader, receive_first

from ..compiler_abc import Compiler


class SemanticModelMapper(Compiler):
    """Translate the authoring-layer ``tcs:Connection`` syntax into the
    internal ``tcs:readsFrom`` / ``tcs:writesTo`` / ``tcs:Channel``
    wiring every downstream compiler, SHACL shape and inference rule
    already understands.

    ``tcs:Connection`` is a second, terser way for an author to state a
    dataflow edge between two ``tcs:InstancePipelineComponent``\\ s::

        [ a tcs:Connection ; tcs:from demo:A ; tcs:to demo:B ] .

    instead of hand-naming a channel on both ends. This compiler is the
    *only* place that vocabulary is understood — nothing downstream of
    it (compilers, SHACL shapes, inference rules) is touched or should
    ever be, so authors may freely mix ``tcs:Connection`` with the
    existing ``tcs:readsFrom`` / ``tcs:writesTo`` / ``p-plan:isPrecededBy``
    forms.

    Branching (a step with more than one incoming or outgoing
    ``tcs:Connection``, or pre-existing ``tcs:writesTo`` /
    ``tcs:readsFrom`` wiring that already branches) is not supported by
    this vocabulary yet — it needs a channel-identity term of its own,
    deferred to its own design pass. An author who needs fan-out/fan-in
    keeps using ``tcs:readsFrom`` / ``tcs:writesTo`` directly, which
    stays fully supported.

    Resolution table (``W`` = the ``from`` step's existing
    ``tcs:writesTo`` objects, ``R`` = the ``to`` step's existing
    ``tcs:readsFrom`` objects, as seen at mapper time):

    - ``W`` and ``R`` both empty: mint a fresh ``tcs:Channel`` and wire
      both ends.
    - Exactly one side already wired: reuse that channel for the other
      side.
    - Both sides already wired to the *same* channel: no-op.
    - Both sides already wired to *different* channels: the Connection
      contradicts existing wiring — raises, since (unlike
      ``p-plan:isPrecededBy``, a supplementary ordering hint this
      compiler's sibling :class:`~compilers.core.pipeline_enricher.PipelineEnricher`
      may silently skip) a ``tcs:Connection`` is the author's sole
      statement of this dataflow edge, so silently dropping it would
      lose it.

    Only ``tcs:Connection`` nodes whose endpoints are steps of the
    pipeline currently being compiled are considered — a standalone
    Connection carries no ``p-plan:isStepOfPlan`` of its own, and
    ``DEFAULT_PIPELINE_FILES`` loads every shipped pipeline definition
    into one graph, so an unscoped mapper would wire other pipelines'
    edges into this build. A Connection belonging to another plan is
    not an error, just silently left alone for that plan's own build.
    """

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        # Same collision-safe minting idiom as PipelineEnricher /
        # PipelineSeeder — only incremented when a channel is actually
        # minted, checked against the graph so it never collides with a
        # name already in use.
        self._next_channel_index = 0

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Eligible as soon as the build exists and at least one
        ``tcs:Connection`` is present in the graph — i.e. in the first
        scan after ``PipelineSeeder``. Never fires on a Connection-free
        pipeline, so the compiler stays entirely invisible (no
        ``dct:creator`` entry) unless the author actually used the
        syntax.
        """
        return graph_reader.ask(
            """
            ?build a tcs:PipelineBuild .
            ?c a tcs:Connection .
            """
        )

    def compile(self) -> Graph:
        self.lookup_target_pipeline()
        self.list_connections()
        self.validate_connection_wellformedness()
        self.resolve_connection_channels()
        self.attach_channel_wiring()
        self.detach_connection_nodes()
        return self.output_reader.graph

    def lookup_target_pipeline(self) -> None:
        """Same pattern as ``GraphReducer.__init__``: cross-check the
        seeded build's ``prov:hadPlan`` against the original
        ``tcs:CompilationRequest``'s ``tcs:targetPipeline`` rather than
        trusting either alone.
        """
        build_and_pipeline = self.output_reader.select(
            "?build ?pipeline",
            """
            ?build a tcs:PipelineBuild ; prov:hadPlan ?pipeline .
            ?request a tcs:CompilationRequest ; tcs:targetPipeline ?pipeline .
            """,
        )
        self.pipeline_id: str = receive_first(build_and_pipeline["pipeline"])

    def list_connections(self) -> None:
        """Collect every ``tcs:Connection`` node together with its raw
        ``tcs:from`` / ``tcs:to`` objects (kept as lists, not yet
        cardinality-checked — that is
        :meth:`validate_connection_wellformedness`'s job), scoped to
        this pipeline: a Connection is considered ours if at least one
        of its raw endpoints is a step of :attr:`pipeline_id`. Anything
        else belongs to another plan entirely and is left untouched.
        """
        pipeline_step_ids = set(
            self.output_reader.filter(
                pred="p-plan:isStepOfPlan", obj=self.pipeline_id
            )
            .df["sub"]
            .to_list()
        )
        conn_ids = (
            self.output_reader.filter(pred="rdf:type", obj="tcs:Connection")
            .df["sub"]
            .to_list()
        )
        self.connections = []
        for conn_id in conn_ids:
            froms = self._lookup_single_endpoint(conn_id, "tcs:from")
            tos = self._lookup_single_endpoint(conn_id, "tcs:to")
            if not (set(froms) | set(tos)) & pipeline_step_ids:
                continue
            self.connections.append((conn_id, froms, tos))

    def validate_connection_wellformedness(self) -> None:
        """Raise on any structurally malformed Connection belonging to
        this pipeline: not exactly one ``tcs:from`` / ``tcs:to``, or an
        endpoint that is not a ``tcs:InstancePipelineComponent`` —
        including the easy mistake of pointing ``tcs:to`` at a channel.

        Normalizes :attr:`connections` in place from
        ``(conn_id, froms, tos)`` to ``(conn_id, from_step, to_step)``
        for the resolution methods that follow.
        """
        validated = []
        for conn_id, froms, tos in self.connections:
            if len(froms) != 1:
                raise ValueError(
                    f"{conn_id}: a tcs:Connection must have exactly one "
                    f"tcs:from, found {len(froms)}."
                )
            if len(tos) != 1:
                raise ValueError(
                    f"{conn_id}: a tcs:Connection must have exactly one "
                    f"tcs:to, found {len(tos)}."
                )
            from_step, to_step = froms[0], tos[0]
            if not self.output_reader.ask(
                f"{from_step} a tcs:InstancePipelineComponent ."
            ):
                raise ValueError(
                    f"{conn_id}: tcs:from must point at a "
                    f"tcs:InstancePipelineComponent, got {from_step}."
                )
            if not self.output_reader.ask(
                f"{to_step} a tcs:InstancePipelineComponent ."
            ):
                raise ValueError(
                    f"{conn_id}: tcs:to must point at a "
                    f"tcs:InstancePipelineComponent (a step), got {to_step} "
                    "— did you mean to point tcs:to at a channel? A "
                    "tcs:Connection wires two steps; the channel in "
                    "between is derived automatically."
                )
            validated.append((conn_id, from_step, to_step))
        self.connections = validated

    def resolve_connection_channels(self) -> None:
        """Apply the resolution table from the class docstring to every
        validated Connection, raising on the two branching cases: a
        step that is the ``from``/``to`` of more than one Connection,
        or a step whose pre-existing ``tcs:writesTo`` / ``tcs:readsFrom``
        wiring already branches.
        """
        from_counts = Counter(from_step for _, from_step, _ in self.connections)
        to_counts = Counter(to_step for _, _, to_step in self.connections)

        self.channel_assignments: dict[str, str] = {}
        for conn_id, from_step, to_step in self.connections:
            if from_counts[from_step] > 1:
                raise ValueError(
                    f"{from_step} is the tcs:from of more than one "
                    "tcs:Connection — fan-out is not supported yet; use "
                    "tcs:writesTo directly until branching lands."
                )
            if to_counts[to_step] > 1:
                raise ValueError(
                    f"{to_step} is the tcs:to of more than one "
                    "tcs:Connection — fan-in is not supported yet; use "
                    "tcs:readsFrom directly until branching lands."
                )

            writes = self._lookup_wired_channels(from_step, "tcs:writesTo")
            reads = self._lookup_wired_channels(to_step, "tcs:readsFrom")

            if len(writes) > 1 or len(reads) > 1:
                raise ValueError(
                    f"{from_step} -> {to_step}: existing tcs:writesTo / "
                    "tcs:readsFrom wiring already branches — not "
                    "supported yet; use tcs:writesTo / tcs:readsFrom "
                    "directly until branching lands."
                )

            if writes and reads:
                if set(writes) != set(reads):
                    raise ValueError(
                        f"tcs:Connection from {from_step} to {to_step} "
                        f"contradicts existing wiring: {from_step} already "
                        f"tcs:writesTo {writes[0]} but {to_step} already "
                        f"tcs:readsFrom {reads[0]}."
                    )
                channel = writes[0]
            elif writes:
                channel = writes[0]
            elif reads:
                channel = reads[0]
            else:
                channel = self._mint_channel_id()

            self.channel_assignments[conn_id] = channel

    def attach_channel_wiring(self) -> None:
        """Emit the internal wiring for every resolved Connection.
        Adding a triple that already holds (rows where a side was
        already wired) is a no-op — RDF triples are set members — so
        every resolved Connection is written the same way regardless of
        which row of the table it took.
        """
        for conn_id, from_step, to_step in self.connections:
            channel = self.channel_assignments[conn_id]
            self._add(f"{from_step} tcs:writesTo {channel} .")
            self._add(f"{to_step} tcs:readsFrom {channel} .")
            self._add(f"{channel} a tcs:Channel .")

    def detach_connection_nodes(self) -> None:
        """Remove every consumed Connection's triples (``tcs:Connection``
        typing, ``tcs:from``, ``tcs:to``) so no authoring vocabulary
        leaks past this compiler, and ``GraphReducer`` — which narrows
        forward from the build and cannot reach a standalone Connection
        node — never has to know it existed.
        """
        for conn_id, _, _ in self.connections:
            self.output_reader = self.output_reader.remove(
                self.output_reader.filter(sub=conn_id).graph
            )

    def _lookup_single_endpoint(self, conn_id: str, pred: str) -> list[str]:
        return self.output_reader.filter(sub=conn_id, pred=pred).df["obj"].to_list()

    def _lookup_wired_channels(self, step: str, pred: str) -> list[str]:
        return self.output_reader.filter(sub=step, pred=pred).df["obj"].to_list()

    def _mint_channel_id(self) -> str:
        channel_id = f":channel_{self._next_channel_index}"
        self._next_channel_index += 1
        while self.output_reader.check_exists(channel_id):
            channel_id = f":channel_{self._next_channel_index}"
            self._next_channel_index += 1
        return channel_id

    def _add(self, triple: str) -> None:
        new_triples = self.output_reader.construct(triple, "?s ?p ?o .").graph
        self.output_reader = self.output_reader.add(new_triples)
