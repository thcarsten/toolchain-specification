from rdflib import Graph

from rdfine import GraphReader, receive_first

from ..base import Compiler


class PipelineExtractor(Compiler):
    """
    Compiler Class to extract all relevant data for one single pipeline.
    It does NOT yet perform any form of reasoning,
    its sole responsibility is grabbing the data and returning it in line with the internal model.
    It also seeds the ``tcs:PipelineBuild`` node so that every subsequent
    compiler can attach provenance and file nodes to a build that already exists.
    """

    def __init__(self, pipeline_id: str, graph: Graph) -> None:
        super().__init__(graph)
        self.pipeline_id = pipeline_id

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Always applicable — the extractor is the entry point of every run."""
        return True

    def compile(self) -> Graph:
        self.extract_pipeline()
        self.name_blind_nodes()
        self.seed_pipeline_build()
        return self.output_reader.graph

    def seed_pipeline_build(self) -> None:
        """
        Adds:
            - ``<pipeline_id>_build a tcs:PipelineBuild``
            - ``<pipeline_id>_build prov:hadPlan <pipeline_id>``

        The empty ``tcs:PipelineBuild`` skeleton exists from this point on, so
        every downstream compiler (and ``PipelineGenerator`` itself, for the
        ``dct:creator`` provenance triples) can locate it in the graph.
        """
        new_triples = self.output_reader.construct(
            f"""
            {self.pipeline_id}_build a tcs:PipelineBuild ;
                                      prov:hadPlan {self.pipeline_id} .
            """,
            f"{self.pipeline_id} a tcs:PipelineDefinition",
        ).graph
        self.output_reader = self.output_reader.add(new_triples)

    def extract_pipeline(self) -> None:
        # SHACL shapes float independently of the pipeline (reached by no
        # graph edge from it), so the traversal below would otherwise drop
        # them; collect each shape's own subgraph up front and re-add it
        # after extraction.
        shape_ids = (
            self.output_reader.filter(pred="rdf:type", obj="sh:NodeShape")
            .df["sub"]
            .to_list()
        )
        # ``bind_namespaces="none"`` avoids rdflib's default core bindings
        # (e.g. ``dcterms``), which collide with the catalog's own ``dct``
        # prefix for the same URI and can silently evict it once merged.
        shape_graph = Graph(bind_namespaces="none")
        for shape_id in shape_ids:
            shape_graph += self.output_reader.traverse(shape_id).graph

        # ``p-plan:isStepOfPlan`` points from step to plan; traversing it
        # in the "against" direction reaches every step (and everything
        # below it) without needing to materialize an inverse ``:hasStep``
        # triple first — this also scopes the extraction to just the one
        # pipeline's triples.
        self.output_reader = self.output_reader.traverse(
            self.pipeline_id, against="p-plan:isStepOfPlan"
        )
        self.output_reader = self.output_reader.add(shape_graph)

    def name_blind_nodes(self) -> None:
        """
        Some blind nodes need to receive a proper id, this is happening here.

        Renames blank nodes typed with any of the prefixes we know how
        to reason on further downstream: ``tcs:`` (configs, files,
        containers, ...) and ``spdx:`` (package dependencies attached
        to components via ``dct:requires``). Anything else stays a
        blank node.
        """

        # renaming
        rename_ids = (
            self.output_reader.filter(
                sub="^_:", pred="^rdf:type$", obj=["^tcs:", "^spdx:"], regex=True
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
