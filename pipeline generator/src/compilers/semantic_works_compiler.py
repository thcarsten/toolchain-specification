from rdflib import Graph, URIRef, Literal
from rdfine import GraphReader, GraphDict, parse_config
import pandas as pd
import json


class SemanticWorksCompiler:
    """
    Updates DockerComposeConfigs of mu:Microservices by the environment variables which were assigned to InstancePipelineComponents as part of the PipelineDefinition.
    """

    def __init__(self, build_graph: Graph) -> None:
        self.graph_reader = GraphReader(build_graph)

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
        component_df = self.graph_reader.query(
            select="?component ?step ?step_config ?docker_config",
            where=f"""
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
        config_dict = GraphDict.from_graph(self.graph_reader.traverse(config_id).graph)
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
        step_config_id = self.component_df.loc[
            self.component_df["component"] == component_id, "step_config"
        ].to_list()[0]
        docker_config_id = self.component_df.loc[
            self.component_df["component"] == component_id, "docker_config"
        ].to_list()[0]

        # Extracting the two configs
        step_config_dict = self.extract_config(step_config_id)
        docker_config_dict = self.extract_config(docker_config_id)

        # Finding the right level in docker_config_dict to append step_config_dict to
        path_to_image = docker_config_dict.find(path_pattern="image$")[
            "path"
        ].to_list()[0]
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
        add_triples = GraphReader.from_df(
            pd.DataFrame.from_records([config_record]), self.graph_reader.prefix_store
        ).graph
        self.graph_reader = self.graph_reader.add(add_triples)
