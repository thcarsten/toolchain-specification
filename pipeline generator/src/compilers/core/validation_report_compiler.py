"""Attach a SHACL validation report to the ``tcs:PipelineBuild``.

Implements pillar 1 of the strategy in ``test suite/README.md`` (plain,
``sh:target``-based SHACL validation): ``normalize_config_shapes`` gives
every configShape a ``sh:target``, ``validate_normal_shapes`` runs
pySHACL over the result.

Pillar 2 (turning passthroughShapes into inputShapes/outputShapes,
shape-matching): ``gather_throughput_shapes`` resolves an
inputShape/outputShape for every ``tcs:Channel`` from real
inputShape/outputShape attachments; ``normalize_passthrough_shapes``
regularizes every ``passthroughShape`` into a concrete
inputShape/outputShape pair, in dataflow order; ``fill_missing_shapes``
gives every channel still without one a trivial empty shape;
``list_shapes_to_match`` builds the (channel, inputShape, outputShape)
table; ``validate_throughput_shapes`` submits each pair to the
shape-matching bridge (see :meth:`ValidationReportCompiler.match_shapes`
— the bridge itself doesn't exist yet, so this is a documented stub).
``generate_validation_report`` combines everything into the one file
attached to the build.
"""

import pandas as pd
from rdflib import Graph, Literal, URIRef
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
        # Populated by validate_normal_shapes(); consumed by
        # generate_validation_report() at the end of the pipeline.
        self._shacl_report: GraphReader | None = None

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
        self.gather_throughput_shapes()
        self.normalize_passthrough_shapes()
        self.fill_missing_shapes()
        self.list_shapes_to_match()
        self.validate_throughput_shapes()
        self.generate_validation_report()
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
        current build graph already carries one) and stash the
        resulting report for :meth:`generate_validation_report` to
        combine with the throughput-matching results and attach as a
        single file at the end of the pipeline.

        Sets :attr:`conforms` to the report's own ``sh:conforms``
        boolean so ``gen.compilers[ValidationReportCompiler].conforms``
        can be audited directly after a compile, without re-parsing the
        attached report.
        """
        report = self.output_reader.validate(advanced=True, inference="rdfs")
        self.conforms = report.ask("?r sh:conforms true")
        self._shacl_report = report

    def gather_throughput_shapes(self) -> None:
        """Resolve an ``inputShape``/``outputShape`` for every ``tcs:Channel``,
        per the three-tier precedence in ``test suite/README.md``
        (step 3): ``Channel`` > ``InstancePipelineComponent`` >
        ``PipelineComponent``.

        A channel's ``outputShape`` (what a reader receives) comes from
        a reader's ``inputShape``; a channel's ``inputShape`` (what a
        writer produces) comes from a writer's ``outputShape`` — in
        each case, an instance's own shape wins over the shape of the
        ``tcs:PipelineComponent`` it specializes, and either loses to a
        shape already attached directly to the channel.

        Channels are discovered as every object of ``tcs:readsFrom`` /
        ``tcs:writesTo`` rather than by ``rdf:type tcs:Channel`` —
        channels minted by :class:`PipelineEnricher` at compile time
        never pick up that inference-derived type (``GraphReader.infer()``
        is only ever run once, before ``PipelineGenerator.compile()``
        starts), so relying on it here would silently skip them.

        Channels that end up with neither a direct nor an inherited
        shape for a given role are left alone here —
        ``normalize_passthrough_shapes`` and ``fill_missing_shapes``
        (not yet implemented) handle the rest. If more than one reader
        (or writer) resolves to a different shape for the same channel,
        the first one found (sorted for determinism) wins — the model
        has no way to disambiguate further at this stage.
        """
        reads_from = self.output_reader.select(
            "?instance ?channel", "?instance tcs:readsFrom ?channel ."
        )
        writes_to = self.output_reader.select(
            "?instance ?channel", "?instance tcs:writesTo ?channel ."
        )
        channels = set(reads_from["channel"]) | set(writes_to["channel"])

        # Any node's own direct inputShape/outputShape attachment —
        # tier 1 when looked up by channel id below.
        own_shape: dict[tuple[str, str], str] = {}
        for _, row in self.output_reader.select(
            "?node ?role ?shape",
            """
            ?node dcat:qualifiedRelation ?rel .
            ?rel dcat:hadRole ?role ; dct:relation ?shape .
            FILTER (?role IN (tcs:inputShape, tcs:outputShape))
            """,
        ).iterrows():
            own_shape.setdefault((row["node"], row["role"]), row["shape"])

        input_shape = self._effective_role_shapes("tcs:inputShape")
        output_shape = self._effective_role_shapes("tcs:outputShape")

        next_index = 0
        for channel_id in sorted(channels):
            if (channel_id, "tcs:outputShape") not in own_shape:
                readers = sorted(
                    reads_from.loc[
                        reads_from["channel"] == channel_id, "instance"
                    ].tolist()
                )
                for reader in readers:
                    shape = input_shape.get(reader)
                    if shape is not None:
                        next_index = self._attach_shape(
                            channel_id, "tcs:outputShape", shape, next_index
                        )
                        break

            if (channel_id, "tcs:inputShape") not in own_shape:
                writers = sorted(
                    writes_to.loc[
                        writes_to["channel"] == channel_id, "instance"
                    ].tolist()
                )
                for writer in writers:
                    shape = output_shape.get(writer)
                    if shape is not None:
                        next_index = self._attach_shape(
                            channel_id, "tcs:inputShape", shape, next_index
                        )
                        break

    def normalize_passthrough_shapes(self) -> None:
        """Regularize every ``tcs:passthroughShape`` into a concrete
        ``tcs:inputShape`` / ``tcs:outputShape`` pair on the same
        ``InstancePipelineComponent``, per ``test suite/README.md``
        step 4:

        - The instance's ``inputShape`` is a copy of its own
          ``passthroughShape`` (tier 2 own attachment, tier 3 fallback
          to the specialized ``tcs:PipelineComponent``'s) — whatever it
          receives must satisfy it.
        - Its ``outputShape`` is the *current* ``inputShape`` of the
          channel it ``tcs:readsFrom`` — "whatever I actually received
          is what I pass through unchanged", which may be more specific
          than the ``passthroughShape`` itself once an upstream shape
          is known.

        Regularization runs once, in dataflow order
        (:meth:`_instances_in_dataflow_order`, writers before readers),
        so a downstream passthrough sees an upstream passthrough's
        freshly-regularized ``outputShape`` once it has been propagated
        onto their shared channel — the same bidirectional propagation
        :meth:`gather_throughput_shapes` performs for real shapes
        (instance ``inputShape`` → its read channel's ``outputShape``;
        instance ``outputShape`` → its write channel's ``inputShape``),
        replayed here as each passthrough is regularized.

        An instance that already has its own ``inputShape``/
        ``outputShape`` (a real, non-passthrough transform) is left
        untouched for that role even if it also happens to carry a
        ``passthroughShape`` — that combination isn't used anywhere in
        the current catalog, but "more specific info wins" is the safer
        default.
        """
        passthrough_shape = self._effective_role_shapes("tcs:passthroughShape")
        if not passthrough_shape:
            return

        input_shape = self._effective_role_shapes("tcs:inputShape")
        output_shape = self._effective_role_shapes("tcs:outputShape")

        reads_from = self.output_reader.select(
            "?instance ?channel", "?instance tcs:readsFrom ?channel ."
        )
        writes_to = self.output_reader.select(
            "?instance ?channel", "?instance tcs:writesTo ?channel ."
        )

        channel_inputshape = {
            row["channel"]: row["shape"]
            for _, row in self.output_reader.select(
                "?channel ?shape",
                """
                ?channel dcat:qualifiedRelation ?rel .
                ?rel dcat:hadRole tcs:inputShape ; dct:relation ?shape .
                """,
            ).iterrows()
        }
        channel_outputshape = {
            row["channel"]: row["shape"]
            for _, row in self.output_reader.select(
                "?channel ?shape",
                """
                ?channel dcat:qualifiedRelation ?rel .
                ?rel dcat:hadRole tcs:outputShape ; dct:relation ?shape .
                """,
            ).iterrows()
        }

        next_index = 0
        for instance in self._instances_in_dataflow_order(reads_from, writes_to):
            shape = passthrough_shape.get(instance)
            if shape is None:
                continue

            if instance not in input_shape:
                next_index = self._attach_shape(
                    instance, "tcs:inputShape", shape, next_index
                )
                for channel in sorted(
                    reads_from.loc[
                        reads_from["instance"] == instance, "channel"
                    ].tolist()
                ):
                    if channel not in channel_outputshape:
                        next_index = self._attach_shape(
                            channel, "tcs:outputShape", shape, next_index
                        )
                        channel_outputshape[channel] = shape

            if instance in output_shape:
                continue

            read_channels = sorted(
                reads_from.loc[reads_from["instance"] == instance, "channel"].tolist()
            )
            resolved = next(
                (
                    channel_inputshape[channel]
                    for channel in read_channels
                    if channel in channel_inputshape
                ),
                None,
            )
            if resolved is None:
                continue

            next_index = self._attach_shape(
                instance, "tcs:outputShape", resolved, next_index
            )
            for channel in sorted(
                writes_to.loc[writes_to["instance"] == instance, "channel"].tolist()
            ):
                if channel not in channel_inputshape:
                    next_index = self._attach_shape(
                        channel, "tcs:inputShape", resolved, next_index
                    )
                    channel_inputshape[channel] = resolved

    def fill_missing_shapes(self) -> None:
        """Attach a trivial, empty ``sh:NodeShape`` ("no known
        constraint") to every ``tcs:Channel`` still missing an
        ``inputShape`` or ``outputShape`` after
        :meth:`gather_throughput_shapes` and
        :meth:`normalize_passthrough_shapes` — no real or passthrough
        producer/consumer was ever able to supply one. Ensures every
        channel has *something* to match against in
        :meth:`validate_throughput_shapes`, per ``test suite/README.md``
        step 5.
        """
        reads_from = self.output_reader.select(
            "?instance ?channel", "?instance tcs:readsFrom ?channel ."
        )
        writes_to = self.output_reader.select(
            "?instance ?channel", "?instance tcs:writesTo ?channel ."
        )
        channels = set(reads_from["channel"]) | set(writes_to["channel"])

        resolved = {
            (row["channel"], row["role"])
            for _, row in self.output_reader.select(
                "?channel ?role",
                """
                ?channel dcat:qualifiedRelation ?rel .
                ?rel dcat:hadRole ?role ; dct:relation ?shape .
                FILTER (?role IN (tcs:inputShape, tcs:outputShape))
                """,
            ).iterrows()
        }

        next_index = 0
        for channel_id in sorted(channels):
            for role in ("tcs:inputShape", "tcs:outputShape"):
                if (channel_id, role) in resolved:
                    continue
                shape_id, next_index = self._mint_empty_shape(next_index)
                next_index = self._attach_shape(channel_id, role, shape_id, next_index)

    def list_shapes_to_match(self) -> None:
        """Build the ``(channel, inputShape, outputShape)`` table
        :meth:`validate_throughput_shapes` needs to check — one row per
        ``tcs:Channel``, per architecture step 6 in
        ``test suite/README.md``. Every channel is expected to have
        both roles resolved by this point (``gather_throughput_shapes``
        + ``normalize_passthrough_shapes`` + ``fill_missing_shapes``).
        Stored on :attr:`shapes_to_match` for the next step to consume
        without re-querying.
        """
        reads_from = self.output_reader.select(
            "?instance ?channel", "?instance tcs:readsFrom ?channel ."
        )
        writes_to = self.output_reader.select(
            "?instance ?channel", "?instance tcs:writesTo ?channel ."
        )
        channels = set(reads_from["channel"]) | set(writes_to["channel"])

        # dcat:qualifiedRelation/hadRole is used for the same roles on
        # PipelineComponents and InstancePipelineComponents too, so the
        # query alone can't tell a channel apart from e.g. a real
        # transform's own inputShape/outputShape attachment — restrict
        # to the discovered channel set in Python instead.
        pairs = self.output_reader.select(
            "?channel ?inputShape ?outputShape",
            """
            ?channel dcat:qualifiedRelation ?inputRel .
            ?inputRel dcat:hadRole tcs:inputShape ; dct:relation ?inputShape .
            ?channel dcat:qualifiedRelation ?outputRel .
            ?outputRel dcat:hadRole tcs:outputShape ; dct:relation ?outputShape .
            """,
        )
        self.shapes_to_match = pairs[pairs["channel"].isin(channels)].reset_index(
            drop=True
        )

    def validate_throughput_shapes(self) -> None:
        """For every ``(channel, inputShape, outputShape)`` row from
        :meth:`list_shapes_to_match`, ask :meth:`match_shapes` whether
        data conforming to ``inputShape`` is guaranteed to also conform
        to ``outputShape``, and record the result as a new ``matches``
        column. Stored on :attr:`throughput_matches` for
        :meth:`generate_validation_report` to consume.
        """
        if not hasattr(self, "shapes_to_match"):
            self.list_shapes_to_match()

        matches = [
            self.match_shapes(row["inputShape"], row["outputShape"])
            for _, row in self.shapes_to_match.iterrows()
        ]
        self.throughput_matches = self.shapes_to_match.assign(
            matches=pd.Series(matches, dtype="object")
        )

    def match_shapes(self, input_shape: str, output_shape: str) -> bool | None:
        """Ask the external shape-matching bridge whether data
        conforming to ``input_shape`` is guaranteed to also conform to
        ``output_shape``.

        Returns ``None`` ("not verified") by default — deliberately a
        stub, not a hardwired network client: ``qsm-service``, the
        Node/Fastify bridge described in ``test suite/README.md``'s
        "Architecture" section, doesn't exist yet and its API contract
        is still undecided. Override this method on a subclass (or
        monkeypatch an instance) once the bridge is built and its
        contract is settled.
        """
        return None

    def generate_validation_report(self) -> None:
        """Combine :meth:`validate_normal_shapes`' pySHACL report with
        :meth:`validate_throughput_shapes`' per-channel match results
        into the one file attached to the build, per architecture step
        8 in ``test suite/README.md``.

        The throughput-matching vocabulary used here
        (``tcs:ThroughputMatchResult``, ``tcs:forChannel``,
        ``tcs:inputShape``, ``tcs:outputShape``, ``tcs:matches``) is
        provisional — it isn't part of the public semantic model yet,
        since the shape-matching bridge's own contract (see
        :meth:`match_shapes`) is still undecided. ``tcs:matches`` is a
        string literal (``"true"`` / ``"false"`` / ``"unknown"``)
        rather than a boolean so ``"unknown"`` — the expected default
        result until the bridge exists — has somewhere to live without
        overloading ``xsd:boolean``.
        """
        report = self._shacl_report
        assert report is not None, "validate_normal_shapes() must run first"

        next_index = 0
        for _, row in self.throughput_matches.iterrows():
            result_id = f":throughputresult_{next_index}"
            next_index += 1
            while report.check_exists(result_id):
                result_id = f":throughputresult_{next_index}"
                next_index += 1

            matches = row["matches"]
            matches_literal = (
                "true"
                if matches is True
                else "false" if matches is False else "unknown"
            )
            new_triples = report.construct(
                f"""
                {result_id} a tcs:ThroughputMatchResult ;
                    tcs:forChannel {row['channel']} ;
                    tcs:inputShape {row['inputShape']} ;
                    tcs:outputShape {row['outputShape']} ;
                    tcs:matches "{matches_literal}" .
                """,
                "?s ?p ?o .",
            ).graph
            report = report.add(new_triples)

        self.output_reader = attach_file(
            self.output_reader,
            filename=self.filename,
            filepath=self.filepath,
            content=report.serialize("ttl"),
        )

    def _effective_role_shapes(self, role: str) -> dict[str, str]:
        """Tier-2 (own ``dcat:qualifiedRelation`` attachment) over
        tier-3 (falls back to the shape attached to the
        ``tcs:PipelineComponent`` an instance specializes) resolution
        of a single role — e.g. ``tcs:inputShape``, ``tcs:outputShape``,
        ``tcs:passthroughShape`` — for every
        ``tcs:InstancePipelineComponent``. Shared by
        :meth:`gather_throughput_shapes` and
        :meth:`normalize_passthrough_shapes`.
        """
        effective: dict[str, str] = {}
        for _, row in self.output_reader.select(
            "?instance ?shape",
            f"""
            ?instance a tcs:InstancePipelineComponent ;
                      dcat:qualifiedRelation ?rel .
            ?rel dcat:hadRole {role} ; dct:relation ?shape .
            """,
        ).iterrows():
            effective.setdefault(row["instance"], row["shape"])

        for _, row in self.output_reader.select(
            "?instance ?shape",
            f"""
            ?instance a tcs:InstancePipelineComponent ;
                      prov:specializationOf ?component .
            ?component dcat:qualifiedRelation ?rel .
            ?rel dcat:hadRole {role} ; dct:relation ?shape .
            FILTER NOT EXISTS {{
                ?instance dcat:qualifiedRelation ?rel2 .
                ?rel2 dcat:hadRole {role} .
            }}
            """,
        ).iterrows():
            effective.setdefault(row["instance"], row["shape"])

        return effective

    def _instances_in_dataflow_order(self, reads_from, writes_to) -> list[str]:
        """Topologically order every step that participates in channel
        wiring, upstream (writers) before downstream (readers) of the
        same channel — the order :meth:`normalize_passthrough_shapes`
        needs so a passthrough component's regularized ``outputShape``
        is already visible on the channel a downstream passthrough
        reads it from.

        Plain DFS-postorder topological sort. Guarded by a ``visiting``
        set against a malformed (cyclic) channel graph — already
        flagged elsewhere by ``tcs:AcyclicGraphShape`` — rather than
        assuming acyclicity holds; a node caught mid-cycle is simply
        skipped instead of recursing forever (same backstop idiom as
        the ``dct:requires`` cycle guard elsewhere in the codebase).
        """
        channel_readers: dict[str, list[str]] = {}
        for _, row in reads_from.iterrows():
            channel_readers.setdefault(row["channel"], []).append(row["instance"])

        successors: dict[str, list[str]] = {}
        for _, row in writes_to.iterrows():
            for reader in channel_readers.get(row["channel"], []):
                successors.setdefault(row["instance"], []).append(reader)

        all_instances = sorted(set(reads_from["instance"]) | set(writes_to["instance"]))

        order: list[str] = []
        visited: set[str] = set()
        visiting: set[str] = set()

        def _visit(node: str) -> None:
            if node in visited or node in visiting:
                return
            visiting.add(node)
            for successor in sorted(successors.get(node, [])):
                _visit(successor)
            visiting.discard(node)
            visited.add(node)
            order.append(node)

        for instance in all_instances:
            _visit(instance)

        order.reverse()
        return order

    def _attach_shape(
        self, node_id: str, role: str, shape_id: str, next_index: int
    ) -> int:
        """Attach a resolved ``role`` shape onto ``node_id`` (a
        ``tcs:Channel`` or an ``InstancePipelineComponent``) via the
        same ``dcat:qualifiedRelation`` idiom used everywhere else,
        minting a named relation IRI the same way ``normalize_config_shapes``
        mints its target IRIs. ``shape_id`` is always a named IRI here —
        ``PipelineExtractor.name_blind_nodes`` renames every blank-node
        ``sh:NodeShape`` (e.g. a component's trivial passthrough/output
        shape) before this compiler ever runs.
        """
        # e.g. "tcs:inputShape" -> ":inputshaperel_N" — named after the
        # role so a reader can tell an inputShape relation from an
        # outputShape one at a glance, unlike a generic shared prefix.
        prefix = role.rsplit(":", maxsplit=1)[-1].lower() + "rel"
        relation_id = f":{prefix}_{next_index}"
        next_index += 1
        while self.output_reader.check_exists(relation_id):
            relation_id = f":{prefix}_{next_index}"
            next_index += 1

        # Anchored on the fully unconstrained "?s ?p ?o ." rather than,
        # say, "{node_id} a tcs:Channel ." — a channel minted by
        # PipelineEnricher at compile time never picks up that type
        # (see gather_throughput_shapes' docstring), so anchoring on it
        # would silently produce zero triples for those channels. Safe
        # here regardless of how many rows match because the template
        # mints no blank node — a repeated identical triple is a no-op
        # under RDF set semantics (same idiom as PipelineEnricher._add).
        new_triples = self.output_reader.construct(
            f"""
            {node_id} dcat:qualifiedRelation {relation_id} .
            {relation_id} a dcat:Relationship ;
                dcat:hadRole {role} ;
                dct:relation {shape_id} .
            """,
            "?s ?p ?o .",
        ).graph
        self.output_reader = self.output_reader.add(new_triples)
        return next_index

    def _mint_empty_shape(self, next_index: int) -> tuple[str, int]:
        """Mint a fresh, named, trivial ``sh:NodeShape`` ("no known
        constraint") for a channel no producer or consumer ever
        supplied one for.

        Built from raw rdflib triples rather than a SPARQL
        ``CONSTRUCT`` — same reason as ``PipelineEnricher._mint_config``:
        a fresh node must never be minted via a ``CONSTRUCT`` template
        paired with a broad ``"?s ?p ?o ."`` WHERE clause, which mints a
        *distinct* node per matched row instead of exactly one.
        """
        prefix_store = self.output_reader.prefix_store
        shape_id = f":emptyshape_{next_index}"
        next_index += 1
        while self.output_reader.check_exists(shape_id):
            shape_id = f":emptyshape_{next_index}"
            next_index += 1

        shape_uri = URIRef(prefix_store.expand_string(shape_id))
        new_triples = Graph()
        prefix_store.bind_to_namespace(new_triples)
        new_triples.add(
            (
                shape_uri,
                URIRef(prefix_store.expand_string("rdf:type")),
                URIRef(prefix_store.expand_string("sh:NodeShape")),
            )
        )
        new_triples.add(
            (
                shape_uri,
                URIRef(prefix_store.expand_string("rdfs:comment")),
                Literal(
                    "Auto-filled: no producer or consumer ever supplied a "
                    "shape for this channel."
                ),
            )
        )
        self.output_reader = self.output_reader.add(new_triples)
        return shape_id, next_index
