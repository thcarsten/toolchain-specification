from rdflib import Graph
from rdfine import GraphReader, GraphDict, receive_first
import pandas as pd

from .base import Compiler
from .utils import extract_config


class RdfcConfigCompiler(Compiler):
    """
    Compiles the pipeline definition file for a Rdf Connect pipeline.
    """

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        # Intermediate state — populated in ``compile``. ``rdfc_reader``
        # is a *separate* accumulator that holds only the RDF-Connect
        # pipeline triples eventually serialized into ``pipeline.ttl``;
        # it is not the compiler's output graph. The build graph the
        # compiler contributes to (a ``tcs:File`` node with the
        # serialized ttl) is managed via the base-class
        # :attr:`output_reader` through :meth:`_attach_file`.
        self.rdfc_reader: GraphReader = GraphReader(Graph())
        self.pipeline_id: str = ""
        self.df_channel: pd.DataFrame = pd.DataFrame()

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
        self.df_channel = self.create_channel_overview()
        self.describe_channels()

        ttl_string = self.rdfc_reader.serialize("ttl")
        # RDF Connect requires the pipeline to be named ``<>``.
        ttl_string = ttl_string.replace(self.pipeline_id, "<>")

        self._attach_file(
            filename="pipeline.ttl",
            filepath="rdfc",
            content=ttl_string,
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

    def create_channel_overview(self) -> pd.DataFrame:
        """
        Adds:
            - <channel_i> a rdfc:Writer, rdfc:Reader .
            - ?step rdfc:incoming <channel_1> .
            - ?step rdfc:outgoing <channel_2> .

        Strategy:
            Each step in rdfc_pipeline.steps that HAS a prev_step is assigned a channel
            channel name can be simply channel + row_index for now
            prev_step is always the out and step is always the in
            The exact predicates have to be looked up in the node shape of the respective processor
        """

        df_channel = self.input_reader.select(
            """
                ?step ?prev_step ?component 
                """,
            """            
                ?step prov:specializationOf ?component .
                ?container tcs:instantiates rdfc:Orchestrator .
                ?container tcs:instantiates ?component .
                ?container tcs:runs ?step .
                OPTIONAL {?step p-plan:isPrecededBy ?prev_step .
                ?container tcs:runs ?prev_step .}
            """,
        )

        # Adding unique channel names
        df_channel["channel_id"] = ":channel_" + df_channel.index.astype(str)
        # Adding some placeholder channel predicates
        df_channel["input_predicate"] = ":in"
        df_channel["output_predicate"] = ":out"

        return df_channel

    def describe_channels(self) -> None:

        df_channels = self.df_channel.loc[self.df_channel["prev_step"].notna()]

        for index, row in df_channels.iterrows():
            # Grabbing the necessary references
            step = row["step"]
            prev_step = row["prev_step"]
            component = row["component"]
            channel_id = row["channel_id"]
            input_predicate = row["input_predicate"]
            output_predicate = row["output_predicate"]

            # Adding the relevant triples to the output graph
            channel_reader = self.rdfc_reader.construct(
                f"""
                    {channel_id} a rdfc:Reader, rdfc:Writer .
                    {prev_step} {output_predicate} {channel_id} .
                    {step} {input_predicate} {channel_id} . """,
                "",
            )

            # Appending the output graph
            self.rdfc_reader = self.rdfc_reader.add(channel_reader.graph)
