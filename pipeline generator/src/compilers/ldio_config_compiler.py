from rdfine import GraphDict, GraphReader, drop_empty
from copy import deepcopy
import pandas as pd
import yaml


class LdioConfigCompiler:
    """
    Class to generate config file for LDIO.
    """

    def __init__(self, assembled_pipeline: GraphDict) -> None:
        self.graph_dict = deepcopy(
            assembled_pipeline
        )  # Making sure this is a deep copy
        self.graph_reader = GraphReader(assembled_pipeline.to_graph())
        self.output = {
            "name": "",
            "description": "",
            "input": {"adapter": {}},
            "transformers": [],
            "outputs": [],
        }
        self.df_steps: pd.DataFrame | None = None
        self.config_components: dict = {}
        self.config_orchestrator: dict = {}

    def compile(self) -> str:
        # Creating a dataframe for simple retrieval of entry
        self.df_steps = self.fetch_steps()
        # Fetching config data based on whats attached to components and whats attached to the orchestrator
        self.config_components = self.create_config_components()
        self.config_orchestrator = self.create_config_orchestrator()
        # Combining the two sources
        self.output.update(drop_empty(self.config_orchestrator))
        self.output.update(drop_empty(self.config_components))
        self.output = drop_empty(self.output)

        return yaml.dump(self.output, sort_keys=False)

    def create_config_orchestrator(self):
        """
        Creates a config based on the orchestrator and its associated configs (assigned, required and default)
        """

        config_query = f"""
                                        SELECT DISTINCT ?config_id ?relation
                                        WHERE {{
                                        ?config_id :configForComponent ldio:LinkedDataInteractionsOrchestrator.
                                        ?config_id :config_relation ?relation .
                                        ?config_id a tc:PipelineConfig .
                                        }}
                                    """

        df_config = self.graph_reader.execute_query(config_query)

        config_list = df_config["config_id"].to_list()
        output_dict = deepcopy(self.output)

        for config_id in config_list:
            config_dict = self.graph_dict.get_branch(config_id)[":config"]
            config_dict = self.graph_reader.prefix_store.drop(config_dict)
            output_dict.update(config_dict)

        return output_dict

    def create_config_components(self) -> dict:
        """
        Creates a config based on the components and their configs assigned to the LDIO microservice
        """

        dict_config = deepcopy(self.output)

        for index, row in self.df_steps.iterrows():
            dict_processor = {"name": row["name"], "config": row["config"]}
            processor_type = row["type"]
            if processor_type == "Input":
                dict_config["input"].update(dict_processor)
            elif processor_type == "Adapter":
                dict_config["input"]["adapter"].update(dict_processor)
            elif processor_type == "Transformer":
                dict_config["transformers"].append(dict_processor)
            elif processor_type == "Output":
                dict_config["outputs"].append(dict_processor)

        return dict_config

    def fetch_steps(self) -> pd.DataFrame:
        """
        Creating a df that contains all info needed to fill in the LDIO config
        """
        list_components = self.graph_reader.get_triples(
            sub="ldio:LinkedDataInteractionsOrchestrator", pred=":instantiates"
        )["obj"].to_list()

        list_records = []
        for component_id in list_components:
            ldio_label = self.graph_reader.get_triples(
                sub=component_id, pred="rdfs:label"
            )["obj"].to_list()[0]
            ldio_type = self.graph_reader.get_triples(
                sub=component_id, pred="ldio:type"
            )["obj"].to_list()[0]
            config_id = self.graph_reader.get_triples(
                sub=component_id, pred=":isAssigned"
            )["obj"].to_list()

            config = {}
            if len(config_id) > 0:
                config_id = config_id[0]
                path_to_config = self.graph_dict.find(
                    ":hasConfig.[0-9]+.@id$", config_id
                )["path"].to_list()[0]
                path_to_config = path_to_config.removesuffix("@id") + ":config"
                config = self.graph_dict.get(path_to_config)
                config = self.graph_reader.prefix_store.drop(config)

            dict_component = {
                "component": component_id,
                "type": ldio_type,
                "name": ldio_label,
                "config": config,
            }

            list_records += [dict_component]

        return pd.DataFrame.from_records(list_records)
