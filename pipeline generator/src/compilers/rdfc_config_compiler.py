from rdfine import GraphDict, GraphReader, merge_graphs
from rdflib import Graph
import pandas as pd
from copy import deepcopy


class RdfcConfigCompiler:
    """
    Compiles the pipeline definition file for a Rdf Connect pipeline.
    """

    def __init__(self, assembled_pipeline: GraphDict) -> None:
        self.graph_dict = deepcopy(assembled_pipeline)
        self.graph_reader = GraphReader(assembled_pipeline.to_graph())
        self.pipeline_id = assembled_pipeline.get("@id")

    def compile(self) -> None:
        self.pipeline_graph = self.describe_pipeline()
        self.pipeline_required_graph = self.describe_pipeline_required()
        self.processor_graph = self.describe_processors()
        self.df_channel_predicates = self.lookup_channel_predicates()
        self.channel_graph = self.describe_channels()
        output_graph = merge_graphs(
            [
                self.pipeline_graph,
                self.pipeline_required_graph,
                self.processor_graph,
                self.channel_graph,
            ]
        )
        self.output = output_graph
        self.pipeline_definition = self.output.serialize(format="turtle")
        self.pipeline_definition = self.pipeline_definition.replace(
            self.pipeline_id, "<>"
        )
        return self.pipeline_definition

        # self.output["@id"] = ""  # The pipeline HAS to be named <>, this is what RDF Connect expects. Otherwise it will not work

    def tab_processors(self) -> pd.DataFrame:
        """
        Looks up processors that are instantiated by the rdfc:Orchestrator microservice.
        """

        # Starting a new graph which initializes each pipeline step as a rdfc:Processor
        processors_query = f"""
            SELECT  ?step ?component 
            WHERE {{
            ?step :isCarriedOutBy ?component .
            rdfc:Orchestrator :instantiates ?component .
            rdfc:Orchestrator :executes ?step .
            }}
        """

        return self.graph_reader.execute_query(processors_query)

    def describe_pipeline(self) -> Graph:

        # Fetching the rdfc:Runners as list
        runner_list = self.graph_reader.get_triples(pred="rdf:type", obj="rdfc:Runner")[
            "sub"
        ].to_list()

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
                                    :env_{env_i} rdfc:instantiates {runner_id} .
                                    :env_{env_i} rdfc:processor ?step .
                                    }}
                                    WHERE {{
                                    ?processor dct:requires {runner_id} .
                                    ?step :isCarriedOutBy ?processor .
                                    }}
                                """

            environment_graph = self.graph_reader.execute_query(pipeline_query)
            graph_list.append(environment_graph)

        output_graph = merge_graphs(graph_list)
        return output_graph

    def describe_pipeline_required(self) -> Graph:
        """
        Looks up any PipelineConfigs which are directly attached to the rdfc:Orchestrator.
        """

        config_query = f"""        
        SELECT ?config_id ?literal
        WHERE {{
        ?config_id ?configForComponent rdfc:Orchestrator . 
        ?config_id a tc:PipelineConfig . 
        ?config_id ?config_relation ?literal . 
        FILTER(?literal IN ('isRequired', 'isAssigned')) 
        }}
        """

        config_list = self.graph_reader.execute_query(config_query)[
            "config_id"
        ].to_list()

        graph_list = []
        for config_id in config_list:
            config_dict = deepcopy(self.graph_dict.get_branch(config_id)[":config"])
            config_dict["@id"] = self.pipeline_id
            config_dict["@context"] = dict(self.graph_reader.prefix_store.prefixes)
            graph_list += [GraphDict(config_dict).to_graph()]

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
            ?step :isCarriedOutBy ?component .
            rdfc:Orchestrator :instantiates ?component .
            rdfc:Orchestrator :executes ?step .
            }}
        """

        output_graph = self.graph_reader.execute_query(processors_query)
        output_graph_reader = GraphReader(output_graph)
        triples_table = output_graph_reader.get_triples()

        # Adding the config for each pipeline step
        for index, row in triples_table.iterrows():
            step_id = row["sub"]
            processor_id = row["obj"]
            config_df = self.graph_reader.get_triples(
                sub=processor_id, pred=":isAssigned"
            )
            config_df = pd.concat(
                [
                    config_df,
                    self.graph_reader.get_triples(sub=processor_id, pred=":isRequired"),
                ]
            )

            if len(config_df) > 0:
                config_id = config_df["obj"].to_list()[0]
                config_dict = self.graph_dict.get_branch(config_id)[":config"]
                config_dict["@id"] = step_id
                config_dict["@context"] = dict(self.graph_reader.prefix_store.prefixes)
                config_graph = GraphDict(config_dict).to_graph()

                output_graph = merge_graphs([output_graph, config_graph])

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
             ?step :isCarriedOutBy ?component .
             OPTIONAL {{?step p-plan:isPrecededBy ?prev_step .
             rdfc:Orchestrator :executes ?prev_step .}}
             rdfc:Orchestrator :instantiates ?component .
             rdfc:Orchestrator :executes ?step .
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
        df_processors = self.tab_processors()

        for processor_id in df_processors["component"].to_list():

            df_output = pd.concat(
                [
                    _lookup_channel_predicates(constraint_id)
                    for constraint_id in self.graph_reader.get_triples(
                        sub=processor_id, pred=":constraint"
                    )["obj"].to_list()
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
