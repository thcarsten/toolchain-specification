from rdflib import Graph
from rdfine import GraphReader, drop_empty, receive_first
import pandas as pd
import yaml

from ..base import Compiler
from ..utils import attach_file, extract_config, rewrite_compose_volume_host_path

# The LDIO pipeline-starter service curls the config file at this
# fixed container path. Kept next to the host-side output location
# so the pairing is visible in one place.
_LDIO_STARTER_CONTAINER_PATH = "/pipeline.yml"
_LDIO_STARTER_HOST_PATH = "./ldio/config.yml"


class LdioConfigCompiler(Compiler):
    """
    Class to generate config file for LDIO.

    Also patches the ``tcs:DockerComposeConfig`` on
    ``ldio:LdioPipelineStarterService`` so its volume mount for the
    pipeline file points at ``./ldio/config.yml`` — the host
    location this compiler actually emits to. Doing that here
    rather than in :class:`DockerComposeCompiler` keeps the
    aggregator generic: each framework compiler shapes its own
    compose fragment, then the aggregator merges them.
    """

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        # Intermediate state — populated in ``compile``.
        self.df_steps: pd.DataFrame = pd.DataFrame()
        self.dict_configs: dict = {}
        self.output: dict = {}
        self.config_yaml: str = ""

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Triggered when a container instantiates the LDIO orchestrator."""
        return not graph_reader.filter(
            pred="tcs:instantiates",
            obj="ldio:LinkedDataInteractionsOrchestrator",
        ).df.empty

    def compile(self) -> Graph:
        self.initialize_output()
        self.fetch_steps()
        self.fetch_configs()
        self.fill_in_components()
        self.serialize_config_yaml()
        self.attach_ldio_config_file()
        return self.output_reader.graph

    def initialize_output(self) -> None:
        """Seed the empty LDIO pipeline-YAML skeleton on :attr:`output`."""
        self.output = {
            "name": "",
            "description": "",
            "input": {"adapter": {}},
            "transformers": [],
            "outputs": [],
        }

    def fetch_steps(self) -> None:

        # One row per LDIO *step instance* — not per catalog component,
        # so two steps specializing the same component (e.g. two
        # Ldio:SparqlConstructTransformer entries) each keep their own
        # config instead of collapsing onto a shared component-level slot.
        # ``reads_from``/``writes_to`` are pulled in the same query so
        # execution order can be recovered afterwards — LDIO's YAML has
        # no other way to express it than list position.
        where_statement = """
        ?container a tcs:DockerContainer .
        ?container tcs:instantiates ldio:LinkedDataInteractionsOrchestrator .
        ?container tcs:instantiates ?pipeline_component .
        ?container tcs:runs ?pipeline_step .
        ?pipeline_step prov:specializationOf ?pipeline_component .
        OPTIONAL { ?pipeline_step tcs:readsFrom ?reads_from . }
        OPTIONAL { ?pipeline_step tcs:writesTo ?writes_to . }
        """

        df_raw = self.output_reader.select(
            "?pipeline_step ?pipeline_component ?reads_from ?writes_to",
            where_statement,
        )
        # A step's channel predicates are single-valued in the LDIO
        # segment (a strictly serial chain); collapse any duplicate rows
        # the OPTIONALs may have introduced down to one row per step.
        df_raw = df_raw.groupby(
            ["pipeline_step", "pipeline_component"], as_index=False
        ).first()
        step_to_component = dict(
            zip(df_raw["pipeline_step"], df_raw["pipeline_component"])
        )

        list_records = []
        for step_id in self._order_by_channel_chain(df_raw):
            component_id = step_to_component[step_id]

            ldio_label = receive_first(
                self.output_reader.filter(sub=component_id, pred="rdfs:label").df[
                    "obj"
                ],
            )
            ldio_type = receive_first(
                self.output_reader.filter(sub=component_id, pred="ldio:type").df["obj"],
            )

            dict_component = {
                "component": component_id,
                "type": ldio_type,
                "name": ldio_label,
            }

            # Each step's own config, read directly off the step instance
            # (never off the shared catalog component).
            if self.output_reader.ask(f"{step_id} p-plan:hasInputVar ?config ."):
                config_id = receive_first(
                    self.output_reader.filter(
                        sub=step_id, pred="p-plan:hasInputVar"
                    ).df["obj"],
                )
                dict_component.update({"config": config_id})

            list_records += [dict_component]

        self.df_steps = pd.DataFrame.from_records(list_records)

    @staticmethod
    def _order_by_channel_chain(df_raw: pd.DataFrame) -> list:
        """Recover execution order by walking ``tcs:readsFrom``/``tcs:writesTo``.

        Starts from every step with no ``tcs:readsFrom`` (a segment's
        entry point) and follows ``writes_to`` -> matching ``reads_from``
        until the chain ends. Steps the walk never reaches (missing or
        partial channel wiring) are appended afterwards in their
        original query order, so an LDIO segment that doesn't use
        channel wiring at all still compiles exactly as before instead
        of silently losing steps.
        """
        channel_to_reader = {
            row["reads_from"]: row["pipeline_step"]
            for _, row in df_raw.iterrows()
            if pd.notna(row["reads_from"])
        }
        writes_to = {
            row["pipeline_step"]: row["writes_to"]
            for _, row in df_raw.iterrows()
            if pd.notna(row["writes_to"])
        }
        has_reads_from = set(df_raw.loc[df_raw["reads_from"].notna(), "pipeline_step"])

        ordered: list = []
        visited: set = set()
        entry_points = [s for s in df_raw["pipeline_step"] if s not in has_reads_from]
        for start in entry_points:
            current = start
            while current is not None and current not in visited:
                ordered.append(current)
                visited.add(current)
                current = channel_to_reader.get(writes_to.get(current))

        for step_id in df_raw["pipeline_step"]:
            if step_id not in visited:
                ordered.append(step_id)
                visited.add(step_id)

        return ordered

    def fetch_configs(self) -> None:

        config_list = self.df_steps["config"].to_list()
        config_list = [config for config in config_list if isinstance(config, str)]

        output_dict = {}
        for config_id in config_list:
            config_dict = extract_config(self.output_reader, config_id)
            config_dict = self.output_reader.prefix_store.drop(config_dict)
            output_dict.update({config_id: config_dict})
        self.dict_configs = output_dict

    def fill_in_components(self) -> None:
        """
        Fills in the components into the output dictionary
        """

        for index, row in self.df_steps.iterrows():
            config_id = row["config"]
            dict_processor = {
                "name": row["name"],
                "config": self.dict_configs.get(config_id),
            }
            processor_type = row["type"]
            if processor_type == "Input":
                self.output["input"].update(dict_processor)
            elif processor_type == "Adapter":
                self.output["input"]["adapter"].update(dict_processor)
            elif processor_type == "Transformer":
                self.output["transformers"].append(dict_processor)
            elif processor_type == "Output":
                self.output["outputs"].append(dict_processor)

    def serialize_config_yaml(self) -> None:
        """Drop empty keys from :attr:`output` and render it as YAML,
        stashed on :attr:`config_yaml` for :meth:`attach_ldio_config_file`.
        """
        self.output = drop_empty(self.output)
        self.config_yaml = yaml.dump(self.output, sort_keys=False)

    def attach_ldio_config_file(self) -> None:
        """Attach :attr:`config_yaml` as ``ldio/config.yml`` and repoint
        ``ldio:LdioPipelineStarterService``'s compose volume mount at
        that same host path (it curls the file from inside its
        container at :data:`_LDIO_STARTER_CONTAINER_PATH`).
        """
        self.output_reader = attach_file(
            self.output_reader,
            filename="config.yml",
            filepath="ldio",
            content=self.config_yaml,
        )

        self.output_reader = rewrite_compose_volume_host_path(
            self.output_reader,
            component_iri="ldio:LdioPipelineStarterService",
            container_path=_LDIO_STARTER_CONTAINER_PATH,
            host_path=_LDIO_STARTER_HOST_PATH,
        )
