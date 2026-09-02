from rdflib import BNode, Graph, URIRef

from rdfine import GraphReader

from ..compiler_abc import Compiler


class PipelineEnricher(Compiler):
    """
    Framework-agnostic graph enrichment, run between ``PipelineSeeder``
    and ``PipelineAssembler``. Bundles every generic normalization step so
    downstream framework compilers (RDF-Connect, LDIO, semantic.works,
    ...) can assume a fully-wired, fully-configured step graph without
    minting anything themselves — that responsibility belongs here, not
    scattered across whichever framework compiler happens to need it
    first.

    Responsibilities:

    - **Channel synthesis** (:meth:`synthesize_channels`) — expands
      ``p-plan:isPrecededBy`` edges between two
      ``tcs:InstancePipelineComponent``\\ s into concrete ``tcs:Channel``
      wiring (``tcs:readsFrom`` / ``tcs:writesTo``), so authors can use
      the terse ``p-plan:isPrecededBy`` form for the common
      strictly-serial 1:1 case instead of hand-naming a channel on both
      ends.

    - **Config seeding** (:meth:`ensure_step_configs`) — every
      ``tcs:InstancePipelineComponent`` gets exactly one
      ``tcs:PipelineConfig`` to inject into later. Authors never need to
      pre-declare an empty config just so a later compiler has somewhere
      to write a key.

    Explicit wiring always wins over minting a fresh channel, but an
    edge with exactly one already-explicit side (predecessor has a
    single ``tcs:writesTo``, or successor has a single
    ``tcs:readsFrom``) still gets its *other* side reused-and-wired —
    there is no ambiguity in that case, only a missing triple. Genuine
    ambiguity (either side already has more than one channel — e.g.
    ``rdfc:Sdsify`` writing to two channels) is left completely
    untouched: ``p-plan:isPrecededBy`` alone cannot say which of a
    producer's outputs a given consumer reads, so this compiler never
    guesses and the edge must stay fully explicit.

    ``tcs:Channel`` typing on a minted channel comes for free from the
    existing ``inference_rules.yaml`` rule keyed on ``tcs:readsFrom`` /
    ``tcs:writesTo`` — this compiler does not need to add it itself.
    """

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        # Only incremented when a new resource is actually minted, and
        # checked against the graph so it never collides with a name
        # already in use — same idiom as PipelineSeeder.name_blind_nodes
        # / PipelineAssembler.describe_docker_container.
        self._next_channel_index = 0
        self._next_config_index = 0

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Triggered once ``PipelineSeeder`` has seeded the
        ``tcs:PipelineBuild`` node — i.e. as soon as its bootstrap has
        run, regardless of whether the pipeline happens to have any
        steps yet. Triggering on the presence of an
        ``tcs:InstancePipelineComponent`` instead would coincidentally
        work for a non-empty pipeline, but is the wrong signal: this
        compiler's job is "run right after extraction", not "run once
        there happen to be steps".
        """
        return not graph_reader.filter(
            pred="rdf:type", obj="tcs:PipelineBuild"
        ).df.empty

    def compile(self) -> Graph:
        self.synthesize_channels()
        self.ensure_step_configs()
        return self.output_reader.graph

    def synthesize_channels(self) -> None:
        edges = self.output_reader.select(
            "?successor ?predecessor",
            "?successor p-plan:isPrecededBy ?predecessor .",
        )

        for _, row in edges.iterrows():
            successor = row["successor"]
            predecessor = row["predecessor"]

            reads = (
                self.output_reader.filter(sub=successor, pred="tcs:readsFrom")
                .df["obj"]
                .to_list()
            )
            writes = (
                self.output_reader.filter(sub=predecessor, pred="tcs:writesTo")
                .df["obj"]
                .to_list()
            )

            if len(reads) == 0 and len(writes) == 0:
                # Neither side wired — mint a fresh channel for both.
                channel_id = self._mint_channel_id()
                self._add(f"{successor} tcs:readsFrom {channel_id} .")
                self._add(f"{predecessor} tcs:writesTo {channel_id} .")
            elif len(reads) == 0 and len(writes) == 1:
                # Predecessor already unambiguously wired — reuse it.
                self._add(f"{successor} tcs:readsFrom {writes[0]} .")
            elif len(writes) == 0 and len(reads) == 1:
                # Successor already unambiguously wired — reuse it.
                self._add(f"{predecessor} tcs:writesTo {reads[0]} .")
            # Any other combination (either side already has >1 channel,
            # or both sides are already wired) is left untouched —
            # either it's a genuine branch that must stay explicit, or
            # it's already fully resolved.

    def ensure_step_configs(self) -> None:
        """Give every step exactly one ``tcs:PipelineConfig`` to inject into.

        Steps that already declare a ``p-plan:hasInputVar`` (whether
        exactly one, or more than one — a modelling error the generic
        ``InstancePipelineComponentShape`` cardinality check already
        flags) are left untouched; this method only fills the gap for
        steps that have none, and never guesses which existing config a
        later compiler should use.
        """
        steps = (
            self.output_reader.filter(
                pred="rdf:type", obj="tcs:InstancePipelineComponent"
            )
            .df["sub"]
            .to_list()
        )
        for step_id in steps:
            existing = (
                self.output_reader.filter(sub=step_id, pred="p-plan:hasInputVar")
                .df["obj"]
                .to_list()
            )
            if existing:
                continue
            self._mint_config(step_id)

    def _mint_channel_id(self) -> str:
        channel_id = f":channel_{self._next_channel_index}"
        self._next_channel_index += 1
        while self.output_reader.check_exists(channel_id):
            channel_id = f":channel_{self._next_channel_index}"
            self._next_channel_index += 1
        return channel_id

    def _mint_config(self, step_id: str) -> None:
        """Mint an empty ``tcs:PipelineConfig`` for ``step_id``.

        Built from raw rdflib triples rather than a SPARQL ``CONSTRUCT``:
        the fresh ``tcs:embedded`` blank node must never be minted via a
        ``CONSTRUCT`` template paired with a broad ``"?s ?p ?o ."`` WHERE
        clause — that matches every triple in the graph and mints a
        *distinct* blank node per solution row, silently corrupting the
        graph.
        """
        prefix_store = self.output_reader.prefix_store
        config_id = f":pipelineconfig_{self._next_config_index}"
        self._next_config_index += 1
        while self.output_reader.check_exists(config_id):
            config_id = f":pipelineconfig_{self._next_config_index}"
            self._next_config_index += 1

        step_uri = URIRef(prefix_store.expand_string(step_id))
        config_uri = URIRef(prefix_store.expand_string(config_id))
        new_triples = Graph()
        # GraphReader.add() derives the merged reader's prefix_store from
        # each graph's own rdflib namespace bindings; a bare Graph() has
        # none, which would otherwise leave the merged store missing
        # every prefix beyond rdflib's built-in defaults. Bind ours first
        # so the merge is additive, not a silent loss of prefixes.
        prefix_store.bind_to_namespace(new_triples)
        new_triples.add(
            (
                step_uri,
                URIRef(prefix_store.expand_string("p-plan:hasInputVar")),
                config_uri,
            )
        )
        new_triples.add(
            (
                config_uri,
                URIRef(prefix_store.expand_string("rdf:type")),
                URIRef(prefix_store.expand_string("tcs:PipelineConfig")),
            )
        )
        new_triples.add(
            (
                config_uri,
                URIRef(prefix_store.expand_string("tcs:embedded")),
                BNode(),
            )
        )
        self.output_reader = self.output_reader.add(new_triples)

    def _add(self, triple: str) -> None:
        new_triples = self.output_reader.construct(triple, "?s ?p ?o .").graph
        self.output_reader = self.output_reader.add(new_triples)
