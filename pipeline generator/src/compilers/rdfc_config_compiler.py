from rdf_extract import Compiler, DataTree, GraphReader, merge_graphs
from rdflib import Graph
import pandas as pd


class RdfcConfigCompiler(Compiler):
    """
    Compiles the pipeline definition file for a Rdf Connect pipeline.
    """

    def __init__(self, pipeline_tree: DataTree) -> None:
        super().__init__()  # Inheriting the init of the parent class
        self.input = pipeline_tree
        self.pipeline_id = pipeline_tree.dict_data.get("@id")
        self.processor_graph: Graph | None = None

        # Creating a graph reader based on the pipeline_tree
        copy_tree = pipeline_tree.copy()
        copy_tree.compact()
        copy_tree.provide_prefixes()
        pipeline_graph = copy_tree.to_graph()
        self.graph_reader = GraphReader(pipeline_graph)
        self.graph_reader.prefix_store.load(
            copy_tree.prefix_store.prefixes, replace=True
        )

    def generate_output(self) -> None:
        self.df_channel_predicates = self.lookup_channel_predicates()
        self.pipeline_graph = self.describe_pipeline()
        self.processor_graph = self.describe_processors()
        self.channel_graph = self.describe_channels()
        output_graph = merge_graphs(
            [self.pipeline_graph, self.processor_graph, self.channel_graph]
        )
        self.output = DataTree(GraphReader(output_graph).to_dict(self.pipeline_id))
        # self.output["@id"] = ""  # The pipeline HAS to be named <>, this is what RDF Connect expects. Otherwise it will not work

    def describe_pipeline(self) -> Graph:

        # Fetching the rdfc:Runners as list
        components = self.input.get("components")
        components.subset(keep=["@type"])
        components = components.to_dict()
        runner_list = [
            component
            for component in components
            if "rdfc:Runner" in components[component]["@type"]
            or "rdfc:Runner" == components[component]["@type"]
        ]

        # Each runner requires its own instanced environment
        env_i = 0  # Simple running index for the environments
        graph_list = []  # I return one graph per runner in this loop
        for runner_id in runner_list:
            env_i += 1
            # The pipeline HAS to be named <>, this is what RDF Connect expects. Otherwise it will not work
            pipeline_query = f"""
                                    CONSTRUCT {{
                                    {self.pipeline_id} a rdfc:Pipeline . 
                                    {self.pipeline_id} rdfc:consistsOf :env_{env_i} .
                                    {self.pipeline_id} owl:imports ?import .
                                    :env_{env_i} rdfc:instantiates {runner_id} .
                                    :env_{env_i} rdfc:processor ?step .
                                    }}
                                    WHERE {{
                                    {self.pipeline_id} na_:components ?all_components_container .
                                    ?all_components_container ?component_pred ?component .
                                    ?component osw:hasDependency {runner_id} .

                                    {self.pipeline_id} na_:steps ?all_steps_container .
                                    ?all_steps_container ?step ?single_step_container .
                                    ?single_step_container na_:component ?component .

                                    ?anycomponent owl:imports ?import .
                                    }}
                                """

            environment_graph = self.graph_reader.execute_query(pipeline_query)
            graph_list.append(environment_graph)

        output_graph = merge_graphs(graph_list)
        return output_graph

    def describe_processors(self) -> Graph:
        """
        Produces triples of format ?step a ?processor . ?processor ?config.
        """

        # Starting a new graph which initializes each pipeline step as a rdfc:Processor
        processors_query = f"""
            CONSTRUCT {{
            ?step a ?component .
            }}
            WHERE {{
            {self.pipeline_id} na_:steps ?all_steps_container .
            ?all_steps_container ?step ?single_step_container .
            ?single_step_container na_:component ?component .
            }}
        """

        output_graph = self.graph_reader.execute_query(processors_query)
        output_graph_reader = GraphReader(output_graph)
        triples_table = output_graph_reader.get_triples()

        # Adding the config for each pipeline step
        for index, row in triples_table.iterrows():
            step_id = row["sub"]
            processor_id = row["obj"]
            config_tree = self.input.get(f"configs.{processor_id}.config")
            config_tree["@id"] = step_id
            config_tree.add_to_graph(output_graph)

        # Returning the resulting output graph as data tree
        return output_graph

    def describe_channels(self) -> Graph:
        """
        Strategy to describe channels:
        Each step in rdfc_pipeline.steps that HAS a prev_step is assigned a channel
        channel name can be simply channel + row_index for now
        prev_step is always the out and step is always the in
        The exact predicates have to be looked up in the node shape of the respective processor
        """
        ######
        # Building a dataframe with all information needed to create the triples regarding channels
        ######

        step_query = f"""
            SELECT ?step ?prev_step ?component
            WHERE {{
            {self.pipeline_id} na_:steps ?all_steps_container .
            ?all_steps_container ?step ?single_step_container .
            ?single_step_container na_:component ?component .
             OPTIONAL {{?single_step_container na_:previous_step ?prev_step .}}
            }}
        """

        df_channels = self.graph_reader.execute_query(step_query)
        df_channels = df_channels.reset_index(drop=True)
        # adding a channel_id per channel
        df_channels["channel_id"] = ":channel_" + df_channels.index.astype(str)
        # Initializing some new columns to be filled in progressively
        df_channels["input_predicate"] = None
        df_channels["output_predicate"] = None

        for index, row in self.df_channel_predicates.iterrows():
            df_channels.loc[
                df_channels["component"] == row["processor"], "input_predicate"
            ] = row["input_predicate"]
            df_channels.loc[
                df_channels["component"] == row["processor"], "output_predicate"
            ] = row["output_predicate"]

        ######
        # Actually building the triples regarding channels
        ######

        output_graph = Graph()

        for index, row in df_channels.iterrows():
            # Grabbing the necessary references
            step = row["step"]
            prev_step = row["prev_step"]
            component = row["component"]
            channel_id = row["channel_id"]
            input_predicate = row["input_predicate"]
            output_predicate = row["output_predicate"]

            # If the current step has not previous step, a channel is not necessary
            if not prev_step:
                continue

            # Adding the relevant triples to the output graph
            channel_query = f"""
                                CONSTRUCT {{
                                {channel_id} a rdfc:Reader, rdfc:Writer .
                                {prev_step} {output_predicate} {channel_id} .
                                {step} {input_predicate} {channel_id} .
                                }}
                                WHERE {{
                                }}
                        """
            channel_graph = self.graph_reader.execute_query(channel_query)

            # Appending the output graph
            output_graph = merge_graphs([output_graph, channel_graph])
        return output_graph

    def lookup_channel_predicates(self) -> pd.DataFrame:

        def _lookup_channel_predicates(nodeShape_id: str):
            """
            Given the nodeShape_id, looks up the predicate names related to rdfc:Reader and rdfc:Writer
            """
            # I first extract a subgraph because it allows for a simpler select query afterwards
            nodeShapeGraph = self.graph_reader.extract_subgraph(nodeShape_id)

            query = f"""        
                            SELECT ?input_predicate ?output_predicate
                            WHERE {{
                                OPTIONAL {{ 
                                    ?bnw sh:class rdfc:Writer . 
                                    ?bnw sh:path ?output_predicate .
                                }}
                                OPTIONAL {{ 
                                ?bnr sh:class rdfc:Reader .
                                ?bnr sh:path ?input_predicate . 
                                }}
                            }}
            """

            # Execute query that looks up channel predicates
            df_results = GraphReader(nodeShapeGraph).execute_query(query)
            return df_results

        # For each processor that is responsible for a PipelineStep, look up the channel predicates and attach to self.components
        list_channel_predicates = []
        # Absolutely disgusting but it works
        for processor_id in [
            self.input.get(f"steps.{step_id}.component")["@id"]
            for step_id in self.input.get("steps").to_dict().keys()
        ]:

            df_output = pd.concat(
                [
                    _lookup_channel_predicates(constraint_id)
                    for constraint_id in self.input.get(f"constraints.{processor_id}")
                    .to_dict()
                    .keys()
                ],
                ignore_index=True,
            )

            # raise exception if no predicates where found for processor_id
            if len(df_output) == 0:
                raise (
                    LookupError(
                        f"No channel predicates were found for {processor_id} in NodeShapes {pipeline_extractor.constraints.get(processor_id)}."
                    )
                )
            elif len(df_output) > 1:
                raise (
                    LookupError(
                        f"Channel predicates ambiguous, several found for {processor_id} in NodeShapes {pipeline_extractor.constraints.get(processor_id)}."
                    )
                )
            else:
                dict_channel_predicates = {
                    "processor": processor_id,
                    "input_predicate": list(df_output["input_predicate"])[0],
                    "output_predicate": list(df_output["output_predicate"])[0],
                }
                list_channel_predicates.append(dict_channel_predicates)

        return pd.DataFrame(list_channel_predicates)
