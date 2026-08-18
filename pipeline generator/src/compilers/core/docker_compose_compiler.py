from rdflib import Graph
from rdfine import GraphReader, GraphDict

from ..base import Compiler
from ..utils import attach_file, parse_docker_compose_config


class DockerComposeCompiler(Compiler):
    """
    Compiles the docker compose configuration file.

    Runs only once the shaping loop has settled, because other compilers
    (e.g. :class:`SemanticWorksEnvVarCompiler`) may still be editing
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
        self.merge_docker_compose_configs()
        self.attach_docker_compose_file()
        return self.output_reader.graph

    def merge_docker_compose_configs(self) -> None:
        """Aggregate every ``tcs:DockerComposeConfig`` in the build graph
        into one normalized compose-file dict, stashed on
        :attr:`compose_file` for :meth:`attach_docker_compose_file` to
        serialize.
        """
        # Sorted for reproducibility — SPARQL SELECT row order isn't
        # guaranteed stable, and this loop's order determines which
        # config the collision guard below blames second.
        config_list = sorted(
            self.output_reader.select(
                "?config",
                """
                     ?config a tcs:DockerComposeConfig ;
            """,
            )["config"].to_list()
        )

        # Every ``tcs:DockerComposeConfig`` is normalized to the same
        # compose-file shape (``{"services": {...}, ...}``) by
        # ``parse_docker_compose_config``, so aggregating multiple
        # configs is a merge per top-level key. Two configs are never
        # *expected* to name the same service/volume/network entry —
        # docker-compose requires unique names, so that's always a
        # modelling mistake; raise instead of letting one silently
        # clobber the other.
        compose_file: dict = {"services": {}}
        contributed_by: dict[str, str] = {}
        for config_id in config_list:
            normalized = parse_docker_compose_config(self.output_reader, config_id)
            for key, val in normalized.items():
                if isinstance(compose_file.get(key), dict) and isinstance(val, dict):
                    for name in val:
                        owner = contributed_by.get(f"{key}.{name}")
                        if owner is not None and owner != config_id:
                            raise ValueError(
                                f"tcs:DockerComposeConfig {config_id!s} and {owner!s} "
                                f"both declare a {key!r} entry named {name!r} — "
                                "docker-compose requires unique names; rename one "
                                "of them in the catalog/pipeline definition."
                            )
                        contributed_by[f"{key}.{name}"] = config_id
                    compose_file[key].update(val)
                else:
                    compose_file[key] = val

        self.compose_file = compose_file

    def attach_docker_compose_file(self) -> None:
        """Serialize :attr:`compose_file` to YAML and attach it as
        ``./docker-compose.yml`` on the build.
        """
        yaml_string = GraphDict(
            self.compose_file, prefix_store=self.output_reader.prefix_store
        ).serialize("yml", "drop")

        self.output_reader = attach_file(
            self.output_reader,
            filename="docker-compose.yml",
            filepath=".",
            content=yaml_string,
        )
