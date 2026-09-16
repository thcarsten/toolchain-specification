import re

from rdflib import BNode, Graph, URIRef

from rdfine import GraphReader

from ..compiler_abc import Compiler
from ..utils import lookup_seeded_pipeline_id


class ConfigTranslator(Compiler):
    """Derive each step's *compiler-facing* config from its authored one.

    A ``tcs:PipelineComponent`` declares what the compiler needs under
    ``dcat:hadRole tcs:compilerFacingConfigShape``, and — optionally —
    the smaller contract its author must satisfy under
    ``tcs:userFacingConfigShape``. When the two differ, the component
    carries a ``tcs:configTranslation`` SPARQL ``CONSTRUCT`` that bridges
    them: it is what lets an author omit what the generator can infer
    (an RDF-Connect ``rdfc:writer`` the wiring already implies) and lets
    a component hard-code what the author must not set (a fixed
    semantic.works ``GRAPH_NAME``).

    The authored config stays untouched at ``p-plan:hasInputVar``; the
    translated one is attached at ``tcs:compilerConfig``. Two predicates
    rather than one because each shape has to target its own config, and
    because the ``len(configs) != 1`` guards throughout the framework
    compilers would break if both hung off ``hasInputVar``.

    **Most components need no query.** Absent a
    ``tcs:userFacingConfigShape`` the compiler-facing shape *is* the
    authoring contract, and translation is an identity copy. Declaring a
    user-facing shape without a ``tcs:configTranslation`` is an authoring
    error and raises rather than silently identity-copying — the author
    asked for two different contracts and supplied nothing to bridge
    them.

    The copy is a Concise Bounded Description
    (``GraphReader.traverse`` in ``stop_at_named_nodes`` mode, excluding
    ``dcat:qualifiedRelation``) — the same traversal
    :func:`compilers.utils.extract_config` performs, and for the same
    reason: a config value may *be* a named resource (a channel IRI),
    whose own description must not be dragged in.

    Every blank node in the body is re-minted. Were the copy to reuse
    the authored config's blank nodes, the two configs would share
    structure and a later injection into the compiler-facing config
    would silently mutate the authored one — which is exactly what the
    split exists to prevent.
    """

    #: Component roles, as they appear on a ``dcat:Relationship``.
    compiler_facing_role: str = "tcs:compilerFacingConfigShape"
    user_facing_role: str = "tcs:userFacingConfigShape"

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        self.pipeline_id: str | None = None
        self.translatable_steps: list[tuple[str, str, str]] = []
        self._next_index = 0

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Triggered once bridging and per-boundary configuration have
        settled, for as long as some step still lacks a
        ``tcs:compilerConfig``.

        ``dct:creator tcs:SegmentTagger`` marks the end of bridging (the
        same gate :class:`RdfcConfigCompiler` uses). That alone is not
        enough: the per-boundary config compilers mint
        ``p-plan:hasInputVar`` for the steps
        :class:`BridgeTransportCompiler` inserted, and a compiler runs at
        most once — translating before they finish would leave a bridge
        step with no compiler-facing config and no second chance. So this
        also waits until no boundary step is left unconfigured.
        """
        segments_tagged = not graph_reader.filter(
            pred="dct:creator", obj="tcs:SegmentTagger"
        ).df.empty
        if not segments_tagged:
            return False

        boundary_unconfigured = graph_reader.select(
            "?step",
            """
            ?step a tcs:InstancePipelineComponent ;
                  prov:specializationOf ?component .
            { ?component a tcs:EntryBoundaryComponent }
            UNION
            { ?component a tcs:ExitBoundaryComponent }
            FILTER NOT EXISTS { ?step p-plan:hasInputVar ?config }
            """,
        )
        if not boundary_unconfigured.empty:
            return False

        return not graph_reader.select(
            "?step",
            """
            ?step a tcs:InstancePipelineComponent ;
                  p-plan:hasInputVar ?config .
            FILTER NOT EXISTS { ?step tcs:compilerConfig ?compiler_config }
            """,
        ).empty

    def compile(self) -> Graph:
        self.lookup_target_pipeline()
        self.list_translatable_steps()
        self.translate_step_configs()
        return self.output_reader.graph

    def lookup_target_pipeline(self) -> None:
        """Scope the run to the pipeline being compiled — the catalog
        graph can hold other plans, and their steps are none of this
        compiler's business.
        """
        self.pipeline_id = lookup_seeded_pipeline_id(self.output_reader)

    def list_translatable_steps(self) -> None:
        """Collect ``(step, component, config)`` for every step of the
        target pipeline carrying exactly one authored config.

        :class:`PipelineEnricher` has already given every step exactly
        one ``tcs:PipelineConfig``, so "exactly one" is the norm rather
        than a filter that discards work. A step that somehow has two is
        skipped rather than guessed at, matching the ``len(configs) != 1``
        guards the framework compilers already apply.
        """
        rows = self.output_reader.select(
            "?step ?component ?config",
            f"""
            ?step a tcs:InstancePipelineComponent ;
                  p-plan:isStepOfPlan {self.pipeline_id} ;
                  prov:specializationOf ?component ;
                  p-plan:hasInputVar ?config .
            FILTER NOT EXISTS {{ ?step tcs:compilerConfig ?existing }}
            """,
        )
        if rows.empty:
            self.translatable_steps = []
            return

        rows = rows.drop_duplicates(subset=["step", "component", "config"])
        config_counts = rows.groupby("step")["config"].nunique()
        # Sorted for the same reason every other enumeration in this
        # package is: the index below is baked into the minted IRI, and
        # SPARQL result order is not guaranteed.
        self.translatable_steps = sorted(
            (row["step"], row["component"], row["config"])
            for _, row in rows.iterrows()
            if config_counts[row["step"]] == 1
        )

    def translate_step_configs(self) -> None:
        """Attach a compiler-facing config to each collected step.

        Identity copy by default; a component declaring a user-facing
        shape gets its ``tcs:configTranslation`` run instead.
        """
        for step, component, config in self.translatable_steps:
            translation = self._lookup_config_translation(component)
            compiler_config = self._mint_id("compilerconfig")
            embedded = self._mint_id("compilerembedded")

            if translation is None:
                body = self._copy_config(config, compiler_config, embedded)
            else:
                body = self._run_translation(
                    translation,
                    source=config,
                    target=embedded,
                    step=step,
                    component=component,
                )

            self.output_reader = self.output_reader.add(body)
            self.output_reader = self.output_reader.add(
                self.output_reader.construct(
                    f"""
                    {step} tcs:compilerConfig {compiler_config} .
                    {compiler_config} a tcs:CompilerConfig ;
                        tcs:embedded {embedded} .
                    """,
                    f"{step} a tcs:InstancePipelineComponent .",
                ).graph
            )

    def _lookup_config_translation(self, component: str) -> str | None:
        """The component's ``tcs:configTranslation``, or ``None`` when it
        declares no user-facing shape.

        Raises when a user-facing shape is declared without a query: the
        component asked for two distinct contracts and supplied nothing
        to bridge them, so an identity copy would quietly emit a config
        that does not satisfy the compiler-facing shape.
        """
        queries = (
            self.output_reader.filter(sub=component, pred="tcs:configTranslation")
            .df["obj"]
            .to_list()
        )
        declares_user_facing = self.output_reader.ask(
            f"""
            {component} dcat:qualifiedRelation ?relation .
            ?relation dcat:hadRole {self.user_facing_role} .
            """
        )

        if declares_user_facing and not queries:
            raise ValueError(
                f"{component} declares a {self.user_facing_role} but no "
                "tcs:configTranslation to derive its "
                f"{self.compiler_facing_role} config from. Either supply the "
                "query, or drop the user-facing shape, which makes the "
                "compiler-facing shape the authoring contract."
            )
        if not declares_user_facing:
            return None
        return queries[0]

    def _copy_config(self, config: str, compiler_config: str, embedded: str) -> Graph:
        """The identity path: the authored config's CBD, re-rooted on
        ``compiler_config`` with every blank node re-minted.

        ``rdf:type`` on the authored root is dropped — the new node is a
        ``tcs:CompilerConfig``, asserted by the caller, not a second
        ``tcs:PipelineConfig`` competing with the original. Everything
        else the root carries (``dct:format`` above all, which decides
        how the body parses) comes across untouched.
        """
        source = self.output_reader.traverse(
            config, stop_at_named_nodes=True, exclude="dcat:qualifiedRelation"
        ).graph

        expand = self.output_reader.prefix_store.expand_string
        config_node = URIRef(expand(config))
        embedded_predicate = URIRef(expand("tcs:embedded"))
        rdf_type = URIRef(expand("rdf:type"))

        # The authored embedded root becomes the named ``?target`` node,
        # so a compiler-facing shape can target
        # ``tcs:compilerConfig/tcs:embedded`` the same way a user-facing
        # one targets ``p-plan:hasInputVar/tcs:embedded``.
        mapping: dict[object, object] = {config_node: URIRef(expand(compiler_config))}
        for embedded_root in source.objects(config_node, embedded_predicate):
            mapping[embedded_root] = URIRef(expand(embedded))

        # Labelled from a sorted walk rather than ``BNode()``'s random
        # uuid: these nodes are minted per run, and a random label would
        # make anything that serializes them differ between runs over
        # identical input.
        remaining = sorted(
            {
                term
                for triple in source
                for term in triple
                if isinstance(term, BNode) and term not in mapping
            },
            key=str,
        )
        stem = embedded.lstrip(":").replace(":", "_")
        for index, node in enumerate(remaining):
            mapping[node] = BNode(f"{stem}_{index}")

        copied = Graph(bind_namespaces="none")
        for subject, predicate, obj in source:
            if subject == config_node and predicate == rdf_type:
                continue
            copied.add(
                (mapping.get(subject, subject), predicate, mapping.get(obj, obj))
            )
        return copied

    def _run_translation(
        self, query: str, *, source: str, target: str, step: str, component: str
    ) -> Graph:
        """Run a component's ``tcs:configTranslation``.

        ``?source`` (the authored ``tcs:embedded`` node), ``?target``
        (the compiler-facing one), ``?step`` and ``?component`` are bound
        by textual substitution before the query runs — the same way
        :class:`RdfcConfigCompiler` and
        :class:`ValidationReportCompiler` already build their queries.
        ``?target`` is substituted with a named IRI on purpose: a blank
        node in a ``CONSTRUCT`` template is minted afresh per solution,
        so a multi-row ``WHERE`` would scatter the body across several
        unconnected nodes.
        """
        authored_embedded = (
            self.output_reader.filter(sub=source, pred="tcs:embedded")
            .df["obj"]
            .to_list()
        )
        bindings = {
            "source": authored_embedded[0] if authored_embedded else source,
            "target": target,
            "step": step,
            "component": component,
        }
        bound = query
        for name, value in bindings.items():
            # A plain word boundary would also match inside
            # ``?sourceThing``; the lookahead keeps substitution to whole
            # variable names.
            bound = re.sub(rf"\?{name}(?![A-Za-z0-9_])", value, bound)

        result = self.output_reader.sparql(bound)
        if not isinstance(result, GraphReader):
            raise ValueError(
                f"{component}'s tcs:configTranslation must be a CONSTRUCT "
                f"query; got a {type(result).__name__} result instead."
            )
        return result.graph

    def _mint_id(self, prefix: str) -> str:
        """``:{prefix}_N``, skipping any name already taken."""
        candidate = f":{prefix}_{self._next_index}"
        self._next_index += 1
        while self.output_reader.check_exists(candidate):
            candidate = f":{prefix}_{self._next_index}"
            self._next_index += 1
        return candidate
