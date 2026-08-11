"""Attach a SHACL validation report to the ``tcs:PipelineBuild``.

Implements pillar 1 of the strategy in ``test suite/README.md`` (plain,
``sh:target``-based SHACL validation): ``normalize_config_shapes`` gives
every configShape a ``sh:target``, ``validate_normal_shapes`` runs
pySHACL over the result and attaches the report to the build.

Pillar 2 (turning passthroughShapes into inputShapes/outputShapes,
gathering per-``tcs:Channel`` shapes, shape-matching) is not yet
implemented here.
"""

from rdflib import Graph
from rdfine import GraphReader

from ..base import Compiler
from ..utils import attach_file


class ValidationReportCompiler(Compiler):
    """
    Runs once :class:`PipelineEnricher` has actually finished (channels
    synthesized, configs seeded), guaranteed by :meth:`applies_to`
    triggering explicitly on ``<build> dct:creator tcs:PipelineEnricher``
    - the provenance triple ``PipelineGenerator`` writes immediately
    after any compiler runs - rather than a coarser signal both
    compilers would happen to share. First use of this pattern in the
    codebase; elsewhere two compilers' relative order has so far either
    not mattered or fallen out of graph state each needs anyway.

    Side effect worth knowing: fixpoint eligibility is snapshotted once
    per iteration, and ``PipelineEnricher``'s ``dct:creator`` triple
    only exists *after* it finishes - so this compiler can't be
    eligible in the same iteration Enricher runs in, only the next one.
    ``PipelineAssembler`` has no such dependency and is already eligible
    in Enricher's own iteration, so it runs first. Verified empirically:
    compile order is ``..., PipelineEnricher, PipelineAssembler,
    ValidationReportCompiler, ...`` - not strictly between the two as
    an earlier version of this docstring claimed. Harmless for
    correctness (``PipelineAssembler`` only adds container/``tcs:runs``
    triples, nothing this compiler inspects), but worth a second look if
    that ordering ever needs to be tightened back up.

    Known limitation (resolved): :class:`PipelineExtractor` used to
    shrink the graph down to only what is reachable from the one
    pipeline being compiled, dropping the generic application-profile
    shapes in ``catalog-application-profile-shapes.ttl`` (e.g.
    ``tcs:RdfcProcessorShape``, ``tcs:PipelineComponentShape``) since
    they float independently via ``sh:targetClass``/``sh:target`` with
    no graph edge from the pipeline to reach them. Fixed by having
    ``PipelineExtractor`` separately collect the forward-reachable
    subgraph of every ``sh:NodeShape`` in the source graph and re-add
    it after the pipeline traversal. Verified empirically:
    ``tcs:RdfcProcessorShape`` and ``tcs:PipelineComponentShape`` both
    now survive into the build graph alongside component-attached
    configShapes, so ``validate_normal_shapes`` sees both kinds.
    """

    #: Override on a subclass to change where the report is attached.
    filename = "validation-report.ttl"
    filepath = "validation"

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        # "NA" until validate_normal_shapes() has run, so auditing
        # gen.compilers[ValidationReportCompiler].conforms can tell
        # "not yet validated" apart from an actual pass/fail.
        self.conforms: str | bool = "NA"

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Triggered once ``PipelineEnricher`` has actually run —
        checked via the ``<build> dct:creator tcs:PipelineEnricher``
        provenance triple ``PipelineGenerator`` writes right after any
        compiler finishes, rather than a coarser signal both compilers
        would happen to share.
        """
        return not graph_reader.filter(
            pred="dct:creator", obj="tcs:PipelineEnricher"
        ).df.empty

    def compile(self) -> Graph:
        self.normalize_config_shapes()
        self.validate_normal_shapes()
        return self.output_reader.graph

    def normalize_config_shapes(self) -> None:
        """Give every configShape a ``sh:target`` so a normal SHACL
        validator can evaluate it.

        For each ``tcs:PipelineComponent`` with a ``dcat:qualifiedRelation``
        / ``dcat:hadRole tcs:configShape`` / ``dct:relation`` attachment,
        adds a SHACL-AF ``sh:SPARQLTarget`` selecting ``?this`` = the
        ``tcs:embedded`` config body of any step that specializes that
        component - exactly what the shape's own ``sh:property``
        constraints are already written to assume (see the
        ``configShape`` example in ``test suite/README.md``).

        Shapes that already carry a ``sh:target`` (e.g. a re-run, or an
        author-supplied one) are left untouched.
        """
        pairs = self.output_reader.select(
            "?component ?shape",
            """
            ?component dcat:qualifiedRelation ?rel .
            ?rel dcat:hadRole tcs:configShape ;
                 dct:relation ?shape .
            """,
        )

        next_index = 0
        for _, row in pairs.iterrows():
            component_id = row["component"]
            shape_id = row["shape"]

            if self.output_reader.ask(f"{shape_id} sh:target ?existing ."):
                continue

            # Named IRI, not a blank node — safe to mint via CONSTRUCT
            # regardless of how many rows the WHERE clause below
            # matches (same IRI repeated is a no-op under RDF set
            # semantics), unlike a blank-node template paired with a
            # multi-row WHERE clause.
            target_id = f":configshapetarget_{next_index}"
            next_index += 1
            while self.output_reader.check_exists(target_id):
                target_id = f":configshapetarget_{next_index}"
                next_index += 1

            select_query = (
                "SELECT ?this WHERE { "
                f"?instance prov:specializationOf {component_id} ; "
                "p-plan:hasInputVar/tcs:embedded ?this . }"
            )
            new_triples = self.output_reader.construct(
                f"""
                {shape_id} sh:target {target_id} .
                {target_id} a sh:SPARQLTarget ;
                    sh:prefixes tcs:prefixes ;
                    sh:select "{select_query}" .
                """,
                f"{shape_id} a sh:NodeShape .",
            ).graph
            self.output_reader = self.output_reader.add(new_triples)

    def validate_normal_shapes(self) -> None:
        """Run pySHACL over every shape with a ``sh:target`` (the
        newly-normalized configShapes, plus whatever else in the
        current build graph already carries one) and attach the
        resulting report to the build as a file.

        Sets :attr:`conforms` to the report's own ``sh:conforms``
        boolean so ``gen.compilers[ValidationReportCompiler].conforms``
        can be audited directly after a compile, without re-parsing the
        attached report.
        """
        report = self.output_reader.validate(advanced=True, inference="rdfs")
        self.conforms = report.ask("?r sh:conforms true")

        self.output_reader = attach_file(
            self.output_reader,
            filename=self.filename,
            filepath=self.filepath,
            content=report.serialize("ttl"),
        )
