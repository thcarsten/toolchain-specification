from rdflib import Graph
from rdfine import GraphReader, GraphDict, receive_first

from .base import Compiler
from .utils import attach_file, extract_config, rewrite_compose_volume_host_path

# The RDF-Connect Python / Node runners mount their pipeline
# definition at this fixed container path. Kept next to the
# host-side output location so the pairing is visible in one place.
_RDFC_PIPELINE_CONTAINER_PATH = "/workspace/pipeline/pipeline.ttl"
_RDFC_PIPELINE_HOST_PATH = "./rdfc/pipeline.ttl"


class RdfcConfigCompiler(Compiler):
    """
    Compiles the pipeline definition file for a Rdf Connect pipeline.

    Also patches the paired ``tcs:DockerComposeConfig`` on
    ``rdfc:Orchestrator`` so its volume mount for ``pipeline.ttl``
    points at ``./rdfc/pipeline.ttl`` — the host location this
    compiler actually emits to. Doing that here rather than in
    :class:`DockerComposeCompiler` keeps the aggregator generic:
    each framework compiler shapes its own compose fragment, then
    the aggregator merges them.
    """

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        # Intermediate state — populated in ``compile``. ``rdfc_reader``
        # is a *separate* accumulator that holds only the RDF-Connect
        # pipeline triples eventually serialized into ``pipeline.ttl``;
        # it is not the compiler's output graph. The build graph the
        # compiler contributes to (an ``spdx:File`` node with the
        # serialized ttl) is managed via the base-class
        # :attr:`output_reader` through :func:`compilers.utils.attach_file`.
        self.rdfc_reader: GraphReader = GraphReader(Graph())
        self.pipeline_id: str = ""

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Triggered when a container instantiates the RDF-Connect orchestrator."""
        return not graph_reader.filter(
            pred="tcs:instantiates", obj="rdfc:Orchestrator"
        ).df.empty

    def compile(self) -> Graph:
        self.rdfc_reader = self.input_reader.construct(
            "?pipeline a rdfc:Pipeline .",
            "?pipeline a tcs:PipelineDefinition",
        )
        self.pipeline_id = receive_first(
            self.input_reader.filter(pred="rdf:type", obj="tcs:PipelineDefinition").df[
                "sub"
            ],
        )

        self.describe_pipeline()
        self.describe_processors()
        self.describe_configs()
        self.describe_channels()

        ttl_string = self.rdfc_reader.serialize("ttl")
        # RDF Connect requires the pipeline to be named ``<>``.
        ttl_string = ttl_string.replace(self.pipeline_id, "<>")

        self.output_reader = attach_file(
            self.output_reader,
            filename="pipeline.ttl",
            filepath="rdfc",
            content=ttl_string,
        )

        self.output_reader = rewrite_compose_volume_host_path(
            self.output_reader,
            component_iri="rdfc:Orchestrator",
            container_path=_RDFC_PIPELINE_CONTAINER_PATH,
            host_path=_RDFC_PIPELINE_HOST_PATH,
        )
        return self.output_reader.graph

    def describe_pipeline(self) -> None:
        """
        Adds:
            - {self.pipeline_id} rdfc:consistsOf :env_{env_i} .
            - {self.pipeline_id} owl:imports ?import .
            - :env_{env_i} rdfc:instantiates {runner_id} .
            - :env_{env_i} rdfc:processor ?step .
        """

        # Fetching the rdfc:Runners as list
        runner_list = (
            self.input_reader.filter(pred="rdf:type", obj="rdfc:Runner")
            .df["sub"]
            .to_list()
        )

        # Each runner requires its own instanced environment
        env_i = 0  # Simple running index for the environments
        for runner_id in runner_list:
            env_i += 1
            # The pipeline HAS to be named <>, this is what RDF Connect expects. Otherwise it will not work
            runner_reader = self.input_reader.construct(
                f"""
                    {self.pipeline_id} rdfc:consistsOf :env_{env_i} . 
                     {self.pipeline_id} owl:imports ?import .
                    :env_{env_i} rdfc:instantiates {runner_id} .
                    :env_{env_i} rdfc:processor ?step . """,
                f"""
                    ?processor dct:requires {runner_id} .
                    ?processor owl:imports ?import .
                    ?step prov:specializationOf ?processor . 
                    """,
            )

            self.rdfc_reader = self.rdfc_reader.add(runner_reader.graph)

    def describe_processors(self) -> None:
        """
        Adds:
            - ?step a ?processor .
            - ?processor ?config .
        """

        processor_reader = self.input_reader.construct(
            """
                ?step a ?component . 
                """,
            """            
                ?step prov:specializationOf ?component .
                ?container tcs:instantiates ?component .
                ?container tcs:instantiates rdfc:Orchestrator .
                ?container tcs:runs ?step .
            """,
        )

        self.rdfc_reader = self.rdfc_reader.add(processor_reader.graph)

    def describe_configs(self) -> None:
        """
        Adds:
            - ?processor ?config ... .
        """

        step_list = self.rdfc_reader.filter(pred="rdfc:processor").df["obj"].to_list()
        config_df = self.input_reader.filter(
            sub=step_list, pred="p-plan:hasInputVar"
        ).df
        config_list = config_df["obj"].to_list()

        for config_id in config_list:
            step_id = receive_first(
                self.input_reader.filter(
                    sub=step_list, pred="p-plan:hasInputVar", obj=config_id
                ).df["sub"],
            )
            config_dict = extract_config(self.input_reader, config_id)
            config_dict["@id"] = step_id
            config_graph = GraphDict(
                config_dict, prefix_store=self.input_reader.prefix_store
            ).graph
            self.rdfc_reader = self.rdfc_reader.add(config_graph)

    def describe_channels(self) -> None:
        """Emit the ``?channel a rdfc:Reader, rdfc:Writer`` boilerplate.

        Concrete step→channel wiring (``?step rdfc:reader ?channel`` /
        ``?step rdfc:writer ?channel`` / ``?step rdfc:memberStream
        ?channel`` etc.) is the PipelineDefinition author's
        responsibility: it lives in each step's ``tcs:embedded``
        config and is emitted verbatim by :meth:`describe_configs`.
        Only the author knows which framework predicate their step
        expects — the compiler cannot guess it reliably.

        What the compiler *can* do without ambiguity is add the
        uniform type declaration every RDF-Connect channel needs.
        Scope of "channel worth typing": any ``tcs:Channel`` already
        referenced in the emitted pipeline graph (``self.rdfc_reader``).
        ``describe_configs`` pulls in each config's ``tcs:embedded``
        block via ``GraphDict``/``traverse``, which follows through
        to every channel IRI referenced by the config and picks up
        the ``?ch a tcs:Channel`` triple that ``inference_rules.yaml``
        adds from ``tcs:readsFrom`` / ``tcs:writesTo``. Constraining
        on those already-present triples means we emit boilerplate
        exactly for the channels the pipeline.ttl uses — no dead
        declarations for cross-framework channels whose RDFC-side
        step has no wiring config referencing them (e.g., an LDIO→RDFC
        boundary channel that only appears as a ``tcs:readsFrom``
        annotation).
        """
        channel_types = self.rdfc_reader.construct(
            "?ch a rdfc:Reader, rdfc:Writer .",
            "?ch a tcs:Channel .",
        ).graph
        self.rdfc_reader = self.rdfc_reader.add(channel_types)
