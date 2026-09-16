from rdflib import BNode, Graph

from rdfine import GraphReader, receive_first

from ..compiler_abc import Compiler


class PipelineSeeder(Compiler):
    """Seed the ``tcs:PipelineBuild`` node from a compilation request.

    Reads the target pipeline id off a ``tcs:CompilationRequest``
    posted to the graph by :class:`CompilationRunner`, then seeds the
    ``tcs:PipelineBuild`` node so every downstream compiler (and the
    runner itself, for the ``dct:creator`` provenance triples) has a
    target to attach to. Also renames typed blank-node subjects to
    stable IRIs so later compilers can reference the resulting
    resources by name inside SPARQL query strings.

    Does not narrow the graph — the full catalog stays visible for the
    fixpoint loop so registry compilers such as
    :class:`BridgeTransportCompiler` can reach catalog components no
    step of this pipeline specializes yet. Narrowing lives in
    :class:`GraphReducer`.
    """

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Applies once a ``tcs:CompilationRequest`` has been posted."""
        return not graph_reader.filter(
            pred="rdf:type", obj="tcs:CompilationRequest"
        ).df.empty

    def compile(self) -> Graph:
        self.lookup_target_pipeline()
        self.seed_pipeline_build()
        self.name_blind_nodes()
        return self.output_reader.graph

    def lookup_target_pipeline(self) -> None:
        """Read the target pipeline id off the ``tcs:CompilationRequest``.

        Stored on :attr:`pipeline_id` so it stays inspectable after
        ``compile()`` — same convention as any other intermediate.
        """
        self.pipeline_id = receive_first(
            self.output_reader.select(
                "?pipeline",
                "?req a tcs:CompilationRequest ; tcs:targetPipeline ?pipeline .",
            )["pipeline"]
        )

    def seed_pipeline_build(self) -> None:
        """
        Adds:
            - ``<pipeline_id>_build a tcs:PipelineBuild``
            - ``<pipeline_id>_build prov:hadPlan <pipeline_id>``
        """
        if not self.output_reader.ask(f"{self.pipeline_id} a tcs:PipelineDefinition ."):
            raise NameError(
                f"{self.pipeline_id} not found in graph as a tcs:PipelineDefinition."
            )
        new_triples = self.output_reader.construct(
            f"""
            {self.pipeline_id}_build a tcs:PipelineBuild ;
                                      prov:hadPlan {self.pipeline_id} .
            """,
            f"{self.pipeline_id} a tcs:PipelineDefinition",
        ).graph
        self.output_reader = self.output_reader.add(new_triples)

    def name_blind_nodes(self) -> None:
        """
        Rename blank nodes typed with any of the prefixes we reason on
        further downstream — ``tcs:`` (configs, files, containers, ...),
        ``spdx:`` (package dependencies attached to components via
        ``dct:requires``), and ``sh:`` (anonymous ``sh:NodeShape``s —
        e.g. the trivial passthrough/input/output shapes attached to
        catalog components — so they become stable named IRIs downstream
        compilers can reference in a SPARQL query string instead of
        blank nodes, whose labels aren't safely reusable across separate
        query executions). Anything else stays a blank node.
        """

        rename_ids = (
            self.output_reader.filter(
                sub="^_:",
                pred="^rdf:type$",
                obj=["^tcs:", "^spdx:", "^sh:"],
                regex=True,
            )
            .df["sub"]
            .to_list()
        )
        # Sorted by a content-derived key, never by blank-node label:
        # rdflib mints a fresh random label on every parse, so both the
        # raw list order and ``set`` iteration order differ from run to
        # run. The index below is baked into the resulting IRI, which is
        # then baked into every emitted file that references the
        # resource, so an unstable order means the generator emits
        # different bytes for identical input.
        rename_ids = sorted(set(rename_ids), key=self._describe_node_canonically)

        # One counter per type prefix rather than a single running index
        # across all blank nodes: with ~150 renameable nodes in a loaded
        # catalog, a global counter numbered the pipeline's third channel
        # `:channel_139`, which reads like a bug every time someone opens
        # the generated pipeline.ttl.
        next_index: dict[str, int] = {}

        for rename_id in rename_ids:
            type_list = (
                self.output_reader.filter(sub=rename_id, pred="rdf:type")
                .df["obj"]
                .to_list()
            )
            type_list.sort()
            first_type = receive_first(type_list)
            prefix = (
                ":" + self.output_reader.prefix_store.drop_string(first_type).lower()
            )
            i = next_index.get(prefix, 0)
            new_name = f"{prefix}_{i}"
            # Guard against colliding with a name already in use (e.g. an
            # author-declared resource in the pipeline definition) — bump
            # the suffix until the name is free rather than silently
            # merging two distinct resources under one IRI.
            while self.output_reader.check_exists(new_name):
                i += 1
                new_name = f"{prefix}_{i}"
            next_index[prefix] = i + 1
            self.output_reader = self.output_reader.rename(rename_id, new_name)

    def _describe_node_canonically(self, node_id: str) -> tuple[str, tuple[str, ...]]:
        """Return a run-stable sort key describing ``node_id`` by content.

        Blank-node labels cannot be used to order anything that survives
        into generated output — rdflib assigns them fresh on every
        parse. This builds a key out of what the node *says* instead:
        its outgoing predicate/object pairs (descending recursively into
        nested blank nodes, so two shapes that differ only in a nested
        ``sh:property`` still sort apart) plus the edges pointing at it,
        which is what separates otherwise-identical configs attached to
        different named steps.

        Nodes that remain tied are structurally indistinguishable, so
        which of them wins a given index does not change the meaning of
        the build. Against the shipped catalog exactly one tie remains:
        three bare ``sh:NodeShape`` passthrough shapes, whose minted
        names never reach an emitted file —
        ``ValidationReportCompiler._unblank_synthetic_shape_ids`` renders
        them back as blank nodes in the report.
        """
        graph = self.output_reader.graph
        # Every id reaching here came from a ``sub="^_:"`` filter, so it
        # is a blank node in rdfine's ``_:<label>`` rendering.
        node = BNode(node_id[2:])

        def describe(term, seen: frozenset) -> str:
            if not isinstance(term, BNode):
                return str(term)
            if term in seen:
                # Blank-node cycles are legal RDF; stop rather than recur
                # forever, and let the rest of the description separate.
                return "<cycle>"
            inner = seen | {term}
            return (
                "["
                + ";".join(
                    sorted(
                        f"{pred}={describe(obj, inner)}"
                        for pred, obj in graph.predicate_objects(term)
                    )
                )
                + "]"
            )

        incoming = tuple(
            sorted(
                f"{describe(subject, frozenset({node}))}<{pred}"
                for subject, pred in graph.subject_predicates(node)
            )
        )
        return describe(node, frozenset()), incoming
