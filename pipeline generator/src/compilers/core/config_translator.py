import re

from rdflib import BNode, Graph, URIRef

from rdfine import GraphReader

from ..compiler_abc import Compiler
from ..utils import lookup_seeded_pipeline_id


class ConfigTranslator(Compiler):
    """Derive a compiler-facing config for the steps that need one.

    A ``tcs:PipelineComponent`` declares what the compiler needs under
    ``dcat:hadRole tcs:compilerFacingConfigShape``, and — optionally —
    the smaller contract its author must satisfy under
    ``tcs:userFacingConfigShape``. When a component declares both, it
    also carries a ``tcs:configTranslation`` SPARQL ``CONSTRUCT`` that
    bridges them: that is what lets an author omit what the generator can
    infer (an RDF-Connect ``rdfc:writer`` the wiring already implies) and
    lets a component hard-code what the author must not set (a fixed
    semantic.works ``GRAPH_NAME``). The result is attached at
    ``tcs:compilerConfig``, leaving the authored config untouched at
    ``p-plan:hasInputVar``.

    **A component that declares no user-facing shape is not translated,
    but it still gets the predicate.** Its two contracts are the same
    document, so ``tcs:compilerConfig`` is pointed straight at the
    authored config node — one triple, no copy. Every step therefore has
    a ``tcs:compilerConfig``, which is what lets a compiler-facing shape
    target ``tcs:compilerConfig/tcs:embedded`` and a user-facing shape
    target ``p-plan:hasInputVar/tcs:embedded``, each against its own
    config, with no conditional logic in either. Without the alias, a
    shape targeting the derived path would select nothing for an
    untranslated step and validate nothing at all — while still
    reporting ``conforms: true``.

    Aliasing rather than copying is the distinction that matters. An
    earlier version copied every config body to make the predicate
    universal; that bought nothing and cost a great deal, because it
    made consumers wait for the copy and so needed an ordering gate on
    each of them — one of which was unsatisfiable on a pipeline whose
    bridge-inserted ``sw:rdf-ingest-service`` step never gets an
    authored config, silently dropping every LDIO and RDF-Connect file
    from the build. The alias costs one triple and creates no such
    dependency: consumers read through
    :func:`compilers.utils.lookup_step_config`, which resolves to the
    authored config whether or not this compiler has run yet.

    Declaring a user-facing shape without a ``tcs:configTranslation``
    raises: the author asked for two different contracts and supplied
    nothing to bridge them.
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
        """Triggered once bridging has settled, while some step whose
        component declares a user-facing shape still lacks its derived
        config.

        ``dct:creator tcs:SegmentTagger`` marks the end of bridging (the
        same gate :class:`RdfcConfigCompiler` uses), so the steps
        :class:`BridgeTransportCompiler` inserts are present and their
        authored configs minted before anything is translated.
        """
        segments_tagged = not graph_reader.filter(
            pred="dct:creator", obj="tcs:SegmentTagger"
        ).df.empty
        if not segments_tagged:
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

        Every such step gets a ``tcs:compilerConfig``; whether that means
        running a query or aliasing the authored node is decided per
        component in :meth:`translate_step_configs`.

        A step with two configs is skipped rather than guessed at,
        matching the ``len(configs) != 1`` guards the framework compilers
        already apply.
        """
        rows = self.output_reader.select(
            "?step ?component ?config",
            f"""
            ?step a tcs:InstancePipelineComponent ;
                  p-plan:isStepOfPlan {self.pipeline_id} ;
                  prov:specializationOf ?component ;
                  p-plan:hasInputVar ?config .
            {{
                FILTER NOT EXISTS {{ ?step tcs:compilerConfig ?existing }}
            }} UNION {{
                # An alias put there by whichever compiler minted the
                # config. Harmless for a one-contract component and
                # wrong for a two-contract one, so the latter is
                # re-examined here rather than skipped.
                ?step tcs:compilerConfig ?config .
                ?component dcat:qualifiedRelation/dcat:hadRole
                    {self.user_facing_role} .
            }}
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
        """Give every collected step a ``tcs:compilerConfig``: the result
        of its component's ``tcs:configTranslation`` where there is one,
        the authored config node itself where there is not."""
        for step, component, config in self.translatable_steps:
            query = self._lookup_config_translation(component)
            if query is None:
                self.output_reader = self.output_reader.add(
                    self.output_reader.construct(
                        f"{step} tcs:compilerConfig {config} .",
                        f"{step} p-plan:hasInputVar {config} .",
                    ).graph
                )
                continue

            # Drop an alias to the authored config before pointing the
            # step at a derived one: leaving both would give the step two
            # compiler-facing configs, and every consumer treats that as
            # a modelling error and compiles nothing.
            self.output_reader = self.output_reader.remove(
                self.output_reader.construct(
                    f"{step} tcs:compilerConfig {config} .",
                    f"{step} tcs:compilerConfig {config} .",
                ).graph
            )

            compiler_config = self._mint_id("compilerconfig")
            body, embedded = self._run_translation(
                query, config=config, step=step, component=component
            )

            self.output_reader = self.output_reader.add(body)
            self.output_reader = self.output_reader.add(
                self.output_reader.construct(
                    f"""
                    {step} tcs:compilerConfig {compiler_config} .
                    {compiler_config} a tcs:CompilerConfig .
                    """,
                    f"{step} a tcs:InstancePipelineComponent .",
                ).graph
            )
            self.output_reader = self.output_reader.add(
                self._attach_embedded(compiler_config, embedded)
            )

    def _lookup_config_translation(self, component: str) -> str | None:
        """The component's ``tcs:configTranslation``, or ``None`` when it
        declares no user-facing shape and so needs no translating.

        Raises when it declares a user-facing shape without a query: the
        component asked for two distinct contracts and supplied nothing
        to bridge them, so compiling its authored config unchanged would
        quietly emit something the compiler-facing shape rejects.
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
        if not declares_user_facing:
            return None
        if not queries:
            raise ValueError(
                f"{component} declares a {self.user_facing_role} but no "
                "tcs:configTranslation to derive its "
                f"{self.compiler_facing_role} config from. Either supply the "
                "query, or drop the user-facing shape, which makes the "
                "compiler-facing shape the authoring contract."
            )
        return queries[0]

    def _run_translation(
        self, query: str, *, config: str, step: str, component: str
    ) -> tuple[Graph, BNode]:
        """Run one ``tcs:configTranslation``; return its graph and the
        body root to hang off the compiler-facing config.

        ``?config``, ``?step`` and ``?component`` are bound by textual
        substitution, matching how :class:`RdfcConfigCompiler` and
        :class:`ValidationReportCompiler` build their queries. All three
        are named IRIs, which is what makes that safe: a query reaches
        the authored body by matching ``?config tcs:embedded ?source``
        rather than by having ``?source`` pasted in. That matters —
        the authored ``tcs:embedded`` node is a blank node, and a
        blank-node label written into a ``WHERE`` clause is not a
        reference to that node but an existential that matches anything,
        which turns ``?source ?p ?o`` into "every triple in the graph".

        ``?target`` is substituted with a temporary named IRI, because a
        blank node in a ``CONSTRUCT`` template is minted afresh per
        solution and a multi-row ``WHERE`` would scatter the body over
        several unconnected nodes. That name is an implementation
        detail: the result is relabelled back to a blank node before it
        reaches the build, so a translated config has exactly the shape
        an authored one has and nothing downstream needs to know the
        difference.
        """
        target = self._mint_id("compilerembedded")
        bound = query
        for name, value in (
            ("target", target),
            ("config", config),
            ("step", step),
            ("component", component),
        ):
            # A plain word boundary would also match inside
            # ``?configThing``; the lookahead keeps substitution to whole
            # variable names.
            bound = re.sub(rf"\?{name}(?![A-Za-z0-9_])", value, bound)

        result = self.output_reader.sparql(bound)
        if not isinstance(result, GraphReader):
            raise ValueError(
                f"{component}'s tcs:configTranslation must be a CONSTRUCT "
                f"query; got a {type(result).__name__} result instead."
            )

        expand = self.output_reader.prefix_store.expand_string
        target_node = URIRef(expand(target))
        body_root = BNode(target.lstrip(":").replace(":", "_"))
        relabelled = Graph(bind_namespaces="none")
        for subject, predicate, obj in result.graph:
            relabelled.add(
                (
                    body_root if subject == target_node else subject,
                    predicate,
                    body_root if obj == target_node else obj,
                )
            )
        return relabelled, body_root

    def _attach_embedded(self, compiler_config: str, embedded: BNode) -> Graph:
        """``<compiler_config> tcs:embedded <body root>``.

        Built directly rather than through ``construct``, whose template
        is text and so cannot carry a blank node through by identity.
        """
        expand = self.output_reader.prefix_store.expand_string
        graph = Graph(bind_namespaces="none")
        graph.add(
            (URIRef(expand(compiler_config)), URIRef(expand("tcs:embedded")), embedded)
        )
        return graph

    def _mint_id(self, prefix: str) -> str:
        """``:{prefix}_N``, skipping any name already taken."""
        candidate = f":{prefix}_{self._next_index}"
        self._next_index += 1
        while self.output_reader.check_exists(candidate):
            candidate = f":{prefix}_{self._next_index}"
            self._next_index += 1
        return candidate
