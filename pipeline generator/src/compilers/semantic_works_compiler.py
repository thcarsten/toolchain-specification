from rdflib import Graph, URIRef, Literal
from rdfine import GraphReader, GraphDict, parse_config
import pandas as pd
import json

from .utils import receive_first
from .base import Compiler


class SemanticWorksCompiler(Compiler):
    """
    Updates DockerComposeConfigs of mu:Microservices by the environment variables which were assigned to InstancePipelineComponents as part of the PipelineDefinition.
    """

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        # Intermediate state — populated in ``compile``.
        self.component_df: pd.DataFrame = pd.DataFrame()

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Triggered by any ``tcs:PipelineComponent`` in the ``sw:`` namespace,
        but only once ``PipelineAssembler`` has produced ``tcs:DockerContainer``
        nodes — otherwise there is nothing to fold env vars into yet."""
        if graph_reader.filter(pred="rdf:type", obj="tcs:DockerContainer").df.empty:
            return False
        df = graph_reader.filter(pred="rdf:type", obj="tcs:PipelineComponent").df
        return bool(df["sub"].str.startswith("sw:").any())

    def compile(self) -> Graph:
        self.fetch_components()
        for component_id in self.component_df["component"].to_list():
            self.update_docker_config(component_id)
        return self.graph_reader.graph

    def fetch_components(self) -> None:
        """
        Identifies any SemanticWorks-components which need to be considered
        """

        # Checking that a bunch of conditions are met
        component_df = self.graph_reader.select(
            "?component ?step ?step_config ?docker_config",
            """
            ?component a tcs:PipelineComponent .
            ?step prov:specializationOf ?component .
            ?step p-plan:hasInputVar ?step_config .
            ?component tcs:config ?docker_config .
            ?docker_config a tcs:DockerComposeConfig .
            """,
        )

        # Making sure it is a semantic.works component
        component_df = component_df.loc[component_df["component"].str.startswith("sw:")]
        self.component_df = component_df

    # Extracting the docker_config_dict
    def extract_config(self, config_id) -> GraphDict:
        config_dict = GraphDict(self.graph_reader.traverse(config_id).graph)
        config_dict = config_dict.frame({"@id": config_id})
        config_dict.dict = parse_config(config_dict.dict)
        config_dict = config_dict.get(":config")
        return config_dict

    def update_docker_config(self, component_id) -> None:
        """
        The strategy here is surprisingly simple:
        - fetch the DockerComposeConfig
        - turn it into a regular dict
        - fetch the StepConfig
        - turn it into a regular dict
        - append the DockerComposeConfig with the Step Config
        - turn it into a string and update the DockerComposeConfig by updating with a new tcs:literal
        """

        # Grabbing the right references
        step_config_id = receive_first(
            self.component_df.loc[
                self.component_df["component"] == component_id, "step_config"
            ],
        )
        docker_config_id = receive_first(
            self.component_df.loc[
                self.component_df["component"] == component_id, "docker_config"
            ],
        )

        # Extracting the two configs
        step_config_dict = self.extract_config(step_config_id)
        docker_config_dict = self.extract_config(docker_config_id)

        # Finding the right level in docker_config_dict to append step_config_dict to
        path_to_image = receive_first(
            docker_config_dict.find(path_pattern="image$")["path"],
        )
        target_path = path_to_image.rsplit(".", 1)[0]
        target_dict = docker_config_dict.get(target_path).dict

        # Checking whether target_dict already contains a "environment"-key
        key_list = list(target_dict.keys())
        key_list = [k for k in key_list if "environment" in k]
        if len(key_list) > 0:
            environment_key = key_list[0]
        else:
            environment_key = "environment"
            target_dict[environment_key] = {}

        # Updating the target_dict by the step_config_dict
        target_dict[environment_key].update(step_config_dict.dict)
        target_dict = docker_config_dict.prefix_store.drop(target_dict)
        docker_config_dict = docker_config_dict.set(target_path, target_dict)
        docker_config_str = json.dumps(docker_config_dict.dict)

        # Updating the docker compose config in the graph
        remove_triples = self.graph_reader.filter(
            sub=docker_config_id, pred=["tcs:literal", "tcs:embedded"]
        ).graph
        self.graph_reader = self.graph_reader.remove(remove_triples)
        config_record = {
            "sub": docker_config_id,
            "pred": "tcs:literal",
            "obj": docker_config_str,
            "sub_type": URIRef,
            "obj_type": Literal,
        }
        add_triples = GraphReader(
            pd.DataFrame.from_records([config_record]), self.graph_reader.prefix_store
        ).graph
        self.graph_reader = self.graph_reader.add(add_triples)
