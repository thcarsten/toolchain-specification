import pandas as pd
from rdflib import Graph
from graph_reader import GraphReader


class Pipeline:
    """
    Class to construct a Pipeline Data Object.

    Properties:
            - See init
            - TODO: prefixes (so that is easy to interpret fetched data in an idiomatic way)
    Methods:
            - load_pipeline(pipeline_id, pipeline_graph_reader)
    """

    def __init__(self):
        self.pipeline_id: str = ""
        self.name: str = ""
        self.description: str = ""
        self.steps: pd.DataFrame | None = None
        self.processors: dict | None = None
        self.configs: dict | None = None
        self.runners: dict | None = None
        self.graph: Graph | None = None
        self.prefixes: dict | None = None

    def load_pipeline(self, pipeline_id: str, pipeline_graph_reader: GraphReader):
        self.pipeline_id = pipeline_id

        dict_pipeline = pipeline_graph_reader.extract_subgraph(
            pipeline_id, direction="along", as_dict=True
        )

        self.name = dict_pipeline.get("rdfs:label")
        self.description = dict_pipeline.get("rdfs:comment")
        self.steps = self._fetch_steps(pipeline_id, pipeline_graph_reader)
        self.processors = self._fetch_processors(
            pipeline_id=pipeline_id, pipeline_graph_reader=pipeline_graph_reader
        )
        self.configs = self._fetch_configs(
            pipeline_id=pipeline_id, pipeline_graph_reader=pipeline_graph_reader
        )
        self.runners = self._fetch_runners(
            processors=self.processors, pipeline_graph_reader=pipeline_graph_reader
        )
        self.constraints = self._fetch_constraints(
            pipeline_graph_reader=pipeline_graph_reader
        )

        # I allow inverse traversal for isStepOfPlan to allow traversal from the PipelineDefinition to the PipelineSteps
        self.graph = pipeline_graph_reader.extract_subgraph(
            pipeline_id, direction="along", against=["p-plan:isStepOfPlan"]
        )
        # I also provide the prefixes because urls should be expandable based on information contained in the pipeline class alone
        self.prefixes = pipeline_graph_reader.prefixes

    def _fetch_steps(
        self,
        pipeline_id: str | None = None,
        pipeline_graph_reader: GraphReader | None = None,
    ):
        """
        TODO: Add function description
        TODO: Will later have to include the "unpacking" of nested pipelines
        """

        # Provide default values if no argument is provided
        pipeline_id = pipeline_id or self.pipeline_id

        query_to_fetch_steps = f"""        
                SELECT ?step ?prev_step ?processor ?config
                WHERE {{
                    ?step p-plan:isStepOfPlan {pipeline_id} .
                    OPTIONAL {{?step p-plan:isPrecededBy ?prev_step .}} 
                    OPTIONAL {{?step tc:toBeCarriedOutByProcessor ?processor .}}
                    OPTIONAL {{?step p-plan:hasInputVar ?config .}}
                }}
        """
        df_pipeline = pipeline_graph_reader.execute_query(query_to_fetch_steps)

        return df_pipeline

    def _fetch_processors(
        self,
        df_steps: pd.DataFrame | None = None,
        pipeline_id: str | None = None,
        pipeline_graph_reader: GraphReader | None = None,
    ):
        """
        Grabs processors used in the pipeline as Python dicts
        """

        # Provide default values if no argument is provided
        df_steps = df_steps or self.steps
        list_processors = [
            pipeline_graph_reader.extract_subgraph(
                df_steps["processor"][row_index],
                exclude="osw:hasUseLimitations",
                prune="osw:hasDependency",
                as_dict=True,
            )
            for row_index in range(len(df_steps))
        ]

        return self._jsonld_list_to_dict(
            list_processors, drop=["@id", "@context", "@type"]
        )

    def _fetch_configs(
        self,
        df_steps: pd.DataFrame | None = None,
        pipeline_id: str | None = None,
        pipeline_graph_reader: GraphReader | None = None,
    ):
        """
        Grabs configs used in the pipeline as Python dicts
        """
        # Provide default values if no argument is provided
        df_steps = df_steps or self.steps
        list_configs = [
            pipeline_graph_reader.extract_subgraph(
                df_steps["config"][row_index], as_dict=True
            )
            for row_index in range(len(df_steps))
        ]

        return self._jsonld_list_to_dict(
            list_configs, drop=["@id", "@context", "@type"]
        )

    def _fetch_runners(self, processors: dict, pipeline_graph_reader: GraphReader):
        """
        Grabs runners required in the pipeline as Python dicts
        """
        # Getting a list of each runner a processor depends on
        list_runners = [
            processors[processor].get("osw:hasDependency").get("@id")
            for processor in processors
        ]
        # Only keep the unique list of runners
        list_runners = list(set(list_runners))
        # Fetch the unique info for each runner
        list_runners = [
            pipeline_graph_reader.extract_subgraph(
                runner, exclude="osw:hasUseLimitations", as_dict=True
            )
            for runner in list_runners
        ]

        return self._jsonld_list_to_dict(
            list_runners, drop=["@id", "@context", "@type"]
        )

    def _fetch_constraints(
        self,
        pipeline_graph_reader: GraphReader | None = None,
    ):
        """
        TODO: Currently, this only contains references to constraints of processors, not runners
        Grabs the reference id's of all nodeshapes of all pipeline components used in a pipeline
        """

        df_steps = self.steps
        list_constraints = [
            pipeline_graph_reader.extract_subgraph(
                df_steps["processor"][row_index],
                exclude="osw:hasDependency",
                prune="osw:hasUseLimitations",
                as_dict=True,
            )
            for row_index in range(len(df_steps))
        ]

        dict_constraints = self._jsonld_list_to_dict(
            list_constraints, drop=["@id", "@context", "@type"]
        )

        output_dict = {}
        for key in dict_constraints:
            references = dict_constraints.get(key).get("osw:hasUseLimitations")
            if isinstance(references, dict):
                output_dict[key] = [references.get("@id")]
            elif isinstance(references, list):
                output_dict[key] = [ref.get("@id") for ref in references]

        return output_dict

    def _jsonld_list_to_dict(
        self, list_jsonld: list, drop: list[str] | None = None
    ) -> dict:
        """
        Helper function that restructures lists of frame-compacted json-lds
        to dicts where the key is the id of the entity and the value the rest
        drop: list of keys to drop
        """
        # Provide default values if no argument is provided
        drop = drop or []

        dict_jsonld = {}
        if len(list_jsonld) > 0:
            # Adding each list element to dict
            for element in list_jsonld:
                element_id = element.get("@id")
                # Removing dropped entries
                for drop_key in drop:
                    del element[drop_key]

                dict_jsonld[element_id] = element

        return dict_jsonld
