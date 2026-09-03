from rdflib import Graph
from rdfine import GraphReader, drop_empty, receive_first
import pandas as pd
import re
import yaml

from ..base import Compiler
from ..utils import attach_file, extract_config, prepare_ldio_config, lookup_seeded_pipeline_id


class LdioConfigCompiler(Compiler):
    """Generate one LDIO pipeline YAML per ``tcs:segment``.

    LDIO's Pattern A2 directory-scan orchestrator (set up by
    :attr:`_APPLICATION_YAML`) reads every ``*.yml`` file dropped
    into ``/ldio/pipelines`` as an independent pipeline, so each
    ``tcs:segment`` that contains LDIO steps becomes its own file
    under ``ldio/pipelines/<segment>.yml``. A companion
    ``ldio/application.yml`` points the orchestrator at that
    directory.

    Step order inside each segment YAML is recovered from
    ``tcs:readsFrom`` / ``tcs:writesTo`` walks so LDIO's list-position
    semantics for ``transformers:``/``outputs:`` match the intended
    dataflow. LDIO's singular ``input:`` / ``input.adapter:`` keys
    are enforced per-segment by :class:`tcs:LdioSingularStepShape`.
    """

    _APPLICATION_YAML = "orchestrator:\n  directory: /ldio/pipelines\n"

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        # Intermediate state — populated in ``compile``.
        self.df_steps: pd.DataFrame = pd.DataFrame()
        self.dict_configs: dict = {}
        self.segment_outputs: dict[str, dict] = {}
        self.segment_yamls: dict[str, str] = {}
        self.pipeline_name: str | None = None
        self.pipeline_description: str | None = None

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Fires once a container instantiates the LDIO orchestrator,
        :class:`SegmentTagger` has recorded provenance so every LDIO
        step already carries its ``tcs:segment`` tag, and every LDIO
        step in the container has a ``p-plan:hasInputVar``. The last
        gate defers this compiler until the boundary config compilers
        have finished populating any Bridge-inserted steps — otherwise
        this compiler would emit YAML that silently omits an
        unconfigured Bridge step.
        """
        has_ldio_container = not graph_reader.filter(
            pred="tcs:instantiates",
            obj="ldio:LinkedDataInteractionsOrchestrator",
        ).df.empty
        if not has_ldio_container:
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
                       tcs:instantiates ldio:LinkedDataInteractionsOrchestrator ;
                       tcs:runs ?step .
            ?step a tcs:InstancePipelineComponent ;
                  prov:specializationOf ?comp .
            ?comp ldio:type ?anytype .
            FILTER NOT EXISTS { ?step p-plan:hasInputVar ?c }
            """,
        )
        return unconfigured.empty

    def compile(self) -> Graph:
        self.fetch_steps()
        self.fetch_configs()
        self.lookup_pipeline_metadata()
        self.fill_in_segments()
        self.serialize_segment_yamls()
        self.attach_segment_files()
        self.attach_application_yaml()
        return self.output_reader.graph

    def fetch_steps(self) -> None:
        df_raw = self.output_reader.select(
            "?pipeline_step ?pipeline_component ?segment ?reads_from ?writes_to",
            """
            ?container a tcs:DockerContainer ;
                       tcs:instantiates ldio:LinkedDataInteractionsOrchestrator ;
                       tcs:runs ?pipeline_step .
            ?pipeline_step a tcs:InstancePipelineComponent ;
                           prov:specializationOf ?pipeline_component ;
                           tcs:segment ?segment .
            ?pipeline_component ldio:type ?ldio_type .
            OPTIONAL { ?pipeline_step tcs:readsFrom ?reads_from . }
            OPTIONAL { ?pipeline_step tcs:writesTo ?writes_to . }
            """,
        )
        # Collapse the OPTIONAL cross-join down to one row per step —
        # LDIO steps are single-valued on readsFrom/writesTo, so the
        # first row per (step, component, segment) is sufficient.
        df_raw = df_raw.groupby(
            ["pipeline_step", "pipeline_component", "segment"], as_index=False
        ).first()
        step_to_component = dict(
            zip(df_raw["pipeline_step"], df_raw["pipeline_component"])
        )
        step_to_segment = dict(zip(df_raw["pipeline_step"], df_raw["segment"]))

        list_records = []
        for step_id in self._order_by_channel_chain(df_raw):
            component_id = step_to_component[step_id]
            segment_id = step_to_segment[step_id]

            ldio_label = receive_first(
                self.output_reader.filter(sub=component_id, pred="rdfs:label").df[
                    "obj"
                ],
            )
            ldio_type = receive_first(
                self.output_reader.filter(sub=component_id, pred="ldio:type").df["obj"],
            )

            record = {
                "step": step_id,
                "component": component_id,
                "segment": segment_id,
                "type": ldio_type,
                "name": ldio_label,
            }

            if self.output_reader.ask(f"{step_id} p-plan:hasInputVar ?config ."):
                config_id = receive_first(
                    self.output_reader.filter(
                        sub=step_id, pred="p-plan:hasInputVar"
                    ).df["obj"],
                )
                record["config"] = config_id

            list_records.append(record)

        self.df_steps = pd.DataFrame.from_records(list_records)

    @staticmethod
    def _order_by_channel_chain(df_raw: pd.DataFrame) -> list:
        """Recover execution order by walking ``tcs:readsFrom``/``tcs:writesTo``.

        Starts from every step with no ``tcs:readsFrom`` (a chain's
        entry point) and follows ``writes_to`` -> matching ``reads_from``
        until the chain ends. Steps the walk never reaches are
        appended afterwards in their original query order, so a
        wiring-free chain still compiles instead of silently losing
        steps.
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
        if self.df_steps.empty or "config" not in self.df_steps.columns:
            self.dict_configs = {}
            return
        config_list = [
            c for c in self.df_steps["config"].to_list() if isinstance(c, str)
        ]
        output_dict = {}
        for config_id in config_list:
            config_dict = extract_config(self.output_reader, config_id)
            if isinstance(config_dict, dict):
                config_dict = prepare_ldio_config(
                    self.output_reader.prefix_store, config_dict
                )
            output_dict[config_id] = config_dict
        self.dict_configs = output_dict

    def lookup_pipeline_metadata(self) -> None:
        """Copy ``dct:identifier`` / ``rdfs:comment`` off the seeded plan.

        YAML ``name:`` is the HttpIn URL path; it must be the machine
        slug, not ``rdfs:label`` (spaces). Missing identifier falls
        back to the segment id in :meth:`fill_in_segments`.
        """
        plan = lookup_seeded_pipeline_id(self.output_reader)
        ident_rows = self.output_reader.filter(sub=plan, pred="dct:identifier").df
        self.pipeline_name = (
            str(ident_rows["obj"].iloc[0]) if not ident_rows.empty else None
        )
        comment_rows = self.output_reader.filter(sub=plan, pred="rdfs:comment").df
        if comment_rows.empty:
            self.pipeline_description = None
        else:
            self.pipeline_description = str(comment_rows["obj"].iloc[0]).strip()

    def fill_in_segments(self) -> None:
        """Group steps by ``tcs:segment`` and lay each segment's steps
        into an LDIO pipeline dict on :attr:`segment_outputs`.
        """
        outputs: dict[str, dict] = {}
        if self.df_steps.empty:
            self.segment_outputs = outputs
            return
        segment_ids = list(dict.fromkeys(self.df_steps["segment"].to_list()))
        for _, row in self.df_steps.iterrows():
            segment_id = row["segment"]
            segment = outputs.setdefault(
                segment_id,
                {
                    "name": self._segment_pipeline_name(segment_id, len(segment_ids)),
                    **(
                        {"description": self.pipeline_description}
                        if self.pipeline_description
                        else {}
                    ),
                    "input": {"adapter": {}},
                    "transformers": [],
                    "outputs": [],
                },
            )
            config_id = row.get("config")
            processor = {
                "name": row["name"],
                "config": self.dict_configs.get(config_id),
            }
            processor_type = row["type"]
            if processor_type == "Input":
                segment["input"].update(processor)
            elif processor_type == "Adapter":
                segment["input"]["adapter"].update(processor)
            elif processor_type == "Transformer":
                segment["transformers"].append(processor)
            elif processor_type == "Output":
                segment["outputs"].append(processor)
        self.segment_outputs = outputs

    def serialize_segment_yamls(self) -> None:
        """Drop empty keys and render each segment as YAML, stashed on
        :attr:`segment_yamls` for :meth:`attach_segment_files`.
        """
        self.segment_yamls = {
            segment_id: yaml.dump(drop_empty(body), sort_keys=False)
            for segment_id, body in self.segment_outputs.items()
        }

    def attach_segment_files(self) -> None:
        for segment_id, config_yaml in self.segment_yamls.items():
            self.output_reader = attach_file(
                self.output_reader,
                filename=f"{self.segment_outputs[segment_id]['name']}.yml",
                filepath="ldio/pipelines",
                content=config_yaml,
            )

    def attach_application_yaml(self) -> None:
        if not self.segment_yamls:
            return
        self.output_reader = attach_file(
            self.output_reader,
            filename="application.yml",
            filepath="ldio",
            content=self._APPLICATION_YAML,
        )

    def _segment_pipeline_name(self, segment_id: str, segment_count: int) -> str:
        if self.pipeline_name and segment_count == 1:
            return self.pipeline_name
        slug = self._derive_pipeline_name(segment_id)
        if self.pipeline_name:
            return f"{self.pipeline_name}-{slug}"
        return slug

    @staticmethod
    def _derive_pipeline_name(segment_id: str) -> str:
        local = segment_id.rsplit(":", 1)[-1].rsplit("/", 1)[-1]
        return re.sub(r"[^a-zA-Z0-9_-]+", "_", local).strip("_") or "segment"
