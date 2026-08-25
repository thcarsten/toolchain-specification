from rdflib import Graph

from rdfine import GraphReader, receive_first

from ..base import Compiler


class PipelineAssembler(Compiler):
    """
    Compiler Class to assign components, steps and configs to the microservices responsible for executing the pipeline.
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
        least one step (a ``p-plan:isStepOfPlan`` edge pointing at it).
        Does not depend on the graph having been narrowed to just this
        pipeline — component scoping is done internally via
        :meth:`_lookup_relevant_components`.
        """
        pipelines = (
            graph_reader.filter(pred="rdf:type", obj="tcs:PipelineDefinition")
            .df["sub"]
            .unique()
        )
        if len(pipelines) != 1:
            return False
        return not graph_reader.filter(
            pred="p-plan:isStepOfPlan", obj=pipelines[0]
        ).df.empty

    def compile(self) -> Graph:
        self.lookup_pipeline_id()
        self.describe_docker_container()
        self.describe_step()
        return self.output_reader.graph

    def lookup_pipeline_id(self) -> None:
        """Locate the single ``tcs:PipelineDefinition`` node in the build
        graph (guaranteed to exist and be unique by :meth:`applies_to`)
        and stash it on :attr:`pipeline_id` for the other steps to use.
        """
        self.pipeline_id = receive_first(
            self.output_reader.filter(pred="rdf:type", obj="tcs:PipelineDefinition").df[
                "sub"
            ],
        )

    def describe_docker_container(self) -> None:
        """
        Adds:
            - tcs:PipelineBuild dct:hasPart tcs:DockerContainer
            - tcs:DockerContainer tcs:instantiates tcs:PipelineComponent

        If the pipeline definition already declares a container that
        instantiates a given microservice (``?container a
        tcs:DockerContainer ; tcs:instantiates {microservice}``), that
        container is reused instead of minting a new ``:container_N`` —
        this method only fills in containers that are still missing.
        """

        relevant_components = self._lookup_relevant_components()

        # Create an overview df listing all components and their reliance on other components
        df_requirements = self.output_reader.select(
            "?component ?requirement",
            f"""
            VALUES ?component {{ {" ".join(relevant_components)} }}
            ?component a tcs:PipelineComponent .
            OPTIONAL {{ ?component dct:requires ?requirement . }}
            """,
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

        # Only incremented when a new blank container is minted, and
        # checked against the graph so it never collides with a name
        # already in use — same idiom as
        # PipelineSeeder.name_blind_nodes.
        next_index = 0

        # For each docker container, add the respective statements to the graph
        for microservice_id in microservice_list:
            container_id = f":container_{next_index}"
            next_index += 1
            while self.output_reader.check_exists(container_id):
                container_id = f":container_{next_index}"
                next_index += 1

            # tcs:PipelineBuild dct:hasPart tcs:DockerContainer
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

    def _lookup_relevant_components(self) -> list[str]:
        """
        Components actually specialized by an ``InstancePipelineComponent``
        of this pipeline, plus every component transitively reachable
        from them via ``dct:requires`` — exactly the components this
        pipeline could ever need a container for. Scoping to this
        (rather than every ``tcs:PipelineComponent`` present in the
        graph) means this compiler doesn't depend on the graph having
        already been narrowed down to just this pipeline elsewhere.
        """
        used_components = (
            self.output_reader.select(
                "?component",
                f"""
                ?step p-plan:isStepOfPlan {self.pipeline_id} ;
                      prov:specializationOf ?component .
                """,
            )["component"]
            .drop_duplicates()
            .to_list()
        )

        return (
            self.output_reader.select(
                "?component",
                f"""
                VALUES ?used {{ {" ".join(used_components)} }}
                ?used dct:requires* ?component .
                """,
            )["component"]
            .drop_duplicates()
            .to_list()
        )
