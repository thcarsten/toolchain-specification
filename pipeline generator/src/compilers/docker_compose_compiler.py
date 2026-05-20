from rdfine import GraphDict, GraphReader
from copy import deepcopy
import yaml
import pandas as pd


class DockerComposeCompiler:
    """
    Compiles the docker compose configuration file.
    """

    def __init__(self, assembled_pipeline: GraphDict) -> None:
        self.graph_dict = deepcopy(assembled_pipeline)
        self.graph_reader = GraphReader(assembled_pipeline.to_graph())

    def compile(self) -> str:
        docker_query = f"""
                    SELECT ?component ?config ?relation 
                    WHERE {{
                    ?config a tc:DockerComposeConfig .
                    ?config :configForComponent ?component .
                    ?config :config_relation ?relation .
                    FILTER(?relation IN ('isDefault', 'isRequired', 'isAssigned')) 
                    }}
                """

        # An overview table of all docker compose configs, in the correct update order
        df_docker = self.graph_reader.execute_query(docker_query)
        # Here I define the order of updates by sorting the df_docker
        order_map = {"isDefault": 0, "isAssigned": 1, "isRequired": 2}
        df_docker = df_docker.sort_values(by="relation", key=lambda s: s.map(order_map))

        # Adding each docker compose config to the output dict
        docker_compose_dict = {}
        for config_id in df_docker["config"].to_list():
            microservice_config = self.graph_dict.get_branch(config_id)[":config"]
            microservice_config = self.graph_dict.prefix_store.drop(microservice_config)
            docker_compose_dict.update(microservice_config)

        self.output = docker_compose_dict

        return yaml.dump(docker_compose_dict, sort_keys=False)
