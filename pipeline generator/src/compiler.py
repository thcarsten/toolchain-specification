###############################################################
# CONFIG
###############################################################


import yaml
from graph_reader import GraphReader
from pipeline import Pipeline
from rdflib import Graph
import pandas as pd


# Ensures that multilines are printed in pretty multiline format
def _pretty_multiline(dumper, data):
    if "\n" in data:  # detect multiline
        data = data.replace("\r\n", "\n")
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


yaml.add_representer(str, _pretty_multiline)


###############################################################
# CLASS DEFINITION
###############################################################


class LdioCompiler:
    """
    Class to generate config file for LDIO based on information contained in a Pipeline instance
    Properties:
            - dict_config: Config as native Python dict
    Methods:
            - compile_config(pipeline): Compiles a config dict in self.dict_config based on information contained in pipeline
            - save_config(filename): Saves config to file
    """

    def __init__(self):
        self.dict_config = {
            "name": "",
            "description": "",
            "input": {"adapter": {}},
            "transformers": [],
            "outputs": [],
        }
        self.config_filename = (
            "ldio_config.yml"  # Default filename of config, can be overwritten
        )

    ###########################
    # COMPILE CONFIG
    ###########################

    def save_config(self, filename: str | None = None):
        """dumping the config to yaml file"""

        # Provide default if no filename was provided
        filename = filename or self.config_filename
        # Overwrite filename property if nonstandard filename is used
        self.config_filename = filename

        with open(filename, "w") as outfile:
            yaml.dump(
                self.dict_config, outfile, default_flow_style=False, sort_keys=False
            )

    def save_dockerfile(self, filename="docker-compose.yml"):
        """dumping the dockerfile to file"""
        with open(filename, "w") as outfile:
            outfile.write(self.dockerfile)

    def compile_config(self, pipeline: Pipeline, clean=True):
        self.dict_config["name"] = pipeline.name
        self.dict_config["description"] = pipeline.description

        for index, row in pipeline.steps.iterrows():
            processor_id = row["processor"]
            config_id = row["config"]
            dict_processor = pipeline.processors[processor_id]
            dict_config = pipeline.configs[config_id].get("tc:embedded")
            processor_type = dict_processor.get("ldio:type")
            processor_name = dict_processor.get("rdfs:label")

            dict_processor_clean = {"name": processor_name, "config": dict_config}
            if processor_type == "Input":
                self.dict_config["input"].update(dict_processor_clean)
            elif processor_type == "Adapter":
                self.dict_config["input"]["adapter"].update(dict_processor_clean)
            elif processor_type == "Transformer":
                self.dict_config["transformers"].append(dict_processor_clean)
            elif processor_type == "Output":
                self.dict_config["outputs"].append(dict_processor_clean)

        # Cleaning up the config
        if clean:
            self.dict_config = self._remove_falsy(self.dict_config)
            self.dict_config = self._remove_startswith(self.dict_config, ":")

    def _remove_falsy(self, d, falsy_values=None):
        """
        Removes key-value pairs in a dict if the value in the dict is None, [] or {}
        TODO:
        # Makes sure d is an independent entity of obj and not just a reference (to prevent deletions in d to affect obj)
        # d = copy.deepcopy(obj).
        """

        if falsy_values is None:
            falsy_values = [None, [], {}]

        if not isinstance(d, dict):
            return d

        keys_to_delete = []

        for key, value in d.items():
            if isinstance(value, dict):
                self._remove_falsy(value, falsy_values)
                if value in falsy_values:
                    keys_to_delete.append(key)

            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        self._remove_falsy(item, falsy_values)

                d[key] = [item for item in value if item not in falsy_values]

                if d[key] in falsy_values:
                    keys_to_delete.append(key)

            elif value in falsy_values:
                keys_to_delete.append(key)

        for key in keys_to_delete:
            del d[key]

        return d

    def _remove_startswith(self, obj, startswith):
        """
        Recursively replace prefix with a replacement in dict keys and string values.
        TODO: Does not consider whether prefix stands at beginning or not, so may lead to bugs
        """
        if isinstance(obj, dict):
            new_dict = {}
            for key, value in obj.items():
                # Replace in key if it's a string
                new_key = (
                    key.removeprefix(startswith)
                    if isinstance(key, str) and key.startswith(startswith)
                    else key
                )
                # Recursively process value
                new_dict[new_key] = self._remove_startswith(value, startswith)
            return new_dict

        elif isinstance(obj, list):
            return [self._remove_startswith(item, startswith) for item in obj]

        elif isinstance(obj, str):
            return obj.removeprefix(startswith)

        else:
            return obj

    ###########################
    # COMPILE DOCKERFILE
    ###########################

    def compile_dockerfile(self, pipeline):
        dockerstring = (
            pipeline.runners.get("ldio:LinkedDataInteractionsOrchestrator")
            .get("osw:hasInstallationInstructions")
            .get("tc:dockerString")
        )

        filename = self.config_filename

        # Auto-remove the filepath
        if "/" in filename:
            filename = filename.split("/")[-1]
        elif "\\" in filename:
            filename = filename.split("\\")[-1]
        if " " in filename:
            raise SyntaxError("filename may not contain whitespace")

        # ldio_config.yml is the placeholder filename in the dockerstring
        dockerstring = dockerstring.replace("ldio_config.yml", filename)

        self.dockerfile = dockerstring


