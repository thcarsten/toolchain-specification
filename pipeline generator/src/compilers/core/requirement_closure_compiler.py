from rdflib import Graph

from rdfine import GraphReader

from ..compiler_abc import Compiler
from ..utils import (
    lookup_pipeline_components,
    lookup_seeded_pipeline_id,
    mint_missing_containers,
)


class RequirementClosureCompiler(Compiler):
    """Give containers to microservices that entered the build after
    :class:`PipelineAssembler` had already run.

    The assembler takes the ``dct:requires`` closure of the components
    *declared* in the pipeline, and the runner runs each compiler at most
    once. :class:`BridgeTransportCompiler` runs later and can introduce a
    component that was in nobody's closure — an auto-inserted boundary
    component whose own requirements therefore never get containers.

    ``sw:rdf-ingest-service`` is the case that surfaced it. Inserted as
    the Entry half of a bridge, it drags in the mu stack it has to be
    reached through (``sw:mu-identifier`` → ``sw:mu-dispatcher`` →
    ``sw:mu-authorization``). Without this pass the generated
    docker-compose has the ingest service and no gateway, while the
    channel's ``tcs:endpoint`` names that gateway — a compose file that
    starts and then fails on the first request.

    Ordering is left to the trigger rather than to list position, as
    everywhere else in the runner. Straight after the assembler the
    closure is complete, so :meth:`applies_to` is false and this compiler
    stays out of the way; it only becomes true once a bridge has been
    inserted. Pipelines that declare both sides of their boundaries
    therefore never run it at all.
    """

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        self.pipeline_id: str = ""

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Fires when a bridge has been inserted and some microservice
        reachable along ``dct:requires`` still has no container.

        Gating on :class:`BridgeTransportCompiler` having run (via the
        ``dct:creator`` marker the runner records) keeps this from firing
        early on a pipeline that happens to start with an incomplete
        closure — the assembler owns that first pass, and doing it here
        instead would consume this compiler's single run before the
        bridge steps it exists for are in the graph.
        """
        bridged = not graph_reader.filter(
            pred="dct:creator", obj="tcs:BridgeTransportCompiler"
        ).df.empty
        if not bridged:
            return False

        try:
            pipeline_id = lookup_seeded_pipeline_id(graph_reader)
        except (IndexError, KeyError, ValueError):
            return False

        components = lookup_pipeline_components(graph_reader, pipeline_id)
        if not components:
            return False

        return any(
            graph_reader.ask(
                f"{component} tcs:config ?config . "
                f"?config a tcs:DockerComposeConfig ."
            )
            and not graph_reader.ask(
                f"?container a tcs:DockerContainer ; tcs:instantiates {component} ."
            )
            for component in components
        )

    def compile(self) -> Graph:
        self.pipeline_id = lookup_seeded_pipeline_id(self.output_reader)
        self.close_requirement_gaps()
        return self.output_reader.graph

    def close_requirement_gaps(self) -> None:
        """Mint the missing containers.

        Only containers: the components this adds are infrastructure that
        no step specializes, and the bridge step that pulled them in was
        already placed on its container by
        :class:`BridgeTransportCompiler`. So there is no step-to-container
        mapping left to redo, and :meth:`PipelineAssembler.describe_step`
        does not need a second pass.
        """
        self.output_reader = mint_missing_containers(
            self.output_reader, self.pipeline_id
        )
