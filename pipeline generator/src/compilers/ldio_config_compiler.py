from rdflib import Graph, URIRef
from rdfine import GraphReader, GraphDict, PrefixStore, parse_config, drop_empty
import pandas as pd
from copy import deepcopy
import yaml


class LdioConfigCompiler:
    """
    Class to generate config file for LDIO.
    """

    def __init__(self, build_graph: Graph) -> None:
        self.graph_reader = GraphReader(build_graph)
        self.output = {
            "name": "",
            "description": "",
            "input": {"adapter": {}},
            "transformers": [],
            "outputs": [],
        }
        self.df_steps: pd.DataFrame | None = None

    def compile(self) -> str:
        # Creating a dataframe for simple retrieval of entry
        self.df_steps = self.fetch_steps()
        self.dict_configs = self.fetch_configs()
        self.fill_in_components()
        self.output = drop_empty(self.output)
        return yaml.dump(self.output, sort_keys=False)

    def fetch_steps(self) -> pd.DataFrame:

        # list all LDIO components
        where_statement = f"""
        ?container a tcs:DockerContainer .
        ?container tcs:instantiates ldio:LinkedDataInteractionsOrchestrator .
        ?container tcs:instantiates ?pipeline_component .
        ?container tcs:runs ?pipeline_step .
        ?pipeline_step prov:specializationOf ?pipeline_component .
        """

        list_components = self.graph_reader.query(
            select="?pipeline_component", where=where_statement
        )["pipeline_component"].to_list()

        list_records = []
        for component_id in list_components:
            ldio_label = (
                self.graph_reader.filter(sub=component_id, pred="rdfs:label")
                .df["obj"]
                .to_list()[0]
            )
            ldio_type = (
                self.graph_reader.filter(sub=component_id, pred="ldio:type")
                .df["obj"]
                .to_list()[0]
            )

            dict_component = {
                "component": component_id,
                "type": ldio_type,
                "name": ldio_label,
            }

            # If the component has a config assigned, fetch that too
            if self.graph_reader.query(
                ask=True, where=f"{component_id} :isAssigned ?config ."
            ):
                config_id = (
                    self.graph_reader.filter(sub=component_id, pred=":isAssigned")
                    .df["obj"]
                    .to_list()[0]
                )
                dict_component.update({"config": config_id})

            list_records += [dict_component]

        return pd.DataFrame.from_records(list_records)

    def fetch_configs(self) -> dict:

        config_list = self.df_steps["config"].to_list()
        config_list = [config for config in config_list if isinstance(config, str)]

        graph_dict = GraphDict.from_graph(self.graph_reader.graph)

        output_dict = {}
        for config_id in config_list:
            config_dict = graph_dict.frame({"@id": config_id}).dict
            config_dict = parse_config(config_dict)[":config"]
            config_dict = self.graph_reader.prefix_store.drop(config_dict)
            output_dict.update({config_id: config_dict})
        return output_dict

    def fill_in_components(self) -> None:
        """
        Fills in the components into the output dictionary
        """

        for index, row in self.df_steps.iterrows():
            config_id = row["config"]
            dict_processor = {
                "name": row["name"],
                "config": self.dict_configs.get(config_id),
            }
            processor_type = row["type"]
            if processor_type == "Input":
                self.output["input"].update(dict_processor)
            elif processor_type == "Adapter":
                self.output["input"]["adapter"].update(dict_processor)
            elif processor_type == "Transformer":
                self.output["transformers"].append(dict_processor)
            elif processor_type == "Output":
                self.output["outputs"].append(dict_processor)
