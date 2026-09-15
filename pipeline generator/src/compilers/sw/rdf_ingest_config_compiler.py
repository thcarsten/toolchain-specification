from rdflib import Graph

from rdfine import GraphReader

from ..compiler_abc import Compiler
from ..utils import parse_docker_compose_config, prefer_non_default_compose_configs


#: Steps specializing the service that read a channel crossing a
#: container boundary which nobody has annotated yet. Requiring the
#: hop to be cross-container is what keeps this compiler from firing
#: before :class:`PipelineAssembler` has assigned steps to containers
#: — a user-declared ``sw:rdf-ingest-service`` step exists from the
#: very first pass, and annotating its channel then would both run
#: ahead of the assembler and wrongly annotate a purely intra-container
#: channel, which needs no bridge at all.
CROSS_CONTAINER_UNANNOTATED = """
            ?step a tcs:InstancePipelineComponent ;
                  prov:specializationOf sw:rdf-ingest-service ;
                  tcs:readsFrom ?channel .
            ?reader_container tcs:runs ?step .
            ?writer tcs:writesTo ?channel .
            ?writer_container tcs:runs ?writer .
            FILTER (?writer_container != ?reader_container)
            FILTER NOT EXISTS { ?channel tcs:endpoint ?endpoint }
"""


class SwRdfIngestConfigCompiler(Compiler):
    """Write transport metadata onto the channels read by
    ``sw:rdf-ingest-service`` steps — typically boundary steps inserted
    by :class:`BridgeTransportCompiler`.

    The Entry half of the HTTP bridge contract: annotate the shared
    cross-container channel with ``tcs:endpoint``, ``tcs:port`` and
    ``tcs:contentType`` so the paired Exit compiler on the upstream
    container (``LdioHttpOutConfigCompiler``,
    ``RdfcHttpOutConfigCompiler``, ``NifiInvokeHttpConfigCompiler``)
    can point its own step at this service.

    Two things set this apart from the other Entry compilers
    (``LdioHttpInConfigCompiler``, ``RdfcHttpServerConfigCompiler``,
    ``NifiListenHttpConfigCompiler``):

    - **The endpoint does not name this step's own service.** The
      other three *are* the host you talk to, so they resolve their
      own container's compose service name via
      ``lookup_container_service_name``. A mu stack is entered at
      :attr:`gateway_component` instead: the identifier attaches the
      session header, the dispatcher routes ``/ingest`` onward, and
      mu-authorization derives the target graph from that session —
      which is why the service's INSERTs carry no ``GRAPH`` clause at
      all. POSTing straight at ``rdf-ingest`` would skip the
      identifier, leaving authorization with no rights to reason
      about and the write refused as ``403``. So the endpoint names
      the gateway's own compose service instead.

      The name is read off :attr:`gateway_component`'s
      ``tcs:DockerComposeConfig`` rather than off a
      ``tcs:DockerContainer``, which is why this does not reuse
      ``lookup_container_service_name``: the infrastructure services
      satisfying a ``dct:requires`` chain carry no step of their own,
      and no container node for them is visible at the point the
      per-boundary config compilers run — they surface in the compose
      file later. Reading the catalog config keeps the lookup
      independent of that ordering while still deriving the hostname
      instead of hardcoding it, so renaming the service in
      ``:MuIdentifierDockerCompose`` cannot silently desynchronise the
      generated endpoint.
    - **No config is attached to the step.** The other three own a
      per-instance config body (a port to allocate, a path to serve);
      this service has neither — the route is fixed by the
      dispatcher's ``/ingest`` match and the mu-javascript-template
      listens on :attr:`default_port`. Attaching an empty
      ``p-plan:hasInputVar`` would also put an empty config in front
      of :class:`SemanticWorksEnvVarCompiler`, which folds step
      configs into the service's compose ``environment:`` block. The
      idle condition is therefore the *channel* already carrying a
      ``tcs:endpoint``, not the step already carrying a config —
      without which the fixpoint would never settle.
    """

    #: Catalog component whose container fronts the mu stack.
    gateway_component: str = "sw:mu-identifier"
    #: Route the dispatcher forwards to this service (see
    #: ``:MuDispatcherExDefault`` in ``catalog-sw.ttl``).
    ingest_path: str = "/ingest"
    #: Port the mu-javascript-template listens on. Recorded on the
    #: channel for parity with the other Entry compilers; it is the
    #: HTTP default, so it is left out of the endpoint URL.
    default_port: int = 80
    #: Content-Type advertised to the paired Exit compiler. The service
    #: picks its parser from this header. N-Triples is line-based and
    #: prefix-free, so it streams without the writer having to agree on
    #: a prefix table — the safest default for an auto-inserted bridge.
    default_content_type: str = "application/n-triples"

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        return not graph_reader.select("?step", f"""
            {CROSS_CONTAINER_UNANNOTATED}
            """).empty

    def compile(self) -> Graph:
        self.annotate_unconfigured_channels()
        return self.output_reader.graph

    def annotate_unconfigured_channels(self) -> None:
        rows = self.output_reader.select(
            "?step ?channel",
            f"""
            {CROSS_CONTAINER_UNANNOTATED}
            """,
        ).drop_duplicates()
        for _, row in rows.iterrows():
            service = self._lookup_gateway_service_name(row["step"])
            self._annotate_channel(row["channel"], service)

    def _lookup_gateway_service_name(self, step: str) -> str:
        """Return the compose service name of the gateway fronting the mu
        stack for ``step``.

        Confirms first that the step's component really does reach
        :attr:`gateway_component` along ``dct:requires`` — the chain
        that makes the gateway part of this build at all — then reads
        the service name off the gateway's own compose config.
        """
        if not self.output_reader.ask(
            f"""
            {step} prov:specializationOf ?component .
            ?component dct:requires* {self.gateway_component} .
            """
        ):
            raise ValueError(
                f"Cannot resolve the mu stack gateway for {step}: its "
                f"component does not reach {self.gateway_component} along "
                "dct:requires. sw:rdf-ingest-service must be entered through "
                "the identifier so mu-authorization can derive the target "
                "graph from the session; a component bypassing it cannot be "
                "reached from another container."
            )

        configs = prefer_non_default_compose_configs(
            self.output_reader,
            self.output_reader.select(
                "?config",
                f"""
                {self.gateway_component} tcs:config ?config .
                ?config a tcs:DockerComposeConfig .
                """,
            )["config"].drop_duplicates().to_list(),
        )
        service = None
        if configs:
            services = parse_docker_compose_config(
                self.output_reader, configs[0]
            ).get("services") or {}
            service = next(iter(services), None)

        if service is None:
            raise ValueError(
                f"{self.gateway_component} owns no resolvable "
                "tcs:DockerComposeConfig service name to build the "
                f"endpoint for {step} from."
            )
        return service

    def _annotate_channel(self, channel: str, service: str) -> None:
        endpoint = f"http://{service}{self.ingest_path}"
        new_triples = self.output_reader.construct(
            f"""
            {channel} tcs:endpoint "{endpoint}" ;
                      tcs:port {self.default_port} ;
                      tcs:contentType "{self.default_content_type}" .
            """,
            f"{channel} a tcs:Channel .",
        ).graph
        self.output_reader = self.output_reader.add(new_triples)
