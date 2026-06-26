from rdflib import Graph
from rdfine import GraphReader, GraphDict, parse_config


class DockerComposeCompiler:
    """
    Compiles the docker compose configuration file.
    """

    def __init__(self, build_graph: Graph) -> None:
        self.graph_reader = GraphReader(build_graph)

    def compile(self):

        # Getting a list of all microservice configs
        config_list = self.graph_reader.query(
            select="?config",
            where=f"""
                 ?config a tcs:DockerComposeConfig ;
        """,
        )["config"].to_list()

        # Compiling the output file based on the config_list
        docker_compose_dict = {}
        for config_id in config_list:
            microservice_graph_dict = GraphDict.from_graph(
                self.graph_reader.traverse(config_id).graph
            )
            microservice_graph_dict = microservice_graph_dict.frame({"@id": config_id})
            microservice_dict = microservice_graph_dict.dict
            microservice_dict = parse_config(microservice_dict)
            microservice_dict = microservice_dict[":config"]
            docker_compose_dict.update(microservice_dict)

        # Serializing the output in the expected format
        self.output = GraphDict(docker_compose_dict, self.graph_reader.prefix_store)
        return self.output.serialize("yml", "drop")
