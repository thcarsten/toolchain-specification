from rdf_extract import Compiler, DataTree, GraphReader
import pandas as pd
import yaml
import textwrap


class PipelineExtractor(Compiler):
    """
    Compiler Class to extract all relevant data for one single pipeline.
    """

    def __init__(self, pipeline_id: str, graph_reader: GraphReader) -> None:
        super().__init__()  # Inheriting the init of the parent class
        self.pipeline_id = pipeline_id
        self.graph_reader = graph_reader
        self.output_schema = {
            "type": "object",
            "properties": {
                "components": {
                    "type": "object",
                    "patternProperties": {
                        "^.*$": {
                            "type": "object",
                        }
                    },
                    "additionalProperties": False,
                },
                "configs": {
                    "type": "object",
                    "patternProperties": {
                        "^.*$": {
                            "type": "object",
                            "required": ["pred", "config"],
                        }
                    },
                    "additionalProperties": False,
                },
                "steps": {
                    "type": "object",
                    "patternProperties": {
                        "^.*$": {
                            "type": "object",
                            "required": ["component", "previous_step"],
                        }
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["components", "configs", "steps"],
        }

    def generate_output(self) -> None:

        # Trimming the graph to only containing information of the specific pipeline
        pipeline_graph = self.graph_reader.extract_subgraph(
            self.pipeline_id, direction="along", against="p-plan:isStepOfPlan"
        )
        self.graph_reader = GraphReader(pipeline_graph)

        # Getting general info on the pipeline (metadata)
        dict_pipeline = self.graph_reader.to_dict(self.pipeline_id)

        # Adding pipeline information via fetch functions
        self.steps = self.fetch_steps()
        self.components = self.fetch_components()
        self.configs = self.fetch_configs()
        self.constraints = self.fetch_constraints()

        # Summarizing the resulting pipeline info as one big data tree
        output_dict = dict_pipeline.copy()

        output_dict.update(
            {"steps": {}, "components": {}, "configs": {}, "constraints": {}}
        )

        # Collecting the steps
        for index, row in self.steps.iterrows():
            step = row["step"]
            output_dict["steps"][step] = {"component": {"@id": row["processor"]}}
            prev_step = row["prev_step"]
            if prev_step:
                output_dict["steps"][step].update({"previous_step": {"@id": prev_step}})

        # Collecting the components
        for component in self.components:
            component_dict = self.components[component].to_dict()
            # ssdel component_dict["@id"]
            output_dict["components"][component] = component_dict

        # Collecting the configs:
        for config in self.configs:
            config_dict = self.configs[config].to_dict()
            output_dict["configs"][config] = config_dict

        # Collecting the constraints:
        output_dict["constraints"] = self.constraints.copy()

        self.output = DataTree(output_dict)

    def fetch_steps(self) -> pd.DataFrame:
        """
        TODO: Add function description
        """

        query_to_fetch_steps = f"""        
                SELECT ?step ?prev_step ?processor
                WHERE {{
                    ?step p-plan:isStepOfPlan {self.pipeline_id} .
                    OPTIONAL {{?step p-plan:isPrecededBy ?prev_step .}} 
                    OPTIONAL {{?step tc:toBeCarriedOutByProcessor ?processor .}}
                }}
        """
        df_pipeline = self.graph_reader.execute_query(query_to_fetch_steps)

        return df_pipeline

    def fetch_components(self) -> dict:
        """
        Grabs processors used in the pipeline as Python dicts
        """
        list_components = list(
            self.graph_reader.get_triples(pred="rdf:type", obj="tc:PipelineComponent")[
                "sub"
            ]
        )

        # Grabbing the information on each component
        dict_components = {}
        for component in list_components:
            component_graph = self.graph_reader.extract_subgraph(
                component,
                prune=["osw:hasDependency", "osw:hasUseLimitations"],
                exclude="tc:hasDefaultConfig",
            )
            component_dict = GraphReader(component_graph).to_dict(component)
            component_tree = DataTree(component_dict)
            # Doing some cleaning
            # component_tree.collapse_ids()

            dict_components[component] = component_tree

        return dict_components

    def fetch_configs(self) -> dict:
        """
        Grabs references to configs used in the pipeline as Python dicts
        """

        input_config_query = f"""
            SELECT ?processor ?pred ?config
            WHERE {{
                    ?step p-plan:isStepOfPlan {self.pipeline_id} .
                    ?step tc:toBeCarriedOutByProcessor ?processor .
                    ?step p-plan:hasInputVar ?config .
                    ?step ?pred ?config .
            }}
        """

        default_config_query = f"""
            SELECT ?processor ?pred ?config
            WHERE {{
                    ?processor tc:hasDefaultConfig ?config .
                    ?processor ?pred ?config .
            }}
        """

        df_input_config = self.graph_reader.execute_query(input_config_query)
        df_default_config = self.graph_reader.execute_query(default_config_query)

        df_config = pd.concat(
            [df_input_config, df_default_config], axis=0, ignore_index=True
        )

        # For each config, I catch the DataTree
        dict_configs = {}
        for index, row in df_config.iterrows():
            config_dict = {}
            # config_dict["id"] = row["config"]
            config_dict["pred"] = row["pred"]
            config_dict["processor"] = row["processor"]
            config_dict.update(self.graph_reader.to_dict(row["config"]))
            config_tree = DataTree(config_dict)
            config_tree = self.parse_config(config_tree)
            # config_tree.collapse_ids()
            processor = config_tree["processor"]
            del config_tree["processor"]
            dict_configs[processor] = config_tree

        return dict_configs

    def fetch_constraints(self) -> dict:
        df_constraints = self.graph_reader.get_triples(pred="osw:hasUseLimitations")
        dict_constraints = {}

        for index, row in df_constraints.iterrows():
            component_id = row["sub"]
            constraint_id = row["obj"]

            # Here I trim the output to ensure that no information beyond @id is fetched for references to other nodes
            subgraph_constraint = self.graph_reader.extract_subgraph(
                constraint_id,
                exclude=["osw:hasDependency"],
                prune=["sh:targetClass"],
            )
            dict_constraint = GraphReader(subgraph_constraint).to_dict(constraint_id)
            del dict_constraint["@context"]
            if component_id not in dict_constraints:
                dict_constraints[component_id] = {}
            dict_constraints[component_id][constraint_id] = dict_constraint

        return dict_constraints

    def parse_config(self, config_tree: DataTree) -> DataTree:
        """
        If a data tree is initialized with a dictionary that holds a tc:Config,
        this function can parse the config to a predefined structure.
        """

        # TODO: Should not break if expanded key is used
        if "tc:embedded" in config_tree.dict_data:
            config_tree.rename_key("tc:embedded", "config")
        elif "tc:literal" in config_tree.dict_data:
            literal_value = config_tree["tc:literal"]
            del config_tree["tc:literal"]

            # Clean up trailing double_quotes
            literal_value = literal_value.removeprefix('""')
            literal_value = literal_value.removesuffix('""')

            # Removing '\\r' at the end of a line
            lines = literal_value.splitlines()
            lines = [line.removesuffix("\\r") for line in lines]
            literal_value = "\n".join(lines)
            literal_value = textwrap.dedent(literal_value).strip()

            config_tree["config"] = yaml.load(literal_value, Loader=yaml.FullLoader)
        else:
            raise LookupError(
                "Neither predicates 'tc:embedded' nor 'tc:literal' found in data."
            )

        return config_tree
