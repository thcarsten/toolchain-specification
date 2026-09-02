from rdflib import Graph, URIRef
from rdfine import GraphReader, GraphDict, receive_first
import re

from ..compiler_abc import Compiler
from ..utils import attach_file, extract_config, rewrite_compose_volume_host_path

# The RDF-Connect Python / Node runners mount their pipeline
# definition at this fixed container path. Kept next to the
# host-side output location so the pairing is visible in one place.
_RDFC_PIPELINE_CONTAINER_PATH = "/workspace/pipeline/pipeline.ttl"
_RDFC_PIPELINE_HOST_PATH = "./rdfc/pipeline.ttl"


class RdfcConfigCompiler(Compiler):
    """
    Compiles the pipeline definition file for a Rdf Connect pipeline.

    Also patches the paired ``tcs:DockerComposeConfig`` on
    ``rdfc:Orchestrator`` so its volume mount for ``pipeline.ttl``
    points at ``./rdfc/pipeline.ttl`` — the host location this
    compiler actually emits to. Doing that here rather than in
    :class:`DockerComposeCompiler` keeps the aggregator generic:
    each framework compiler shapes its own compose fragment, then
    the aggregator merges them.
    """

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        # Intermediate state — populated in ``compile``. ``rdfc_reader``
        # is a *separate* accumulator that holds only the RDF-Connect
        # pipeline triples eventually serialized into ``pipeline.ttl``;
        # it is not the compiler's output graph. The build graph the
        # compiler contributes to (an ``spdx:File`` node with the
        # serialized ttl) is managed via the base-class
        # :attr:`output_reader` through :func:`compilers.utils.attach_file`.
        self.rdfc_reader: GraphReader = GraphReader(Graph())
        self.pipeline_id: str = ""
        self.pipeline_ttl: str = ""

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Triggered when a container instantiates the RDF-Connect
        orchestrator, :class:`SegmentTagger` has recorded provenance
        (which implies :class:`BridgeTransportCompiler` has finished
        inserting any boundary steps), and every RDFC step in that
        container already has a ``p-plan:hasInputVar``. The last two
        gates together defer this compiler until the boundary config
        compilers have populated any Bridge-inserted steps — otherwise
        the emitted ``pipeline.ttl`` would silently omit their type
        triple, config body, and reader/writer wiring.
        """
        has_rdfc_container = not graph_reader.filter(
            pred="tcs:instantiates", obj="rdfc:Orchestrator"
        ).df.empty
        if not has_rdfc_container:
            return False
        segments_tagged = not graph_reader.filter(
            pred="dct:creator", obj="tcs:SegmentTagger"
        ).df.empty
        if not segments_tagged:
            return False
        unconfigured = graph_reader.select(
            "?step",
            """
            ?container a tcs:DockerContainer ;
                       tcs:instantiates rdfc:Orchestrator ;
                       tcs:runs ?step .
            ?step a tcs:InstancePipelineComponent ;
                  prov:specializationOf ?comp .
            ?comp a rdfc:Processor .
            FILTER NOT EXISTS { ?step p-plan:hasInputVar ?c }
            """,
        )
        return unconfigured.empty

    def compile(self) -> Graph:
        self.initialize_rdfc_reader()
        self.lookup_pipeline_id()
        self.describe_pipeline()
        self.describe_processors()
        self.describe_channel_wiring()
        self.describe_configs()
        self.describe_channels()
        self.serialize_pipeline_ttl()
        self.attach_rdfc_pipeline_file()
        return self.output_reader.graph

    def initialize_rdfc_reader(self) -> None:
        """Seed :attr:`rdfc_reader` with the ``rdfc:Pipeline`` typing
        triple every ``describe_*`` step below adds to.
        """
        self.rdfc_reader = self.input_reader.construct(
            "?pipeline a rdfc:Pipeline .",
            "?pipeline a tcs:PipelineDefinition",
        )

    def lookup_pipeline_id(self) -> None:
        """Locate the single ``tcs:PipelineDefinition`` node being
        compiled, needed both by the ``describe_*`` steps and to
        anchor the ``<>`` self-reference in :meth:`serialize_pipeline_ttl`.
        """
        self.pipeline_id = receive_first(
            self.input_reader.filter(pred="rdf:type", obj="tcs:PipelineDefinition").df[
                "sub"
            ],
        )

    def describe_pipeline(self) -> None:
        """
        Adds:
            - {self.pipeline_id} rdfc:consistsOf :env_{env_i} .
            - {self.pipeline_id} owl:imports ?import .
            - :env_{env_i} rdfc:instantiates {runner_id} .
            - :env_{env_i} rdfc:processor ?step .
        """

        # Fetching the rdfc:Runners as list
        runner_list = (
            self.input_reader.filter(pred="rdf:type", obj="rdfc:Runner")
            .df["sub"]
            .to_list()
        )

        # Each runner requires its own instanced environment
        env_i = 0  # Simple running index for the environments
        for runner_id in runner_list:
            env_i += 1
            # The pipeline HAS to be named <>, this is what RDF Connect expects. Otherwise it will not work
            runner_reader = self.input_reader.construct(
                f"""
                    {self.pipeline_id} rdfc:consistsOf :env_{env_i} . 
                     {self.pipeline_id} owl:imports ?import .
                    :env_{env_i} rdfc:instantiates {runner_id} .
                    :env_{env_i} rdfc:processor ?step . """,
                f"""
                    ?processor dct:requires {runner_id} .
                    ?processor owl:imports ?import .
                    ?step prov:specializationOf ?processor . 
                    """,
            )

            self.rdfc_reader = self.rdfc_reader.add(runner_reader.graph)

            # Also emit the runner's own owl:imports (e.g.
            # ``@rdfc/js-runner/index.ttl``). Without it the
            # orchestrator sees ``:env_1 rdfc:instantiates rdfc:NodeRunner``
            # but has no triples describing ``rdfc:NodeRunner`` itself
            # (its ``rdfc:CommandRunner`` typing, ``rdfc:command``, and
            # ``rdfc:handlesSubjectsOf`` all live in the runner's
            # own index.ttl) and refuses to start.
            runner_import_reader = self.input_reader.construct(
                f"{self.pipeline_id} owl:imports ?import .",
                f"{runner_id} owl:imports ?import .",
            )
            self.rdfc_reader = self.rdfc_reader.add(runner_import_reader.graph)

    def describe_processors(self) -> None:
        """
        Adds:
            - ?step a ?processor .
            - ?processor ?config .
        """

        processor_reader = self.input_reader.construct(
            """
                ?step a ?component . 
                """,
            """            
                ?step prov:specializationOf ?component .
                ?container tcs:instantiates ?component .
                ?container tcs:instantiates rdfc:Orchestrator .
                ?container tcs:runs ?step .
            """,
        )

        self.rdfc_reader = self.rdfc_reader.add(processor_reader.graph)

    def describe_configs(self) -> None:
        """
        Adds:
            - ?processor ?config ... .
        """

        step_list = self.rdfc_reader.filter(pred="rdfc:processor").df["obj"].to_list()
        config_df = self.output_reader.filter(
            sub=step_list, pred="p-plan:hasInputVar"
        ).df
        config_list = config_df["obj"].to_list()

        for config_id in config_list:
            step_id = receive_first(
                self.output_reader.filter(
                    sub=step_list, pred="p-plan:hasInputVar", obj=config_id
                ).df["sub"],
            )
            config_dict = extract_config(self.output_reader, config_id)
            config_dict["@id"] = step_id
            config_graph = GraphDict(
                config_dict, prefix_store=self.input_reader.prefix_store
            ).graph
            self.rdfc_reader = self.rdfc_reader.add(config_graph)

    def describe_channel_wiring(self) -> None:
        """Fill in a step's reader/writer config key when it's unambiguous.

        For each RDF-Connect step with exactly one ``tcs:readsFrom`` (or
        ``tcs:writesTo``) channel, looks up its component's configShape
        (the same ``dcat:qualifiedRelation`` / ``dcat:hadRole
        tcs:configShape`` attachment used by ``rdfc:SPARQLIngest``) for
        ``sh:property`` entries typed ``sh:class rdfc:Reader`` /
        ``rdfc:Writer``. If exactly one candidate ``sh:path`` exists,
        injects ``<path> <channel>`` into the step's config.
        ``PipelineEnricher`` guarantees every step already has exactly
        one config to write into by this point, so this compiler never
        mints one itself.

        Deliberately conservative: 0 or >1 candidate paths (e.g.
        ``rdfc:Sdsify``'s two writer paths), or 0 or >1 channels on the
        step (a branching producer/consumer), leave the step untouched
        so it must stay explicitly authored — this compiler never
        guesses which branch a step means.
        """
        steps = self.output_reader.select(
            "?step ?component",
            """
            ?step prov:specializationOf ?component .
            ?container tcs:instantiates ?component .
            ?container tcs:instantiates rdfc:Orchestrator .
            ?container tcs:runs ?step .
            """,
        )

        for _, row in steps.iterrows():
            step_id = row["step"]
            component_id = row["component"]

            self._inject_wiring_key(
                step_id,
                component_id,
                channel_pred="tcs:readsFrom",
                shape_class="rdfc:Reader",
            )
            self._inject_wiring_key(
                step_id,
                component_id,
                channel_pred="tcs:writesTo",
                shape_class="rdfc:Writer",
            )

    def _inject_wiring_key(
        self,
        step_id: str,
        component_id: str,
        channel_pred: str,
        shape_class: str,
    ) -> None:
        """Inject ``step_id``'s single ``channel_pred`` channel into its
        config under the component's declared reader/writer path, if
        unambiguous and not already set — see :meth:`describe_channel_wiring`.
        """
        channels = (
            self.output_reader.filter(sub=step_id, pred=channel_pred)
            .df["obj"]
            .to_list()
        )
        if len(channels) != 1:
            # No channel, or an ambiguous branch (>1) — stays explicit.
            return
        channel_id = channels[0]

        path = self._lookup_channel_predicate(component_id, shape_class)
        if path is None:
            # No declared wiring slot, or ambiguous (>1 candidate) —
            # leave to manual authoring.
            return

        configs = (
            self.output_reader.filter(sub=step_id, pred="p-plan:hasInputVar")
            .df["obj"]
            .to_list()
        )
        if len(configs) != 1:
            # PipelineEnricher guarantees at least one; more than one is
            # a modelling error the generic cardinality shape already
            # flags — don't guess which one to use.
            return
        config_id = configs[0]

        # Anchored through config_id (always a named IRI post
        # PipelineSeeder.name_blind_nodes) rather than holding the
        # tcs:embedded blank node directly — a blank node's label isn't
        # stable across separate SPARQL query executions, so it can
        # never be safely stringified into a query.
        if self.output_reader.ask(f"{config_id} tcs:embedded/{path} ?x ."):
            # Already explicit — never overwrite.
            return

        new_triples = self.output_reader.construct(
            f"?embedded {path} {channel_id} .",
            f"{config_id} tcs:embedded ?embedded .",
        ).graph
        self.output_reader = self.output_reader.add(new_triples)

    def _lookup_channel_predicate(
        self, component_id: str, shape_class: str
    ) -> str | None:
        """Return the component's single reader/writer predicate, or ``None``.

        Looks up ``component_id``'s configShape (the same
        ``dcat:qualifiedRelation`` / ``dcat:hadRole tcs:configShape``
        attachment used by ``rdfc:SPARQLIngest``) for ``sh:property``
        entries carrying a channel of the requested direction, and
        returns the single candidate ``sh:path``.

        The direction is read from ``tcs:upstreamClass``, not from
        ``sh:class``. Generated config shapes collapse upstream's
        ``sh:class rdfc:Reader`` / ``rdfc:Writer`` to ``sh:class
        tcs:Channel`` — the toolchain models a channel as one thing and
        only ever asserts that type — and keep the end that was meant on
        ``tcs:upstreamClass``. This is the point where that survives the
        round trip: the annotation is translated back into the
        framework-specific predicate RDF-Connect expects. Both halves
        are required, because ``tcs:upstreamClass`` alone also appears
        on non-channel properties whose foreign class was demoted to an
        annotation (``tm:path`` carries ``rdfl:PathLens``).

        Returns ``None`` if the component declares zero such paths (no
        wiring slot for that role) or more than one (ambiguous — e.g.
        ``rdfc:Sdsify``'s two writer paths) — either way the caller must
        leave the step to manual authoring rather than guess.
        """
        paths = self.output_reader.select(
            "?path",
            f"""
            {component_id} dcat:qualifiedRelation ?rel .
            ?rel dcat:hadRole tcs:configShape .
            ?rel dct:relation ?shape .
            ?shape sh:property ?prop .
            ?prop sh:path ?path ;
                  sh:class tcs:Channel ;
                  tcs:upstreamClass {shape_class} .
            """,
        )["path"].to_list()
        if len(paths) != 1:
            return None
        return paths[0]

    def describe_channels(self) -> None:
        """Emit the ``?channel a rdfc:Reader, rdfc:Writer`` boilerplate.

        Concrete step→channel wiring (``?step rdfc:reader ?channel`` /
        ``?step rdfc:writer ?channel`` / ``?step rdfc:memberStream
        ?channel`` etc.) is the PipelineDefinition author's
        responsibility: it lives in each step's ``tcs:embedded``
        config and is emitted verbatim by :meth:`describe_configs`.
        Only the author knows which framework predicate their step
        expects — the compiler cannot guess it reliably.

        Every ``tcs:Channel`` in the build graph is typed as both
        ``rdfc:Reader`` and ``rdfc:Writer`` so downstream SHACL
        shapes checking ``sh:class rdfc:Reader``/``rdfc:Writer`` on
        catalog configShapes pass on every channel the pipeline
        touches. In the emitted ``pipeline.ttl`` (``rdfc_reader``)
        only channels the RDFC step wiring actually references pick
        up the typing — this keeps ``pipeline.ttl`` free of dead
        declarations for channels other framework compilers care
        about but the RDFC runner never sees.
        """
        channel_types = self.output_reader.construct(
            "?channel a rdfc:Reader, rdfc:Writer .",
            "?channel a tcs:Channel .",
        ).graph
        self.output_reader = self.output_reader.add(channel_types)

        # Restrict the pipeline.ttl-facing typing to channels the RDFC
        # step wiring actually references. Compare in expanded URI
        # form because ``.df`` compaction uses a graph-derived
        # PrefixStore rather than ``self.prefix_store`` (see
        # ``/memories/repo/rdfine-gotchas.md``).
        rdfc_object_uris = {
            str(o) for _, _, o in self.rdfc_reader.graph if isinstance(o, URIRef)
        }
        referenced_types = Graph()
        for s, p, o in channel_types:
            if str(s) in rdfc_object_uris:
                referenced_types.add((s, p, o))
        self.rdfc_reader = self.rdfc_reader.add(referenced_types)

    def serialize_pipeline_ttl(self) -> None:
        """Serialize :attr:`rdfc_reader` to Turtle, stashed on
        :attr:`pipeline_ttl` for :meth:`attach_rdfc_pipeline_file`.
        """
        # Emit ``@base`` as the container path of the pipeline file
        # itself, not the working directory. RDF-Connect's orchestrator
        # (``@rdfc/orchestrator-js`` — ``util.js:readQuads``) only
        # follows ``owl:imports`` triples whose subject IRI exactly
        # matches the file URL it was launched with — for us,
        # ``file://{_RDFC_PIPELINE_CONTAINER_PATH}``. Serializing with
        # the directory as base would emit them on ``<file:///workspace/pipeline/>``
        # instead, and the orchestrator would silently start with no
        # processors registered and exit as soon as it finished loading.
        ttl_string = self.rdfc_reader.graph.serialize(
            format="ttl",
            base=f"file://{_RDFC_PIPELINE_CONTAINER_PATH}",
        )
        # RDF Connect requires the pipeline to be named ``<>``. A plain
        # string replace would also corrupt any other IRI that happens
        # to have the pipeline's compact name as a prefix (e.g. a
        # channel ``demo:TestArchive`` when the pipeline is
        # ``demo:Test``) — the lookahead/lookbehind keep the match
        # anchored to the whole token.
        self.pipeline_ttl = re.sub(
            rf"(?<![\w:]){re.escape(self.pipeline_id)}(?!\w)", "<>", ttl_string
        )

    def attach_rdfc_pipeline_file(self) -> None:
        """Attach :attr:`pipeline_ttl` as ``rdfc/pipeline.ttl`` and
        repoint ``rdfc:Orchestrator``'s compose volume mount at that
        same host path.
        """
        self.output_reader = attach_file(
            self.output_reader,
            filename="pipeline.ttl",
            filepath="rdfc",
            content=self.pipeline_ttl,
        )

        self.output_reader = rewrite_compose_volume_host_path(
            self.output_reader,
            component_iri="rdfc:Orchestrator",
            container_path=_RDFC_PIPELINE_CONTAINER_PATH,
            host_path=_RDFC_PIPELINE_HOST_PATH,
        )
