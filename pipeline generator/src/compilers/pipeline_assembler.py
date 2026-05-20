from rdfine import GraphDict, GraphReader, GraphTable, merge_graphs, infer
from copy import deepcopy
import pandas as pd
from rdflib import Graph, Literal
import textwrap
import yaml


class PipelineAssembler:
    """
    Compiler Class to assign components, steps and configs to the microservices responsible for executing the pipeline.
    Also assigns configs to the components they belong to.
    """

    def __init__(self, pipeline: GraphDict) -> None:
        self.graph_dict = deepcopy(pipeline)  # Making sure this is a deep copy
        self.graph_reader = GraphReader(pipeline.to_graph())

    def compile(self) -> GraphDict:
        """
        Enriches the pipeline by adding a "hasPipelineBuild" to the GraphDict
        """
        self.df_components = self.tab_components()
        self.graph_dict.dict_data[":hasPipelineBuild"] = self.describe_microservices()
        microservice_list = self.describe_steps()
        self.graph_dict.set(":hasPipelineBuild.:hasMicroservice", microservice_list)
        self.assign_configs_to_components()
        self.simplify_steps()
        self.simplify_configs()
        self.simplify_components()
        self.trim_graph_dict()
        self.graph_dict.provide_prefixes()

        return self.graph_dict

    def tab_components(self) -> pd.DataFrame:
        """
        Create an overview df listing all components and their reliance on other components
        """

        # Listing the requirement of each component
        component_requirements_query = f"""
            SELECT ?component ?requirement
            WHERE {{
                    ?component a tc:PipelineComponent .
                    OPTIONAL {{
                    ?component dct:requires ?assignment .
                    ?assignment tc:component ?requirement .
                    }}
                }}"""

        df_requirements = self.graph_reader.execute_query(component_requirements_query)

        # Identifying the components which are microservices
        microservice_query = f"""
            SELECT DISTINCT ?component 
            WHERE {{
                    ?component a tc:PipelineComponent .
                    ?component tc:config ?config .
                    ?config a tc:DockerComposeConfig, tc:DefaultConfig .
                    
            }}"""

        microservice_list = list(
            self.graph_reader.execute_query(microservice_query)["component"]
        )

        # Mark the components which are microservices
        df_requirements["microservice"] = False
        for microservice_id in microservice_list:
            df_requirements.loc[
                df_requirements["component"] == microservice_id, "microservice"
            ] = True
        return df_requirements

    def describe_microservices(self) -> dict:
        """
        Creates the new branch of the dict_data which describes the microservices
        """
        pipeline_build_id = self.graph_dict.get("@id") + "_build"
        dict_data = {"@id": pipeline_build_id, ":hasMicroservice": []}
        microservice_list = self._lookup_microservices()

        for microservice_id in microservice_list:
            microservice_dict = {"@id": microservice_id, "@type": "tc:Microservice"}
            dependants_list = self._lookup_dependants(microservice_id)
            microservice_dict[":instantiates"] = [
                {"@id": dependant_id} for dependant_id in dependants_list
            ]

            dict_data[":hasMicroservice"] += [microservice_dict]

        return dict_data

    def _lookup_dependants(self, microservice_id: str) -> list[str]:
        dependants = set()  # all discovered dependants
        to_process = [microservice_id]  # queue (or stack)
        df_requirements = self.df_components

        while to_process:
            current = to_process.pop()

            new_deps = df_requirements.loc[
                (df_requirements["requirement"] == current)
                & (df_requirements["microservice"] == False),
                "component",
            ].tolist()

            for dep in new_deps:
                if dep not in dependants:
                    dependants.add(dep)
                    to_process.append(dep)

        dependants_list = list(dependants)
        return dependants_list

    def _lookup_microservices(self) -> list[str]:
        return self.df_components.loc[
            self.df_components["microservice"] == True, "component"
        ].to_list()

    def describe_steps(self) -> list[dict]:
        """
        Describe the step that each microservice executes
        """
        step_query = f"""
                                        CONSTRUCT {{
                                        ?microservice :executes ?step .
                                        }}
                                        WHERE {{
                                        ?microservice a tc:Microservice .
                                        ?microservice :instantiates ?dependant . 
                                        ?step a tc:PipelineStep .
                                        ?step p-plan:hasInputVar ?assignment .
                                        ?assignment a tc:Assignment .
                                        ?assignment tc:component ?dependant .
                                        }}
                                """
        step_graph = GraphReader(self.graph_dict.to_graph()).execute_query(step_query)

        # Now I integrate the resulting triples into the existing new branch :hasMicroservice
        microservice_graph = self.graph_dict.to_graph(":hasPipelineBuild")
        microservice_graph = merge_graphs([microservice_graph, step_graph])

        microservice_list = []
        for microservice_id in self._lookup_microservices():
            microservice_list += [
                GraphDict(microservice_graph, microservice_id).serialize(format="dict")
            ]
        return microservice_list

    def simplify_steps(self) -> None:
        """
        Adds 'isCarriedOutBy' - triples to each step
        """
        # Creating new triples
        query = f"""
            CONSTRUCT {{
            ?step :isCarriedOutBy ?component .
            }}
            WHERE {{
            ?step p-plan:hasInputVar ?assignment .
            ?assignment tc:component ?component .
            }}
            """

        new_triples = self.graph_reader.execute_query(query)

        # Updating each branch of steps with the new triples
        step_list = self.graph_reader.get_triples(
            pred="rdf:type", obj="tc:PipelineStep"
        )["sub"].to_list()

        for step_id in step_list:
            path_to_step_id = self.graph_dict.find("^:hasStep.[0-9]*.@id$", step_id)[
                "path"
            ].to_list()[0]
            self.graph_dict.add_triples(new_triples, path_to_step_id)

    def assign_configs_to_components(self) -> None:
        """
        Finds all configs and attaches them to the correct component via
        isDefault, isRequired or isAssigned
        """

        ###############
        # Sort out which configs are default, required or assigned
        ###############

        default_config_query = f"""
                                        CONSTRUCT {{
                                        ?component_id :isDefault ?default_config_id  .
                                        }}
                                        WHERE {{
                                        ?pipeline_id :hasComponent ?component_id .
                                        ?component_id tc:config ?default_config_id .
                                        }}
                                    """

        required_config_query = f"""
                                        CONSTRUCT {{
                                        ?component_id :isRequired ?required_config_id  .
                                        }}
                                        WHERE {{
                                        ?pipeline_id :hasAssignment ?assignment_id .
                                        ?assignment_id tc:component ?component_id .
                                        ?assignment_id tc:config ?required_config_id .
                                        ?anything dct:requires ?assignment_id .
                                        }}
                                    """

        assigned_config_query = f"""
                                        CONSTRUCT {{
                                        ?component_id :isAssigned ?assigned_config_id  .
                                        }}
                                        WHERE {{
                                        ?pipeline_id :hasAssignment ?assignment_id .
                                        ?assignment_id tc:component ?component_id .
                                        ?assignment_id tc:config ?assigned_config_id .
                                        ?anything p-plan:hasInputVar ?assignment_id .
                                        }}
                                    """

        another_assigned_config_query = f"""
                                        CONSTRUCT {{
                                        ?component_id :isAssigned ?assigned_config_id  .
                                        }}
                                        WHERE {{
                                        ?pipeline_id :hasAssignment ?assignment_id .
                                        ?assignment_id tc:component ?component_id .
                                        ?assignment_id tc:config ?assigned_config_id .
                                        ?assignment_id p-plan:isVariableOfPlan ?anything .
                                        }}
                                    """

        query_list = [
            default_config_query,
            required_config_query,
            assigned_config_query,
            another_assigned_config_query,
        ]
        graph_list = []

        for query in query_list:
            new_graph = self.graph_reader.execute_query(query)
            graph_list += [new_graph]

        config_table = GraphTable(merge_graphs(graph_list))

        ###############
        # Update info of each component accordingly
        ###############

        for component_id in set(config_table["sub"].to_list()):
            # finding the right reference path
            component_path = self.graph_dict.find("^:hasComponent.*@id$", component_id)[
                "path"
            ].to_list()[0]
            component_path = component_path.removesuffix(".@id")

            # merging the dict data of a component with new triples describing the component
            component_graph = self.graph_dict.to_graph(component_path)
            sub_table = GraphTable(
                config_table.subset({"sub": component_id}),
                prefix_store=config_table.prefix_store,
            )
            config_graph = sub_table.to_graph()
            merged_graph = merge_graphs([component_graph, config_graph])

            # Creating new dict data with the triples added to it
            config_dict = GraphDict(merged_graph, component_id)
            # Overwriting the existing dict data with the new one which has new triples added to it
            self.graph_dict.set(component_path, config_dict.dict_data)

    def simplify_configs(self) -> None:
        """
        Annotates each config so that it is clear to which component each config belongs to,
        and how the config relates to the component (isDefault, isRequired, isAssigned) .
        Finally parses each config.
        """

        config_query = f"""
            CONSTRUCT {{
                ?config_id :configForComponent ?component  .
                ?config_id :config_relation ?pred .
            }}
            WHERE {{
                ?component ?pred ?config_id .
                ?component rdf:type tc:PipelineComponent .
                ?config_id rdf:type tc:Config .
            }}
        """

        new_triples = GraphReader(self.graph_dict.to_graph()).execute_query(
            config_query
        )

        # I want to ensure that the config_relation points to a literal
        new_table = GraphTable(new_triples)
        mask = new_table.df["pred"] == ":config_relation"
        new_table.df.loc[mask, "obj"] = new_table.df.loc[mask, "obj"].str.removeprefix(
            ":"
        )
        new_table.df.loc[mask, "obj_type"] = Literal
        new_triples = new_table.to_graph()

        df_config = self.graph_dict.find("^:hasConfig.[0-9]+.@id$")

        # Adding new triples to each config
        # And also parse each config
        for path_to_config_id in df_config["path"].to_list():
            self.graph_dict.add_triples(new_triples, path_to_config_id)
            config_dict = self.graph_dict.get(path_to_config_id.removesuffix(".@id"))
            config_dict = self.parse_config(config_dict)
            self.graph_dict.set(path_to_config_id.removesuffix(".@id"), config_dict)

    def simplify_components(self) -> None:
        """
        Simplifies the 'dct:requires'-structure by having dct:requires point to a PipelineComponent directly.
        """

        # CREATING THE NEW TRIPLES

        requires_query = f"""
                    CONSTRUCT {{
                        ?component dct:requires ?another_component  .
                    }}
                    WHERE {{
                        ?component dct:requires ?assignment_id .
                        ?assignment_id tc:component ?another_component .
                    }}
                """

        new_triples = self.graph_reader.execute_query(requires_query)
        new_triples_table = GraphTable(new_triples)

        # REPLACING THE OLD TRIPLES WITH THE NEW ONES
        component_list = [d.get("@id") for d in self.graph_dict.get(":hasComponent")]

        # For each component, delete the old dct:requires and replace with the new one
        for component_id in component_list:
            component_path = self.graph_dict.find(
                "^:hasComponent.[0-9]+.@id$", component_id
            )["path"].to_list()[0]

            # Delete the old dct:requires path
            requires_path = component_path.removesuffix("@id") + "dct:requires"
            if "dct:requires" in self.graph_dict.get(
                component_path.removesuffix(".@id")
            ):
                self.graph_dict.set(requires_path, [])

            # I create a separate graph explicitly for this component
            component_table = deepcopy(new_triples_table)
            component_table.df = component_table.subset({"sub": component_id})
            component_triples = component_table.to_graph()

            # Add the new triples
            self.graph_dict.add_triples(component_triples, component_path)

    def trim_graph_dict(self) -> None:
        """
        As a last step, it does some cleanup by removing branches from the GraphDict,
        which are no longer needed
        """

        # After assigning configs to components, I can trim the graph_dict a bit
        del self.graph_dict[":hasAssignment"]

        # finding all branches that I do no longer require
        remove_pred_list = [
            "tc:config",
            "p-plan:hasInputVar",
            "p-plan:isVariableOfPlan",
            "p-plan:isStepOfPlan",
        ]
        remove_path_list = []
        for remove_pred in remove_pred_list:
            remove_paths = self.graph_dict.find(remove_pred)["path"].to_list()
            remove_path_list += remove_paths

        # deleting each of these branches
        for path in remove_path_list:
            self.graph_dict.set(path, [])
        self.graph_dict.drop_empty()

    def parse_config(self, input_dict: dict) -> dict:
        """
        HAS TO BECOME A COMPILER UTIL, NOT ALL CONFIG CAN BE FULLY INTEGRATED INTO GRAPHS
        If a data tree is initialized with a dictionary that holds a tc:Config,
        this function can parse the config to a predefined structure.
        """

        dict_data = input_dict.copy()

        if "tc:literal" in dict_data:
            literal_value = dict_data["tc:literal"]
            del dict_data["tc:literal"]

            # Clean up trailing double_quotes
            literal_value = literal_value.removeprefix('""')
            literal_value = literal_value.removesuffix('""')

            # Removing '\\r' at the end of a line
            lines = literal_value.splitlines()
            lines = [line.removesuffix("\\r") for line in lines]
            literal_value = "\n".join(lines)
            literal_value = textwrap.dedent(literal_value).strip()

            dict_data[":config"] = yaml.load(literal_value, Loader=yaml.FullLoader)
        elif "tc:embedded" in dict_data:
            embedded_value = dict_data["tc:embedded"]
            del dict_data["tc:embedded"]
            dict_data[":config"] = embedded_value
        else:
            raise LookupError(
                "Neither predicates 'tc:embedded' nor 'tc:literal' found in data."
            )

        return dict_data

    def merge_docker_compose(self) -> dict:
        # Fetching the ids of configs which are both DefaultConfigs and DockerComposeConfigs
        docker_compose_config_list = self.graph_reader.get_triples(
            pred="rdf:type", obj="tc:DockerComposeConfig"
        )["sub"].to_list()
        default_config_list = self.graph_reader.get_triples(
            pred="rdf:type", obj="tc:DefaultConfig"
        )["sub"].to_list()
        microservice_config_list = list(
            set(docker_compose_config_list) & set(default_config_list)
        )

        # Fetching the dict_data of each config id
        docker_compose_config = {}
        for config_id in microservice_config_list:
            config_path = self.graph_dict.find("^:hasConfig.*@id$", config_id)[
                "path"
            ].to_list()[0]
            config_path = config_path.removesuffix(".@id")
            config_dict = self.graph_dict.get(config_path)
            docker_compose_config.update(config_dict[":config"])

        return docker_compose_config