class RdfcCompiler:
    """
    Class to generate a config file for RDF Connect based on information contained in a Pipeline instance
    Properties:
            - graph_config: Config as native Python dict
    Methods:
            - compile_config(pipeline): Compiles a config dict in self.graph_config based on information contained in pipeline
            - save_config(filename): Saves config to file
            - _describe_pipeline(pipeline): Returns a graph containing triples describing the pipeline in RDF Connect language
            - _describe_processors(pipeline): Returns a graph containing triples describing processors in RDF Connect language
            - _describe_channels(pipeline): Returns a graph containing triples describing channels in RDF Connect language

    """

    def __init__(self):
        self.graph: Graph()
        # Initializing a new graph reader, because all describe functions require a graph reader
        self.rdfc_reader = GraphReader()

    def compile_config(self, rdfc_pipeline) -> str:
        self._load_pipeline(rdfc_pipeline)
        rdfc_graph = self.rdfc_reader.merge_graphs(
            [
                self._describe_pipeline(rdfc_pipeline),
                self._describe_processors(rdfc_pipeline),
                self._describe_channels(rdfc_pipeline),
            ]
        )

        return rdfc_graph.serialize(format="turtle", base=self.rdfc_reader._basepath)

    def _load_pipeline(self, rdfc_pipeline: Pipeline):
        # Initializing a new graph reader that holds the graph of the extracted rdfc pipeline
        self.rdfc_reader.graph = rdfc_pipeline.graph
        self.rdfc_reader.prefixes = rdfc_pipeline.prefixes

    def _describe_pipeline(self, rdfc_pipeline: Pipeline) -> Graph:
        # Starting a new graph which initializes each pipeline step as a rdfc:Processor
        # TODO: This is very hacky. The query will break once there is more than 1 runner
        # TODO: owl import triples are not correct, yet

        describe_pipeline_query = f"""
                        CONSTRUCT {{
                        {rdfc_pipeline.pipeline_id} a rdfc:Pipeline .
                        {rdfc_pipeline.pipeline_id} rdfc:consistsOf :env .
                        :env rdfc:instantiates ?runner .
                        :env rdfc:processor ?processor .
                        ?runner owl:imports ?import .
                        }}
                        WHERE {{
                        ?step p-plan:isStepOfPlan {rdfc_pipeline.pipeline_id} .
                        ?step tc:toBeCarriedOutByProcessor ?processor .
                        ?processor osw:hasDependency ?runner .
                        ?runner owl:imports ?import .
                        }}
                """
        pipeline_graph = self.rdfc_reader.execute_query(
            describe_pipeline_query, simplify=False
        )
        return pipeline_graph

    def _describe_processors(self, rdfc_pipeline: Pipeline) -> Graph:
        """
        Produces triples describing the processors for the rdfc graph
        """

        steps = rdfc_pipeline.steps

        # Starting a new graph which initializes each pipeline step as a rdfc:Processor
        describe_processors_query = f"""
                                CONSTRUCT {{
                                ?step a ?processor .
                                ?step owl:imports ?import .
                                }}
                                WHERE {{
                                ?step p-plan:isStepOfPlan {rdfc_pipeline.pipeline_id} .
                                ?step tc:toBeCarriedOutByProcessor ?processor .
                                ?processor owl:imports ?import .
                                }}
                        """
        output_graph = self.rdfc_reader.execute_query(
            describe_processors_query, simplify=False
        )

        # Grabbing the relevant references for a particular step in a pipeline
        for current_step_id in steps["step"]:
            processor_id = list(
                steps.loc[steps["step"] == current_step_id, "processor"]
            )[0]
            config_id = list(steps.loc[steps["step"] == current_step_id, "config"])[0]

            # Fetching the subgraph that only contains the config
            config_graph = self.rdfc_reader.extract_subgraph(config_id, as_dict=False)
            # Renaming the empty blank node to the pipeline step (in this RDF Connect context, the pipeline step is the instantiation of the processor)
            config_graph = self.rdfc_reader.rename_node_in_graph(
                match_pattern="?source tc:embedded ?target",
                new_node_name=current_step_id,
                graph=config_graph,
                simplify=False,
            )
            # trimming the graph to contain only the embedded config
            config_graph = self.rdfc_reader.extract_subgraph(
                current_step_id, direction="along", graph=config_graph, as_dict=False
            )
            # Adding all triples of the config graph to the output graph with ALL configs
            output_graph = self.rdfc_reader.merge_graphs([output_graph, config_graph])

        return output_graph

    def _describe_channels(self, rdfc_pipeline: Pipeline) -> Graph:
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
        channels = rdfc_pipeline.steps
        channels = channels.reset_index(drop=True)

        # adding a channel_id per channel
        channels["channel_id"] = ":channel_" + channels.index.astype(str)

        # Initializing some new columns to be filled in progressively
        channels["input_predicate"] = None
        channels["output_predicate"] = None

        for processor_id in list(channels["processor"]):
            # looking up and filling in the rdfc channel predicates for each processor
            # TODO: This is a very hacky way, will probably break later
            df_output = pd.concat(
                [
                    self._lookup_channel_predicates(constraint_id, rdfc_pipeline)
                    for constraint_id in rdfc_pipeline.constraints.get(processor_id)
                ],
                ignore_index=True,
            )
            channels.loc[channels["processor"] == processor_id, "input_predicate"] = (
                list(df_output["input_predicate"])[0]
            )
            channels.loc[channels["processor"] == processor_id, "output_predicate"] = (
                list(df_output["output_predicate"])[0]
            )

        ######
        # Actually building the triples regarding channels
        ######

        output_graph = Graph()

        for step in list(channels["step"]):
            prev_step = list(channels.loc[channels["step"] == step, "prev_step"])[0]

            # If the current step has not previous step, a channel is not necessary
            if not prev_step:
                continue

            # Grabbing the necessary references
            channel_id = list(channels.loc[channels["step"] == step, "channel_id"])[0]
            input_predicate = list(
                channels.loc[channels["step"] == step, "input_predicate"]
            )[0]
            output_predicate = list(
                channels.loc[channels["step"] == prev_step, "output_predicate"]
            )[0]

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
            channel_graph = self.rdfc_reader.execute_query(
                channel_query, simplify=False
            )

            output_graph = self.rdfc_reader.merge_graphs([output_graph, channel_graph])

        # Binding prefixes to output graph
        self.rdfc_reader._bind_prefixes(output_graph, rdfc_pipeline.prefixes)

        return output_graph

    def _lookup_channel_predicates(self, nodeShape_id: str, rdfc_pipeline: Pipeline):
        """
        Given the nodeShape_id, looks up the predicate names related to rdfc:Reader and rdfc:Writer
        """
        # I first extract a subgraph because it allows for a simpler select query afterwards
        nodeShapeGraph = self.rdfc_reader.extract_subgraph(
            nodeShape_id, graph=rdfc_pipeline.graph, as_dict=False
        )

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
        return self.rdfc_reader.execute_query(query, nodeShapeGraph, simplify=True)
