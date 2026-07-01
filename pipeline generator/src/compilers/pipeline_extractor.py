from rdflib import Graph

from .utils import receive_first
from .base import Compiler, Tier


class PipelineExtractor(Compiler):
    """
    Compiler Class to extract all relevant data for one single pipeline.
    It does NOT yet perform any form of reasoning,
    its sole responsibility is grabbing the data and returning it in line with the internal model.
    """

    tier = Tier.SEED

    def __init__(self, pipeline_id: str, graph: Graph) -> None:
        super().__init__(graph)
        self.pipeline_id = pipeline_id

    def _compile(self) -> Graph:
        self.extract_pipeline()
        self.name_blind_nodes()
        return self.graph_reader.graph

    def extract_pipeline(self) -> None:
        # Extracting the pipeline
        graph_inverse_steps = (
            self.graph_reader.filter(pred="p-plan:isStepOfPlan", obj=self.pipeline_id)
            .construct("?o :hasStep ?s", "?s ?p ?o.")
            .graph
        )
        # Filtering down to triples concerning the pipeline
        self.graph_reader = self.graph_reader.add(graph_inverse_steps).traverse(
            self.pipeline_id
        )

    def name_blind_nodes(self) -> None:
        """
        Some blind nodes need to receive a proper id, this is happening here.
        """

        # renaming
        rename_ids = (
            self.graph_reader.filter(
                sub="^_:", pred="^rdf:type$", obj="^tcs:", regex=True
            )
            .df["sub"]
            .to_list()
        )
        rename_ids = list(set(rename_ids))

        for i, rename_id in enumerate(rename_ids):
            type_list = (
                self.graph_reader.filter(sub=rename_id, pred="rdf:type")
                .df["obj"]
                .to_list()
            )
            type_list.sort()
            first_type = receive_first(type_list)
            new_name = (
                ":"
                + self.graph_reader.prefix_store.drop_string(first_type).lower()
                + f"_{i}"
            )
            self.graph_reader = self.graph_reader.rename(rename_id, new_name)
