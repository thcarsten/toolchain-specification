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

    Responsibility:

    - **Config seeding** (:meth:`ensure_step_configs`) — every
      ``tcs:InstancePipelineComponent`` gets exactly one
      ``tcs:PipelineConfig`` to inject into later. Authors never need to
      pre-declare an empty config just so a later compiler has somewhere
      to write a key.

    Channel synthesis from a terse edge notation is
    ``SemanticModelMapper``'s job now (``tcs:Connection`` /
    ``tcs:from`` / ``tcs:to``) — this compiler no longer expands
    ``p-plan:isPrecededBy``, which is not an authoring form any more.
    """

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        # Only incremented when a new resource is actually minted, and
        # checked against the graph so it never collides with a name
        # already in use — same idiom as PipelineSeeder.name_blind_nodes
        # / PipelineAssembler.describe_docker_container.
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
        self.ensure_step_configs()
        return self.output_reader.graph

    def ensure_step_configs(self) -> None:
        """Give every step exactly one ``tcs:PipelineConfig`` to inject into.

        Steps that already declare a ``p-plan:hasInputVar`` (whether
        exactly one, or more than one — a modelling error the generic
        ``InstancePipelineComponentShape`` cardinality check already
        flags) are left untouched; this method only fills the gap for
        steps that have none, and never guesses which existing config a
        later compiler should use.
        """
        # Sorted so the iteration order — which decides which step gets
        # `:pipelineconfig_0` — is stable across runs.
        steps = sorted(
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
