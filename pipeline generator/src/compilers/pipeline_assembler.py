from rdfine import GraphReader
from rdflib import Graph
import pandas as pd


class PipelineAssembler:
    """
    Compiler Class to assign components, steps and configs to the microservices responsible for executing the pipeline.
    Also assigns configs to the components they belong to.
    """

    def __init__(self, pipeline_graph: Graph) -> None:
        self.graph_reader = GraphReader(pipeline_graph)
        self.pipeline_id = (
            self.graph_reader.filter(pred="rdf:type", obj="tcs:PipelineDefinition")
            .df["sub"]
            .to_list()[0]
        )

    def compile(self) -> Graph:
        self.describe_pipeline_build()
        self.describe_docker_container()
        self.describe_step()
        self.describe_config_assignment()
        return self.graph_reader.graph

    def describe_pipeline_build(self) -> None:
        """
        Adds:
            - XY_build a tcs:PipelineBuild
        """
        new_triples = self.graph_reader.query(
            construct=f"{self.pipeline_id}_build a tcs:PipelineBuild", where="?s ?p ?o"
        ).graph
        self.graph_reader = self.graph_reader.add(new_triples)

    def describe_docker_container(self) -> None:
        """
        Adds:
            - tcs:PipelineBuild dct:hasPart tcs:DockerContainer
            - tcs:DockerContainer tcs:instantiates tcs:PipelineComponent
        """

        # Create an overview df listing all components and their reliance on other components
        df_requirements = self.graph_reader.query(
            select="?component ?requirement",
            where="?component a tcs:PipelineComponent. OPTIONAL {{?component dct:requires ?requirement .}}",
        )

        # Identifying the components which are microservices
        df_requirements["microservice"] = None
        for component in df_requirements["component"].to_list():
            df_requirements.loc[
                df_requirements["component"] == component, "microservice"
            ] = self.graph_reader.query(
                ask=True,
                where=f"{component} tcs:config ?config. ?config a tcs:DockerComposeConfig .",
            )

        microservice_list = df_requirements.loc[
            df_requirements["microservice"], "component"
        ].to_list()

        def _lookup_dependants(microservice_id: str) -> list[str]:
            """
            Helper function that looks up which components are dependant on a specific docker container
            """
            dependants = set()  # all discovered dependants
            to_process = [microservice_id]  # queue (or stack)

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

        # For each docker container, add the respective statements to the graph
        for i in range(len(microservice_list)):
            # tcs:PipelineBuild dct:hasPart tcs:DockerContainer
            container_id = ":container_" + str(i)
            microservice_id = microservice_list[i]

            construct_statement = f"""
            {container_id} a tcs:DockerContainer .
            ?build_id dct:hasPart {container_id}. 
            {container_id} tcs:instantiates {microservice_id} .
            """
            new_triples = self.graph_reader.query(
                construct=construct_statement, where="?build_id a tcs:PipelineBuild"
            ).graph
            self.graph_reader = self.graph_reader.add(new_triples)

            # tcs:DockerContainer tcs:instantiates tcs:PipelineComponent
            dependants_list = _lookup_dependants(microservice_id)
            for dependant in dependants_list:
                new_dependant_triples = self.graph_reader.query(
                    construct=f"{container_id} tcs:instantiates {dependant}.",
                    where="?s ?p ?o .",
                ).graph
                self.graph_reader = self.graph_reader.add(new_dependant_triples)

    def describe_step(self) -> None:
        """
        Adds:
            - tcs:DockerContainer tcs:runs tcs:InstancePipelineComponent
        """

        step_description = f"""
        ?microservice a tcs:DockerContainer .
        ?microservice tcs:instantiates ?component . 
        ?step a tcs:InstancePipelineComponent .
        ?step prov:specializationOf ?component .
        """

        new_triples = self.graph_reader.query(
            construct="?microservice tcs:runs ?step .", where=step_description
        ).graph
        self.graph_reader = self.graph_reader.add(new_triples)

    def describe_config_assignment(self) -> None:
        """
        Adds:
            - tcs:PipelineComponent :isAssigned tcs:Config .
        """

        assignment_description = f"""
        ?step a tcs:InstancePipelineComponent .
        ?step prov:specializationOf ?component .
        ?step p-plan:hasInputVar ?config .
        """

        new_triples = self.graph_reader.query(
            construct="?component :isAssigned ?config .", where=assignment_description
        ).graph
        self.graph_reader = self.graph_reader.add(new_triples)
