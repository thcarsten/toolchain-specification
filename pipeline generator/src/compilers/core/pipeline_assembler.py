from rdflib import Graph

from rdfine import GraphReader, receive_first

from ..base import Compiler


class PipelineAssembler(Compiler):
    """
    Compiler Class to assign components, steps and configs to the microservices responsible for executing the pipeline.
    Also assigns configs to the components they belong to.
    """

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        # Intermediate state — populated in ``compile``; declared here
        # so the instance shape is explicit for static analysis and
        # post-compile inspection.
        self.pipeline_id: str = ""

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Runs once there is exactly one ``tcs:PipelineDefinition`` with at
        least one ``:hasStep`` — i.e. once ``PipelineExtractor`` has narrowed
        the graph down to a single pipeline and populated its steps.
        """
        pipelines = (
            graph_reader.filter(pred="rdf:type", obj="tcs:PipelineDefinition")
            .df["sub"]
            .unique()
        )
        if len(pipelines) != 1:
            return False
        return not graph_reader.filter(sub=pipelines[0], pred=":hasStep").df.empty

    def compile(self) -> Graph:
        self.pipeline_id = receive_first(
            self.output_reader.filter(pred="rdf:type", obj="tcs:PipelineDefinition").df[
                "sub"
            ],
        )
        self.describe_docker_container()
        self.describe_step()
        self.describe_config_assignment()
        return self.output_reader.graph

    def describe_docker_container(self) -> None:
        """
        Adds:
            - tcs:PipelineBuild dct:hasPart tcs:DockerContainer
            - tcs:DockerContainer tcs:instantiates tcs:PipelineComponent
        """

        # Create an overview df listing all components and their reliance on other components
        df_requirements = self.output_reader.select(
            "?component ?requirement",
            "?component a tcs:PipelineComponent. OPTIONAL {{?component dct:requires ?requirement .}}",
        )

        # Identifying the components which are microservices
        df_requirements["microservice"] = False
        for component in df_requirements["component"].to_list():
            df_requirements.loc[
                df_requirements["component"] == component, "microservice"
            ] = self.output_reader.ask(
                f"{component} tcs:config ?config. ?config a tcs:DockerComposeConfig .",
            )

        microservice_list = (
            df_requirements.loc[df_requirements["microservice"], "component"]
            .drop_duplicates()
            .to_list()
        )

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
                    & ~df_requirements["microservice"].astype(bool),
                    "component",
                ].tolist()

                for dep in new_deps:
                    if dep not in dependants:
                        dependants.add(dep)
                        to_process.append(dep)

            dependants_list = list(dependants)
            return dependants_list

        # For each docker container, add the respective statements to the graph
        for i, microservice_id in enumerate(microservice_list):
            # tcs:PipelineBuild dct:hasPart tcs:DockerContainer
            container_id = ":container_" + str(i)

            construct_statement = f"""
            {container_id} a tcs:DockerContainer .
            ?build_id dct:hasPart {container_id}. 
            {container_id} tcs:instantiates {microservice_id} .
            """
            new_triples = self.output_reader.construct(
                construct_statement, "?build_id a tcs:PipelineBuild"
            ).graph
            self.output_reader = self.output_reader.add(new_triples)

            # tcs:DockerContainer tcs:instantiates tcs:PipelineComponent
            dependants_list = _lookup_dependants(microservice_id)
            for dependant in dependants_list:
                new_dependant_triples = self.output_reader.construct(
                    f"{container_id} tcs:instantiates {dependant}.",
                    "?s ?p ?o .",
                ).graph
                self.output_reader = self.output_reader.add(new_dependant_triples)

    def describe_step(self) -> None:
        """
        Adds:
            - tcs:DockerContainer tcs:runs tcs:InstancePipelineComponent
        """

        step_description = """
        ?microservice a tcs:DockerContainer .
        ?microservice tcs:instantiates ?component . 
        ?step a tcs:InstancePipelineComponent .
        ?step prov:specializationOf ?component .
        """

        new_triples = self.output_reader.construct(
            "?microservice tcs:runs ?step .", step_description
        ).graph
        self.output_reader = self.output_reader.add(new_triples)

    def describe_config_assignment(self) -> None:
        """
        Adds:
            - tcs:PipelineComponent :isAssigned tcs:Config .
        """

        assignment_description = """
        ?step a tcs:InstancePipelineComponent .
        ?step prov:specializationOf ?component .
        ?step p-plan:hasInputVar ?config .
        """

        new_triples = self.output_reader.construct(
            "?component :isAssigned ?config .", assignment_description
        ).graph
        self.output_reader = self.output_reader.add(new_triples)
