from rdflib import Graph
from rdfine import GraphReader, GraphDict, parse_config

from .base import Compiler


class DockerComposeCompiler(Compiler):
    """
    Compiles the docker compose configuration file.

    Runs only once the shaping loop has settled, because other compilers
    (e.g. :class:`SemanticWorksCompiler`) may still be editing
    ``tcs:DockerComposeConfig`` bodies. ``PipelineGenerator`` signals
    that settling has occurred by adding ``<build> tcs:isFinishing true``
    to the graph.
    """

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Triggered once ``tcs:isFinishing true`` is set on the build and
        at least one ``tcs:DockerComposeConfig`` node is present."""
        if graph_reader.filter(pred="tcs:isFinishing", obj=True).df.empty:
            return False
        return not graph_reader.filter(
            pred="rdf:type", obj="tcs:DockerComposeConfig"
        ).df.empty

    def compile(self) -> Graph:

        # Getting a list of all microservice configs
        config_list = self.graph_reader.select(
            "?config",
            """
                 ?config a tcs:DockerComposeConfig ;
        """,
        )["config"].to_list()

        # Compiling the output file based on the config_list
        docker_compose_dict = {}
        for config_id in config_list:
            microservice_graph_dict = GraphDict(
                self.graph_reader.traverse(config_id).graph
            )
            microservice_graph_dict = microservice_graph_dict.frame({"@id": config_id})
            microservice_dict = microservice_graph_dict.dict
            microservice_dict = parse_config(microservice_dict)
            microservice_dict = microservice_dict[":config"]
            docker_compose_dict.update(microservice_dict)

        # Serializing the output in the expected format
        yaml_string = GraphDict(
            docker_compose_dict, prefix_store=self.graph_reader.prefix_store
        ).serialize("yml", "drop")

        self._attach_file(
            filename="docker-compose.yml",
            filepath=".",
            content=yaml_string,
        )
        return self.graph_reader.graph
