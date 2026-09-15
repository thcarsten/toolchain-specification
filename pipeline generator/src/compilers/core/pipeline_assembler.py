from rdflib import Graph

from rdfine import GraphReader

from ..compiler_abc import Compiler
from ..utils import lookup_seeded_pipeline_id, mint_missing_containers


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
        """Runs once ``PipelineSeeder`` has seeded a ``tcs:PipelineBuild``
        for a pipeline definition with at least one step (a
        ``p-plan:isStepOfPlan`` edge pointing at it).

        Keyed off the seeded build's ``prov:hadPlan``, cross-checked
        against the original ``tcs:CompilationRequest``'s
        ``tcs:targetPipeline`` (same pattern as
        :class:`compilers.core.graph_reducer.GraphReducer`) — not a
        graph-wide scan for "the" ``tcs:PipelineDefinition``, since the
        shared production catalog intentionally bundles more than one
        pipeline definition so any one can be selected by id. The
        cross-check means a build seeded against the wrong plan (a
        ``PipelineSeeder`` bug) fails closed here instead of silently
        assembling the wrong pipeline.
        """
        pipelines = graph_reader.select(
            "?pipeline",
            """
            ?build a tcs:PipelineBuild ; prov:hadPlan ?pipeline .
            ?request a tcs:CompilationRequest ; tcs:targetPipeline ?pipeline .
            """,
        )["pipeline"].unique()
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
        """Stash the seeded plan on :attr:`pipeline_id`.

        Cross-checked against the original ``tcs:CompilationRequest``'s
        ``tcs:targetPipeline`` — same pattern as :class:`GraphReducer`,
        not a graph-wide scan for "the" ``tcs:PipelineDefinition``, which
        breaks once the catalog bundles more than one (as the shared
        production config intentionally does).
        """
        self.pipeline_id = lookup_seeded_pipeline_id(self.output_reader)

    def describe_docker_container(self) -> None:
        """
        Adds:
            - tcs:PipelineBuild dct:hasPart tcs:DockerContainer
            - tcs:DockerContainer tcs:instantiates tcs:PipelineComponent

        Mints a ``:container_N`` for every microservice of the pipeline
        that the build does not already carry one for.

        Note this does *not* honour a container hand-declared in a
        pipeline definition, despite what this docstring claimed before:
        such a container has no ``dct:hasPart`` edge from the build, and
        treating it as satisfying the microservice would leave the build
        with no container for it at all. Making declared containers
        first-class needs a way to attach them to the seeded build.

        The work itself lives in :func:`mint_missing_containers`, shared
        with :class:`RequirementClosureCompiler`, which runs the same
        pass again after :class:`BridgeTransportCompiler` has introduced
        a component whose ``dct:requires`` chain was in nobody's closure
        at this point. Sharing it is why the helper is idempotent; on the
        graph this compiler normally sees there is nothing to skip.
        """
        self.output_reader = mint_missing_containers(
            self.output_reader, self.pipeline_id
        )

    def describe_step(self) -> None:
        """
        Adds:
            - tcs:DockerContainer tcs:runs tcs:InstancePipelineComponent
        """

        step_description = f"""
        ?microservice a tcs:DockerContainer .
        ?microservice tcs:instantiates ?component .
        ?step a tcs:InstancePipelineComponent ;
              p-plan:isStepOfPlan {self.pipeline_id} ;
              prov:specializationOf ?component .
        """

        new_triples = self.output_reader.construct(
            "?microservice tcs:runs ?step .", step_description
        ).graph
        self.output_reader = self.output_reader.add(new_triples)
