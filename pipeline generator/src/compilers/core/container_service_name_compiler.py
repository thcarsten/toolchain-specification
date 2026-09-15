from rdflib import Graph

from rdfine import GraphReader

from ..compiler_abc import Compiler
from ..utils import parse_docker_compose_config, prefer_non_default_compose_configs


class ContainerServiceNameCompiler(Compiler):
    """Project the docker-compose service names a container contributes
    onto it as ``tcs:serviceName``.

    Multi-valued on purpose. :class:`DockerComposeCompiler` emits one
    stanza per *component* that owns a ``tcs:DockerComposeConfig``, and a
    container can instantiate several of them —
    :class:`BridgeTransportCompiler` attaches an inserted boundary
    component to the container on its side of the hop, which for
    ``sw:rdf-ingest-service`` means one container carrying both the
    ingest service and the triple store. So this reads "the services this
    container contributes to the compose file", not "the one name of this
    container", and deliberately does not reuse
    ``lookup_container_service_name``, which answers the singular
    question and would silently drop the rest.

    The name is the top-level key of the service stanza, and it lives
    only inside the YAML string in the config's ``tcs:literal``. That
    makes it invisible to anything that reasons in triples — SHACL most
    of all, which has no way to parse YAML. Lifting it into the graph is
    what lets ``tcs:BridgeEndpointReachableShape`` check that a bridge's
    ``tcs:endpoint`` names a host the compose file will actually declare.

    This is a projection, not a second source of truth: the literal stays
    authoritative and the triple is derived from it, in the same spirit
    as the ``tcs:endpoint`` / ``tcs:port`` the boundary config compilers
    write onto a channel rather than leaving buried in a config body.

    Runs in the finalize phase because the winning compose config is not
    settled before then — ``prefer_non_default_compose_configs`` exists
    precisely because a non-default config can supersede a default one,
    and :class:`SemanticWorksEnvVarCompiler` rewrites compose literals in
    place. Deriving the name at container-mint time would risk pinning a
    name that a later compiler changes. It is listed ahead of
    :class:`ValidationReportCompiler`, which becomes eligible in the same
    pass and consumes what this writes.
    """

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        finalized = not graph_reader.filter(
            pred="tcs:runPhase", obj="tcs:FinalizePhase"
        ).df.empty
        if not finalized:
            return False
        return not graph_reader.select(
            "?container",
            """
            ?container a tcs:DockerContainer .
            FILTER NOT EXISTS { ?container tcs:serviceName ?name }
            """,
        ).empty

    def compile(self) -> Graph:
        self.project_service_names()
        return self.output_reader.graph

    def project_service_names(self) -> None:
        containers = (
            self.output_reader.select(
                "?container",
                """
                ?container a tcs:DockerContainer .
                FILTER NOT EXISTS { ?container tcs:serviceName ?name }
                """,
            )["container"]
            .drop_duplicates()
            .to_list()
        )

        for container in containers:
            for service in self._lookup_services(container):
                new_triples = self.output_reader.construct(
                    f'{container} tcs:serviceName "{service}" .',
                    f"{container} a tcs:DockerContainer .",
                ).graph
                self.output_reader = self.output_reader.add(new_triples)

    def _lookup_services(self, container: str) -> list[str]:
        """Every compose service name the components on ``container``
        declare.

        A container whose components own no resolvable compose stanza
        contributes nothing and yields an empty list — left alone rather
        than flagged, since tcs:DockerContainerShape already covers a
        container that instantiates nothing usable.
        """
        services: list[str] = []
        components = (
            self.output_reader.select(
                "?component",
                f"{container} tcs:instantiates ?component .",
            )["component"]
            .drop_duplicates()
            .to_list()
        )

        for component in components:
            configs = prefer_non_default_compose_configs(
                self.output_reader,
                self.output_reader.select(
                    "?config",
                    f"""
                    {component} tcs:config ?config .
                    ?config a tcs:DockerComposeConfig .
                    """,
                )["config"]
                .drop_duplicates()
                .to_list(),
            )
            for config in configs:
                body = parse_docker_compose_config(self.output_reader, config)
                services.extend((body.get("services") or {}).keys())

        return services
