from rdflib import Graph
from rdfine import GraphReader, GraphDict, receive_first
import pandas as pd

from .base import Compiler
from .utils import attach_file, extract_config, rewrite_compose_volume_host_path

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
        self.df_channel: pd.DataFrame = pd.DataFrame()

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Triggered when a container instantiates the RDF-Connect orchestrator."""
        return not graph_reader.filter(
            pred="tcs:instantiates", obj="rdfc:Orchestrator"
        ).df.empty

    def compile(self) -> Graph:
        self.rdfc_reader = self.input_reader.construct(
            "?pipeline a rdfc:Pipeline .",
            "?pipeline a tcs:PipelineDefinition",
        )
        self.pipeline_id = receive_first(
            self.input_reader.filter(pred="rdf:type", obj="tcs:PipelineDefinition").df[
                "sub"
            ],
        )

        self.describe_pipeline()
        self.describe_processors()
        self.describe_configs()
        self.df_channel = self.create_channel_overview()
        self.describe_channels()

        ttl_string = self.rdfc_reader.serialize("ttl")
        # RDF Connect requires the pipeline to be named ``<>``.
        ttl_string = ttl_string.replace(self.pipeline_id, "<>")

        self.output_reader = attach_file(
            self.output_reader,
            filename="pipeline.ttl",
            filepath="rdfc",
            content=ttl_string,
        )

        self.output_reader = rewrite_compose_volume_host_path(
            self.output_reader,
            component_iri="rdfc:Orchestrator",
            container_path=_RDFC_PIPELINE_CONTAINER_PATH,
            host_path=_RDFC_PIPELINE_HOST_PATH,
        )
        return self.output_reader.graph

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
        config_df = self.input_reader.filter(
            sub=step_list, pred="p-plan:hasInputVar"
        ).df
        config_list = config_df["obj"].to_list()

        for config_id in config_list:
            step_id = receive_first(
                self.input_reader.filter(
                    sub=step_list, pred="p-plan:hasInputVar", obj=config_id
                ).df["sub"],
            )
            config_dict = extract_config(self.input_reader, config_id)
            config_dict["@id"] = step_id
            config_graph = GraphDict(
                config_dict, prefix_store=self.input_reader.prefix_store
            ).graph
            self.rdfc_reader = self.rdfc_reader.add(config_graph)

    def create_channel_overview(self) -> pd.DataFrame:
        """Build the per-channel DataFrame consumed by ``describe_channels``.

        Thin wrapper that delegates to three focused helpers:

        1. :meth:`_create_channels` — fetch the ``(step, prev_step,
           component, prev_component)`` skeleton via SPARQL. A row
           with ``prev_step`` NaN represents a step with no
           predecessor and never becomes a channel.
        2. :meth:`_sort_channels_by_flow` — topologically order the
           rows so ``channel_id`` numbering follows pipeline flow.
        3. :meth:`_lookup_channel_predicates` — resolve each side's
           ``:reader_predicate`` / ``:writer_predicate`` from the
           catalog, falling back to ``:in`` / ``:out`` when the
           lookup is missing or ambiguous.

        The output columns are ``step``, ``prev_step``, ``component``,
        ``prev_component``, ``input_predicate``, ``output_predicate``
        and ``channel_id``.
        """
        df = self._create_channels()
        df = self._sort_channels_by_flow(df)
        df = self._lookup_channel_predicates(df)
        df["channel_id"] = ":channel_" + df.index.astype(str)
        return df

    def _create_channels(self) -> pd.DataFrame:
        """Return one row per ``(step, prev_step, component, prev_component)``.

        Phantom rows (``prev_step`` NaN) are kept so the topological
        sort has the full step set to reason about.

        ``SELECT DISTINCT`` guards against the query multiplying
        rows when more than one witness container satisfies the
        ``?container tcs:instantiates ?component ; tcs:runs ?step``
        join: channels are defined by their step / component tuple,
        not by the container that happens to run them.
        """
        return self.input_reader.select(
            "DISTINCT ?step ?prev_step ?component ?prev_component",
            """
                ?step prov:specializationOf ?component .
                ?container tcs:instantiates rdfc:Orchestrator .
                ?container tcs:instantiates ?component .
                ?container tcs:runs ?step .
                OPTIONAL {
                    ?step p-plan:isPrecededBy ?prev_step .
                    ?container tcs:runs ?prev_step .
                    ?prev_step prov:specializationOf ?prev_component .
                }
            """,
        )

    def _sort_channels_by_flow(self, df: pd.DataFrame) -> pd.DataFrame:
        """Reorder ``df`` so ``channel_id`` numbering mimics pipeline flow.

        Computes a topological rank per step from the
        ``p-plan:isPrecededBy`` relations already carried in
        ``prev_step`` — Kahn-style, with alphabetical tie-breaking
        for determinism and a bail-out for cyclic remainders.
        Sorting by the sender's rank (then receiver's) puts earlier
        edges before later ones; phantom rows sort to the top and
        never get emitted.
        """
        all_steps = set(df["step"].dropna()) | set(df["prev_step"].dropna())
        predecessors = (
            df.dropna(subset=["prev_step"])
            .groupby("step")["prev_step"]
            .apply(set)
            .to_dict()
        )
        step_position: dict[str, int] = {}
        remaining = set(all_steps)
        rank = 0
        while remaining:
            ready = {s for s in remaining if not predecessors.get(s, set()) & remaining}
            if not ready:  # cycle — bail out deterministically
                ready = remaining
            for step in sorted(ready):
                step_position[step] = rank
                rank += 1
            remaining -= ready

        return (
            df.assign(
                _prev_pos=df["prev_step"].map(step_position),
                _step_pos=df["step"].map(step_position),
            )
            .sort_values(["_prev_pos", "_step_pos"], na_position="first")
            .drop(columns=["_prev_pos", "_step_pos"])
            .reset_index(drop=True)
        )

    def _lookup_channel_predicates(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add ``input_predicate`` / ``output_predicate`` columns to ``df``.

        Each side's predicate is looked up from the catalog on the
        respective component (``:reader_predicate`` for the receiving
        step, ``:writer_predicate`` for the sending step). If the
        lookup returns zero matches (missing annotation) or two-plus
        distinct matches (ambiguity — e.g. branching), the compiler
        falls back to the generic ``:in`` / ``:out`` placeholders so
        a partially-annotated catalog still produces a working file.
        """

        def _resolve(component, predicate: str, fallback: str) -> str:
            if pd.isna(component):
                return fallback
            matches = {
                v
                for v in self.input_reader.filter(sub=component, pred=predicate).df[
                    "obj"
                ]
                if pd.notna(v)
            }
            return next(iter(matches)) if len(matches) == 1 else fallback

        df["input_predicate"] = df["component"].map(
            lambda c: _resolve(c, ":reader_predicate", ":in")
        )
        df["output_predicate"] = df["prev_component"].map(
            lambda c: _resolve(c, ":writer_predicate", ":out")
        )
        return df

    def describe_channels(self) -> None:

        df_channels = self.df_channel.loc[self.df_channel["prev_step"].notna()]

        for index, row in df_channels.iterrows():
            # Grabbing the necessary references
            step = row["step"]
            prev_step = row["prev_step"]
            component = row["component"]
            channel_id = row["channel_id"]
            input_predicate = row["input_predicate"]
            output_predicate = row["output_predicate"]

            # Adding the relevant triples to the output graph
            channel_reader = self.rdfc_reader.construct(
                f"""
                    {channel_id} a rdfc:Reader, rdfc:Writer .
                    {prev_step} {output_predicate} {channel_id} .
                    {step} {input_predicate} {channel_id} . """,
                "",
            )

            # Appending the output graph
            self.rdfc_reader = self.rdfc_reader.add(channel_reader.graph)
