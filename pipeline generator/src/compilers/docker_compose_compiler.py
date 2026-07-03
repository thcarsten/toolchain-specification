from rdflib import Graph
from rdfine import GraphReader, GraphDict

from .base import Compiler
from .utils import parse_docker_compose_config


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
        config_list = self.output_reader.select(
            "?config",
            """
                 ?config a tcs:DockerComposeConfig ;
        """,
        )["config"].to_list()

        # Every ``tcs:DockerComposeConfig`` is normalized to the same
        # compose-file shape (``{"services": {...}, ...}``) by
        # ``parse_docker_compose_config``, so aggregating multiple
        # configs is just a shallow merge per top-level key. Later
        # configs override earlier ones on name collision within
        # ``services`` / ``volumes`` / ``networks`` / etc.
        compose_file: dict = {"services": {}}
        for config_id in config_list:
            normalized = parse_docker_compose_config(self.output_reader, config_id)
            for key, val in normalized.items():
                if isinstance(compose_file.get(key), dict) and isinstance(val, dict):
                    compose_file[key].update(val)
                else:
                    compose_file[key] = val

        # Serializing the output in the expected format
        yaml_string = GraphDict(
            compose_file, prefix_store=self.output_reader.prefix_store
        ).serialize("yml", "drop")

        self._attach_file(
            filename="docker-compose.yml",
            filepath=".",
            content=yaml_string,
        )
        return self.output_reader.graph
