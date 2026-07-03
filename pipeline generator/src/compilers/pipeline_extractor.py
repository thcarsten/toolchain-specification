from rdflib import Graph

from rdfine import GraphReader, receive_first

from .base import Compiler


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
        # Extracting the pipeline
        graph_inverse_steps = (
            self.output_reader.filter(pred="p-plan:isStepOfPlan", obj=self.pipeline_id)
            .construct("?o :hasStep ?s", "?s ?p ?o.")
            .graph
        )
        # Filtering down to triples concerning the pipeline
        self.output_reader = self.output_reader.add(graph_inverse_steps).traverse(
            self.pipeline_id
        )

    def name_blind_nodes(self) -> None:
        """
        Some blind nodes need to receive a proper id, this is happening here.
        """

        # renaming
        rename_ids = (
            self.output_reader.filter(
                sub="^_:", pred="^rdf:type$", obj="^tcs:", regex=True
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
            new_name = (
                ":"
                + self.output_reader.prefix_store.drop_string(first_type).lower()
                + f"_{i}"
            )
            self.output_reader = self.output_reader.rename(rename_id, new_name)
