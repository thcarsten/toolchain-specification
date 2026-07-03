from rdflib import Graph
from rdfine import GraphReader, drop_empty, receive_first
import pandas as pd
import yaml

from .base import Compiler
from .utils import extract_config


class LdioConfigCompiler(Compiler):
    """
    Class to generate config file for LDIO.
    """

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        # Intermediate state — populated in ``compile``.
        self.df_steps: pd.DataFrame = pd.DataFrame()
        self.dict_configs: dict = {}
        self.output: dict = {}

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Triggered when a container instantiates the LDIO orchestrator."""
        return not graph_reader.filter(
            pred="tcs:instantiates",
            obj="ldio:LinkedDataInteractionsOrchestrator",
        ).df.empty

    def compile(self) -> Graph:
        self.output = {
            "name": "",
            "description": "",
            "input": {"adapter": {}},
            "transformers": [],
            "outputs": [],
        }
        self.df_steps = self.fetch_steps()
        self.dict_configs = self.fetch_configs()
        self.fill_in_components()
        self.output = drop_empty(self.output)
        yaml_string = yaml.dump(self.output, sort_keys=False)

        self._attach_file(
            filename="config.yml",
            filepath="ldio",
            content=yaml_string,
        )
        return self.output_reader.graph

    def fetch_steps(self) -> pd.DataFrame:

        # list all LDIO components
        where_statement = """
        ?container a tcs:DockerContainer .
        ?container tcs:instantiates ldio:LinkedDataInteractionsOrchestrator .
        ?container tcs:instantiates ?pipeline_component .
        ?container tcs:runs ?pipeline_step .
        ?pipeline_step prov:specializationOf ?pipeline_component .
        """

        list_components = self.output_reader.select(
            "?pipeline_component", where_statement
        )["pipeline_component"].to_list()

        list_records = []
        for component_id in list_components:
            ldio_label = receive_first(
                self.output_reader.filter(sub=component_id, pred="rdfs:label").df[
                    "obj"
                ],
            )
            ldio_type = receive_first(
                self.output_reader.filter(sub=component_id, pred="ldio:type").df["obj"],
            )

            dict_component = {
                "component": component_id,
                "type": ldio_type,
                "name": ldio_label,
            }

            # If the component has a config assigned, fetch that too
            if self.output_reader.ask(f"{component_id} :isAssigned ?config ."):
                config_id = receive_first(
                    self.output_reader.filter(sub=component_id, pred=":isAssigned").df[
                        "obj"
                    ],
                )
                dict_component.update({"config": config_id})

            list_records += [dict_component]

        return pd.DataFrame.from_records(list_records)

    def fetch_configs(self) -> dict:

        config_list = self.df_steps["config"].to_list()
        config_list = [config for config in config_list if isinstance(config, str)]

        output_dict = {}
        for config_id in config_list:
            config_dict = extract_config(self.output_reader, config_id)
            config_dict = self.output_reader.prefix_store.drop(config_dict)
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
