from rdflib import Graph

from rdfine import GraphReader, receive_first

from ..base import Compiler


class GraphReducer(Compiler):
    """Narrow the build graph down to just what belongs to this pipeline.

    Preserves three subgraphs on top of the pipeline traversal itself:
    every ``sh:NodeShape``'s forward-reachable subgraph, and the
    catalog's own ``dcat:resource`` membership assertion for every
    component the pipeline's steps actually specialize.

    Note (temporary trigger, revisit when BridgeTransportCompiler
    lands): triggers on ``PipelineAssembler`` having recorded itself as
    ``dct:creator`` on the build — a coarse "assembly-and-later work
    has already happened" gate. When later slices add
    :class:`BridgeTransportCompiler` and the per-boundary config
    compilers, this trigger must be tightened so narrowing only fires
    after those have finished; otherwise narrowing runs too early and
    removes catalog components Bridge still needs to reach.
    """

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        build_and_pipeline = self.output_reader.select(
            "?build ?pipeline",
            "?build a tcs:PipelineBuild ; prov:hadPlan ?pipeline .",
        )
        self.build_id: str = receive_first(build_and_pipeline["build"])
        self.pipeline_id: str = receive_first(build_and_pipeline["pipeline"])

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Fires once ``PipelineAssembler`` has recorded provenance on
        the build. See the class docstring for the temporary-trigger
        note.
        """
        return not graph_reader.filter(
            pred="dct:creator", obj="tcs:PipelineAssembler"
        ).df.empty

    def compile(self) -> Graph:
        self.reduce_to_pipeline()
        return self.output_reader.graph

    def reduce_to_pipeline(self) -> None:
        # SHACL shapes float independently of the pipeline (reached by no
        # graph edge from it), so the traversal below would otherwise drop
        # them; collect each shape's own subgraph up front and re-add it
        # after narrowing.
        shape_ids = (
            self.output_reader.filter(pred="rdf:type", obj="sh:NodeShape")
            .df["sub"]
            .to_list()
        )
        # ``bind_namespaces="none"`` avoids rdflib's default core bindings
        # (e.g. ``dcterms``), which collide with the catalog's own ``dct``
        # prefix for the same URI and can silently evict it once merged.
        shape_graph = Graph(bind_namespaces="none")
        for shape_id in shape_ids:
            shape_graph += self.output_reader.traverse(shape_id).graph

        # tcs:Catalog nodes float independently of the pipeline the same
        # way shapes do, so a catalog's dcat:resource membership
        # assertion for a component this pipeline's steps actually
        # specialize would otherwise be dropped too —
        # SpecializedComponentIsCatalogedShape depends on it surviving to
        # tell a real catalog member from a dangling/mistyped
        # prov:specializationOf target. Scoped to just the components
        # this pipeline's own steps specialize, not the catalog's full
        # resource list: pulling in every listed component (most of
        # which this pipeline never uses) would instead make each one a
        # fresh validation target for catalog-wide shapes (CatalogShape,
        # PipelineComponentShape's deployability check) they were never
        # meant to be checked against in a single pipeline's build.
        used_components = self.output_reader.select(
            "?component",
            f"""
            ?step p-plan:isStepOfPlan {self.pipeline_id} ;
                  prov:specializationOf ?component .
            """,
        )["component"].to_list()

        catalog_graph = Graph(bind_namespaces="none")
        if used_components:
            catalog_graph += self.output_reader.filter(
                pred="rdf:type", obj="tcs:Catalog"
            ).graph
            catalog_graph += self.output_reader.filter(
                pred="dcat:resource", obj=used_components
            ).graph

        # Traversal starts from the build so its own attached triples
        # (``dct:hasPart`` containers, ``tcs:compiledFile`` files,
        # ``dct:creator`` provenance) survive alongside the pipeline
        # reached via ``prov:hadPlan``. ``against="p-plan:isStepOfPlan"``
        # then pulls in every step of this pipeline and its downstream
        # configuration.
        self.output_reader = self.output_reader.traverse(
            self.build_id, against="p-plan:isStepOfPlan"
        )
        self.output_reader = self.output_reader.add(shape_graph)
        self.output_reader = self.output_reader.add(catalog_graph)
