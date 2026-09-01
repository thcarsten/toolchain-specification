from rdflib import Graph

from rdfine import GraphReader, receive_first

from ..base import Compiler


class PipelineSeeder(Compiler):
    """Seed the ``tcs:PipelineBuild`` node from a compilation request.

    Reads the target pipeline id off a ``tcs:CompilationRequest``
    posted to the graph by :class:`CompilationRunner`, then seeds the
    ``tcs:PipelineBuild`` node so every downstream compiler (and the
    runner itself, for the ``dct:creator`` provenance triples) has a
    target to attach to. Also renames typed blank-node subjects to
    stable IRIs so later compilers can reference the resulting
    resources by name inside SPARQL query strings.

    Does not narrow the graph — the full catalog stays visible for the
    fixpoint loop so registry compilers such as
    :class:`BridgeTransportCompiler` can reach catalog components no
    step of this pipeline specializes yet. Narrowing lives in
    :class:`GraphReducer`.
    """

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Applies once a ``tcs:CompilationRequest`` has been posted."""
        return not graph_reader.filter(
            pred="rdf:type", obj="tcs:CompilationRequest"
        ).df.empty

    def compile(self) -> Graph:
        self.lookup_target_pipeline()
        self.seed_pipeline_build()
        self.name_blind_nodes()
        return self.output_reader.graph

    def lookup_target_pipeline(self) -> None:
        """Read the target pipeline id off the ``tcs:CompilationRequest``.

        Stored on :attr:`pipeline_id` so it stays inspectable after
        ``compile()`` — same convention as any other intermediate.
        """
        self.pipeline_id = receive_first(
            self.output_reader.select(
                "?pipeline",
                "?req a tcs:CompilationRequest ; tcs:targetPipeline ?pipeline .",
            )["pipeline"]
        )

    def seed_pipeline_build(self) -> None:
        """
        Adds:
            - ``<pipeline_id>_build a tcs:PipelineBuild``
            - ``<pipeline_id>_build prov:hadPlan <pipeline_id>``
        """
        if not self.output_reader.ask(f"{self.pipeline_id} a tcs:PipelineDefinition ."):
            raise NameError(
                f"{self.pipeline_id} not found in graph as a tcs:PipelineDefinition."
            )
        new_triples = self.output_reader.construct(
            f"""
            {self.pipeline_id}_build a tcs:PipelineBuild ;
                                      prov:hadPlan {self.pipeline_id} .
            """,
            f"{self.pipeline_id} a tcs:PipelineDefinition",
        ).graph
        self.output_reader = self.output_reader.add(new_triples)

    def name_blind_nodes(self) -> None:
        """
        Rename blank nodes typed with any of the prefixes we reason on
        further downstream — ``tcs:`` (configs, files, containers, ...),
        ``spdx:`` (package dependencies attached to components via
        ``dct:requires``), and ``sh:`` (anonymous ``sh:NodeShape``s —
        e.g. the trivial passthrough/input/output shapes attached to
        catalog components — so they become stable named IRIs downstream
        compilers can reference in a SPARQL query string instead of
        blank nodes, whose labels aren't safely reusable across separate
        query executions). Anything else stays a blank node.
        """

        rename_ids = (
            self.output_reader.filter(
                sub="^_:",
                pred="^rdf:type$",
                obj=["^tcs:", "^spdx:", "^sh:"],
                regex=True,
            )
            .df["sub"]
            .to_list()
        )
        rename_ids = list(set(rename_ids))

        for i, rename_id in enumerate(rename_ids):
            type_list = (
                self.output_reader.filter(sub=rename_id, pred="rdf:type")
                .df["obj"]
                .to_list()
            )
            type_list.sort()
            first_type = receive_first(type_list)
            prefix = (
                ":" + self.output_reader.prefix_store.drop_string(first_type).lower()
            )
            new_name = f"{prefix}_{i}"
            # Guard against colliding with a name already in use (e.g. an
            # author-declared resource in the pipeline definition) — bump
            # the suffix until the name is free rather than silently
            # merging two distinct resources under one IRI.
            while self.output_reader.check_exists(new_name):
                i += 1
                new_name = f"{prefix}_{i}"
            self.output_reader = self.output_reader.rename(rename_id, new_name)
