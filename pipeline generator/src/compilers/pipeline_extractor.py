from rdfine import (
    GraphDict,
    GraphReader,
    GraphTable,
    merge_graphs,
)
import pandas as pd
import textwrap
from rdflib import URIRef, Graph
import yaml


class PipelineExtractor:
    """
    Compiler Class to extract all relevant data for one single pipeline.
    It does NOT yet perform any form of reasoning,
    its sole responsibility is grabbing the data and returning it in line with the internal model.
    """

    def __init__(self, pipeline_id: str, graph: Graph) -> None:
        self.pipeline_id = pipeline_id
        self.graph_reader = GraphReader(graph)
        # Ensuring that the graph reader contains enriched information on the pipeline in question only
        pipeline_graph = self.graph_reader.extract_subgraph(
            self.pipeline_id,
            direction="along",
            against=["p-plan:isStepOfPlan", "p-plan:isVariableOfPlan"],
        )
        self.graph_reader = GraphReader(pipeline_graph)

    def compile(self) -> GraphDict:
        # Generating the df's for easy retrieval of reference id's
        self.table_entities = self.list_entities()
        # renames entities in self.table_entities and self.graph_reader.graph
        self.rename_entities()
        constraints_graph = self.describe_constraints()
        self.graph_reader.add_triples(constraints_graph)

        # Now I drop the indirect relation via dcat:qualifiedRelationship
        temp_table = GraphTable(self.graph_reader.graph)
        temp_table.df = temp_table.subset(
            {"pred": "dcat:qualifiedRelation"}, action="drop"
        )
        self.graph_reader = GraphReader(temp_table.to_graph())

        self.graph_reader.restore_blank_nodes()
        # Building the graph dict which serves as output
        output_graph = self.table_entities.to_graph()
        self.graph_dict = GraphDict(output_graph, self.pipeline_id)
        self.graph_dict.dict_data = self.describe_entities()
        self.graph_dict.provide_prefixes()
        return self.graph_dict

    def list_entities(self) -> GraphTable:

        graph_table = GraphTable(self.graph_reader.graph)

        # Fetching all steps
        query_step = f"""
            SELECT ?step 
            WHERE {{
                    ?step p-plan:isStepOfPlan {self.pipeline_id} .
                    
            }}"""
        list_step = list(self.graph_reader.execute_query(query_step)["step"])

        # Fetching all assignments
        query_assignment = f"""
            SELECT DISTINCT ?assignment
            WHERE {{
            {{
                ?step p-plan:isStepOfPlan {self.pipeline_id} .
                ?step p-plan:hasInputVar ?assignment .
            }}
            UNION
            {{
                ?assignment p-plan:isVariableOfPlan {self.pipeline_id} .
            }}
            UNION
            {{
                ?step p-plan:isStepOfPlan {self.pipeline_id} .
                ?step p-plan:hasInputVar ?seed_assignment .
                ?seed_assignment (tc:component/dct:requires)+ ?assignment .
            }}
            UNION
            {{
                ?plan_assignment p-plan:isVariableOfPlan {self.pipeline_id} .
                ?plan_assignment (tc:component/dct:requires)+ ?assignment .
            }}
            }}
        """

        list_assignment = list(
            self.graph_reader.execute_query(query_assignment)["assignment"]
        )

        # Fetching all components
        list_component = list(
            set(
                graph_table.subset({"sub": list_assignment, "pred": "tc:component"})[
                    "obj"
                ]
            )
        )

        # Fetching all configs
        list_config = list(
            set(
                graph_table.subset(
                    {"sub": list_assignment + list_component, "pred": ["tc:config"]}
                )["obj"]
            )
        )

        # Fetching all constraints
        # Only include those components that have a constraint

        constraints_query = f"""
                                        SELECT ?constraint 
                                        WHERE {{
                                        ?component dcat:qualifiedRelation ?relationship .
                                        ?relationship dct:relation ?constraint .
                                        }}
                                    """

        list_component_with_constraint = list(
            set(
                GraphTable(self.graph_reader.graph).subset(
                    {"sub": list_component, "pred": "dcat:qualifiedRelation"}
                )["sub"]
            )
        )

        list_constraint = []
        for component_id in list_component_with_constraint:

            constraints_query = f"""
                                        SELECT ?constraint 
                                        WHERE {{
                                        {component_id} dcat:qualifiedRelation ?relationship .
                                        ?relationship dct:relation ?constraint .
                                        }}
                                    """

            graph_constraint = self.graph_reader.execute_query(constraints_query)
            list_constraint += list(
                self.graph_reader.execute_query(constraints_query)["constraint"]
            )

        list_constraint = list(set(list_constraint))

        # Turning the results into a dataframe
        records_steps = [{"pred": ":hasStep", "obj": step} for step in list_step]
        records_assignments = [
            {"pred": ":hasAssignment", "obj": assignment}
            for assignment in list_assignment
        ]
        records_components = [
            {"pred": ":hasComponent", "obj": component} for component in list_component
        ]
        records_configs = [
            {"pred": ":hasConfig", "obj": config} for config in list_config
        ]
        records_constraints = [
            {"pred": ":hasConstraint", "obj": constraint}
            for constraint in list_constraint
        ]

        records = (
            records_steps
            + records_assignments
            + records_components
            + records_configs
            + records_constraints
        )

        df = pd.DataFrame.from_records(records)
        df["sub"] = self.pipeline_id
        df["sub_type"] = URIRef
        df["obj_type"] = URIRef

        graph_table = GraphTable(df, self.graph_reader.prefix_store)

        return graph_table

    def rename_entities(self) -> None:
        """
        Gives all entities, which have a materialized blank node id a proper id.
        Does some cleanup afterwards
        """

        bn_prefix = list(self.graph_reader._blanknode_prefix.keys())[0]

        # Filter rows safely (handle NaN)
        df = self.table_entities
        df = df[df["obj"].fillna("").str.startswith(bn_prefix)].copy()

        # Reset index properly
        df.reset_index(drop=True, inplace=True)

        # Create new names
        df["new_obj_name"] = (
            ":"
            + df["pred"].str.removeprefix(":has").str.lower()
            + "_"
            + df.index.astype(str)
        )

        # Apply renaming
        for _, row in df.iterrows():
            old_name = row["obj"]
            new_name = row["new_obj_name"]

            # Renaming in the graph reader
            self.graph_reader.rename(old_name, new_name)
            # Renaming in the entity table
            self.table_entities.df.loc[
                self.table_entities.df["sub"] == old_name, "sub"
            ] = new_name
            self.table_entities.df.loc[
                self.table_entities.df["obj"] == old_name, "obj"
            ] = new_name

    ##################
    # BUILDING AN INTERNAL MODEL FROM THE LIST OF REFERENCES
    ##################

    def describe_entities(self) -> dict:

        ####
        # Enriches the stub of graph dict by grabbing the GraphDict of each referenced entity.
        ####

        # Initializing the graph dict
        graph_dict = {"@id": self.pipeline_id}
        for pred in set(self.table_entities.df["pred"]):
            graph_dict[pred] = []

        for index, row in self.table_entities.df.iterrows():

            entity_id = row["obj"]
            entity_pred = row["pred"]

            entity_graph_dict = GraphDict(
                self.graph_reader.graph, id=entity_id, embed="@always"
            )
            entity_graph_dict.prune_ids()

            graph_dict[entity_pred] += [entity_graph_dict.dict_data]

        return graph_dict

    def describe_constraints(self) -> Graph:
        """
        Simplifies the constraint structure
        """

        graph_table = self.table_entities
        list_components = list(
            set(graph_table.subset({"pred": ":hasComponent"})["obj"])
        )
        # Only include those components that have a constraint
        list_components = list(
            set(
                GraphTable(self.graph_reader.graph).subset(
                    {"sub": list_components, "pred": "dcat:qualifiedRelation"}
                )["sub"]
            )
        )

        graph_list = []
        for component_id in list_components:

            constraints_query = f"""
                                        CONSTRUCT {{
                                        {self.pipeline_id} :hasConstraint ?constraint .
                                        {component_id} :constraint ?constraint  .
                                        ?constraint :role ?constraintRole . 
                                        
                                        }}
                                        WHERE {{
                                        {component_id} dcat:qualifiedRelation ?relationship .
                                        ?relationship dct:relation ?constraint .
                                        ?relationship dcat:hadRole ?constraintRole .

                                        }}
                                    """

            graph_constraint = self.graph_reader.execute_query(constraints_query)
            graph_list += [graph_constraint]

        output_graph = merge_graphs(graph_list)
        self.graph_reader.prefix_store.bind_to_namespace(output_graph)
        return output_graph
