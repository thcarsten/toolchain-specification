import json
from uuid import NAMESPACE_URL, uuid5

from rdflib import Graph
from rdfine import GraphReader, receive_first
import pandas as pd

from .base import Compiler
from .utils import attach_file

from typing import Any


_PROCESSOR_X_SPACING = 400.0

class NifiConfigCompiler(Compiler):
    """
    Class to generate config file for Nifi
    """

    def __init__(self, graph: Graph) -> None:
            super().__init__(graph)
            # Intermediate state — populated in ``compile``.
            self.df_steps: pd.DataFrame = pd.DataFrame()
            self.dict_configs: dict = {}
            self.output: dict = {}

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Triggered when a container instantiates the Nifi orchestrator."""
        return not graph_reader.filter(
            pred="tcs:instantiates",
            obj="nifi:Orchestrator",
        ).df.empty

    def compile(self) -> Graph:
        expected_steps = self.fetch_expected_steps()
        self.df_steps = self.fetch_steps()
        funnels = self.fetch_funnels()

        if expected_steps.empty:
            raise ValueError("NiFi container exists but runs no pipeline steps")

        implemented_steps = pd.concat(
            [self.df_steps[["step"]], funnels[["step"]]], ignore_index=True
        )
        missing_steps = expected_steps.loc[
            ~expected_steps["step"].isin(implemented_steps["step"])
        ]
        if not missing_steps.empty:
            components = ", ".join(missing_steps["component"].astype(str))
            raise ValueError(
                "NiFi steps have incomplete processor metadata in the catalog: "
                f"{components}"
            )

        duplicate_steps = self.df_steps.loc[
            self.df_steps["step"].duplicated(keep=False)
        ]
        if not duplicate_steps.empty:
            steps = ", ".join(sorted(set(duplicate_steps["step"].astype(str))))
            raise ValueError(f"NiFi steps must have at most one configuration: {steps}")

        ordered_steps = sorted(expected_steps["step"].astype(str))
        positions = {step: index for index, step in enumerate(ordered_steps)}
        self.df_steps = self.df_steps.sort_values("step").reset_index(drop=True)
        funnels = funnels.sort_values("step").reset_index(drop=True)
        pipeline_id = receive_first(
            self.output_reader.filter(
                pred="rdf:type", obj="tcs:PipelineDefinition"
            ).df["sub"]
        )
        group_id = nifi_id(f"{pipeline_id}:group")

        processors = [
             self.build_processor(row, positions[str(row.step)], group_id)
             for row in self.df_steps.itertuples(index=False)
        ]
        funnel_objects = [
            self.build_funnel(row.step, positions[str(row.step)], group_id)
            for row in funnels.itertuples(index=False)
        ]

        component_types = {
            **{str(step): "PROCESSOR" for step in self.df_steps["step"]},
            **{str(step): "FUNNEL" for step in funnels["step"]},
        }
        connections = self.build_connections(group_id, component_types)
        for processor in processors:
            connected_relationships = {
                relationship
                for connection in connections
                if connection["source"]["id"] == processor["identifier"]
                for relationship in connection["selectedRelationships"]
            }
            processor["autoTerminatedRelationships"] = [
                relationship
                for relationship in processor["autoTerminatedRelationships"]
                if relationship not in connected_relationships
            ]

        self.output = self.build_flow(
            pipeline_id,
            group_id,
            processors,
            funnel_objects,
            connections,
        )

        self.output_reader = attach_file(
             self.output_reader,
             filename="flow.json",
             filepath="nifi",
             content=json.dumps(self.output, indent=4),
        )

        return self.output_reader.graph

    def fetch_expected_steps(self) -> pd.DataFrame:
        """Return every step assigned to a NiFi container.

        This deliberately does not require NiFi-specific component metadata,
        so ``compile`` can report incomplete catalog entries instead of
        silently dropping them from the generated flow.
        """
        return self.output_reader.select(
            "DISTINCT ?step ?component",
            """
            ?container a tcs:DockerContainer ;
                tcs:instantiates nifi:Orchestrator ;
                tcs:runs ?step .
            ?step prov:specializationOf ?component .
            """,
        )

    def fetch_steps(self):
        return self.output_reader.select(
            "?step ?component ?processor_type ?bundle_group "
            "?bundle_artifact ?bundle_version ?config "
            "?scheduled_state ?scheduling_period",
            """
            ?container a tcs:DockerContainer ;
                tcs:instantiates nifi:Orchestrator ;
                tcs:runs ?step .

            ?step prov:specializationOf ?component .

            ?component nifi:processorType ?processor_type ;
                nifi:bundleGroup ?bundle_group ;
                nifi:bundleArtifact ?bundle_artifact ;
                nifi:bundleVersion ?bundle_version .

            OPTIONAL {
                ?step p-plan:hasInputVar ?config .
            }
            OPTIONAL {
                ?step nifi:scheduledState ?scheduled_state .
            }
            OPTIONAL {
                ?step nifi:schedulingPeriod ?scheduling_period .
            }
            """,
        )    

    def fetch_funnels(self) -> pd.DataFrame:
        return self.output_reader.select(
            "?step ?component",
            """
            ?container a tcs:DockerContainer ;
                tcs:instantiates nifi:Orchestrator ;
                tcs:runs ?step .

            ?step prov:specializationOf ?component .
            ?component nifi:componentType "FUNNEL" .
            """,
        )

    def build_processor(self, row, index: int, group_id: str) -> dict:
        properties = self.fetch_properties(row)
        processor_id = nifi_id(str(row.step))
        scheduled_state = (
            "ENABLED" if pd.isna(row.scheduled_state) else str(row.scheduled_state)
        )
        if scheduled_state not in {"DISABLED", "ENABLED", "RUNNING"}:
            raise ValueError(
                f"NiFi step {row.step} has invalid nifi:scheduledState "
                f"{scheduled_state!r}"
            )
        scheduling_period = (
            "60 sec" if pd.isna(row.scheduling_period) else str(row.scheduling_period)
        )

        return {
            "identifier": processor_id,
            "instanceIdentifier": nifi_id(f"{row.step}:instance"),
            "name": str(row.step).removeprefix(":"),
            "comments": "",
            "position": {
                "x": index * _PROCESSOR_X_SPACING,
                "y": 0.0,
            },
            "type": row.processor_type,
            "bundle": {
                "group": row.bundle_group,
                "artifact": row.bundle_artifact,
                "version": row.bundle_version,
            },
            "properties": properties,
            "propertyDescriptors": {
                name: {
                    "name": name,
                    "displayName": name,
                    "identifiesControllerService": False,
                    "sensitive": False,
                    "dynamic": False,
                }
                for name in properties
            },
            "style": {},
            "schedulingPeriod": scheduling_period,
            "schedulingStrategy": "TIMER_DRIVEN",
            "executionNode": "ALL",
            "penaltyDuration": "30 sec",
            "yieldDuration": "1 sec",
            "bulletinLevel": "WARN",
            "runDurationMillis": 0,
            "concurrentlySchedulableTaskCount": 1,
            "autoTerminatedRelationships": self.fetch_auto_terminated_relationships(
                row.component
            ),
            "scheduledState": scheduled_state,
            "retryCount": 10,
            "retriedRelationships": [],
            "backoffMechanism": "PENALIZE_FLOWFILE",
            "maxBackoffPeriod": "10 mins",
            "componentType": "PROCESSOR",
            "groupIdentifier": group_id,
        }

    def build_funnel(self, step: Any, index: int, group_id: str) -> dict:
        return {
            "identifier": nifi_id(str(step)),
            "instanceIdentifier": nifi_id(f"{step}:instance"),
            "position": {
                "x": index * _PROCESSOR_X_SPACING,
                "y": 0.0,
            },
            "componentType": "FUNNEL",
            "groupIdentifier": group_id,
        }

    def build_connections(
        self,
        group_id: str,
        component_types: dict[str, str],
    ) -> list[dict]:
        rows = self.output_reader.select(
            "?source ?destination ?channel ?source_component "
            "?default_relationship ?selected_relationship ?available_relationship",
            """
            ?container a tcs:DockerContainer ;
                tcs:instantiates nifi:Orchestrator ;
                tcs:runs ?source, ?destination .

            ?source tcs:writesTo ?channel ;
                prov:specializationOf ?source_component .
            ?destination tcs:readsFrom ?channel .

            OPTIONAL {
                ?source_component nifi:outgoingRelationship ?default_relationship .
            }
            OPTIONAL {
                ?channel nifi:selectedRelationship ?selected_relationship .
            }
            OPTIONAL {
                ?source_component
                    (nifi:outgoingRelationship|nifi:autoTerminatedRelationship)
                    ?available_relationship .
            }

            FILTER (?source != ?destination)
            """,
        )
        if rows.empty:
            return []

        connections = []
        for (source, destination, channel), group in rows.groupby(
            ["source", "destination", "channel"], sort=True, dropna=False
        ):
            source = str(source)
            destination = str(destination)
            channel = str(channel)
            source_type = component_types[source]
            selected_relationships = set(
                group["selected_relationship"].dropna().astype(str)
            )
            available_relationships = set(
                group["available_relationship"].dropna().astype(str)
            )
            unknown_relationships = selected_relationships - available_relationships
            if unknown_relationships:
                unknown = ", ".join(sorted(unknown_relationships))
                raise ValueError(
                    f"NiFi channel {channel} selects relationships not declared "
                    f"by {group.iloc[0]['source_component']}: {unknown}"
                )

            relationships = sorted(
                selected_relationships
                or set(group["default_relationship"].dropna().astype(str))
            )
            if source_type == "PROCESSOR" and not relationships:
                raise ValueError(
                    f"NiFi processor {source} writes to {channel} but its "
                    "catalog component has no nifi:outgoingRelationship"
                )

            connection_key = f"{source}:{channel}:{destination}"
            connections.append(
                {
                    "identifier": nifi_id(connection_key),
                    "instanceIdentifier": nifi_id(f"{connection_key}:instance"),
                    "name": channel.removeprefix(":"),
                    "source": {
                        "id": nifi_id(source),
                        "type": source_type,
                        "groupId": group_id,
                    },
                    "destination": {
                        "id": nifi_id(destination),
                        "type": component_types[destination],
                        "groupId": group_id,
                    },
                    "labelIndex": 1,
                    "zIndex": 0,
                    "selectedRelationships": relationships,
                    "backPressureObjectThreshold": 10000,
                    "backPressureDataSizeThreshold": "1 GB",
                    "flowFileExpiration": "0 sec",
                    "prioritizers": [],
                    "bends": [],
                    "loadBalanceStrategy": "DO_NOT_LOAD_BALANCE",
                    "partitioningAttribute": "",
                    "loadBalanceCompression": "DO_NOT_COMPRESS",
                    "componentType": "CONNECTION",
                    "groupIdentifier": group_id,
                }
            )

        return connections

    def fetch_properties(self, row) -> dict:
        if pd.isna(row.config):
            return {}

        properties = self.output_reader.select(
            "?predicate ?value ?property_name",
            f"""
            {row.config} tcs:embedded ?embedded .
            ?embedded ?predicate ?value .

            OPTIONAL {{
                {row.component} dcat:qualifiedRelation ?relationship .
                ?relationship dcat:hadRole "configShape" ;
                    dct:relation ?shape .

                ?shape sh:property ?property_shape .
                ?property_shape sh:path ?predicate ;
                    nifi:propertyName ?property_name .
            }}

            FILTER (?predicate != rdf:type)
            """,
        )

        unmapped = properties[properties["property_name"].isna()]
        if not unmapped.empty:
            predicates = ", ".join(unmapped["predicate"].astype(str))
            raise ValueError(
                f"NiFi component {row.component} has unmapped "
                f"configuration properties: {predicates}"
            )

        duplicate_properties = properties.loc[
            properties["property_name"].duplicated(keep=False)
        ]
        if not duplicate_properties.empty:
            names = ", ".join(
                sorted(set(duplicate_properties["property_name"].astype(str)))
            )
            raise ValueError(
                f"NiFi step {row.step} assigns properties more than once: {names}"
            )

        return dict(zip(properties["property_name"], properties["value"]))

    def fetch_auto_terminated_relationships(self, component: str) -> list[str]:
        relationships = self.output_reader.filter(
            sub=component,
            pred="nifi:autoTerminatedRelationship",
        ).df
        if relationships.empty:
            return []
        return sorted(relationships["obj"].astype(str).to_list())

    def build_flow(
        self,
        pipeline_id: str,
        group_id: str,
        processors: list[dict],
        funnels: list[dict],
        connections: list[dict],
    ) -> dict:
        labels = self.output_reader.filter(sub=pipeline_id, pred="rdfs:label").df
        comments = self.output_reader.filter(sub=pipeline_id, pred="rdfs:comment").df
        name = (
            str(labels.iloc[0]["obj"])
            if not labels.empty
            else pipeline_id.removeprefix(":")
        )
        comment = str(comments.iloc[0]["obj"]) if not comments.empty else ""

        return {
            "maxTimerDrivenThreadCount": 10,
            "rootGroup": {
                "identifier": group_id,
                "instanceIdentifier": nifi_id(f"{pipeline_id}:group:instance"),
                "name": name,
                "comments": comment,
                "position": {"x": 0.0, "y": 0.0},
                "processGroups": [],
                "remoteProcessGroups": [],
                "processors": processors,
                "inputPorts": [],
                "outputPorts": [],
                "connections": connections,
                "labels": [],
                "funnels": funnels,
                "controllerServices": [],
                "defaultFlowFileExpiration": "0 sec",
                "defaultBackPressureObjectThreshold": 10000,
                "defaultBackPressureDataSizeThreshold": "1 GB",
                "scheduledState": "ENABLED",
                "executionEngine": "INHERITED",
                "maxConcurrentTasks": 1,
                "statelessFlowTimeout": "1 min",
                "flowFileConcurrency": "UNBOUNDED",
                "flowFileOutboundPolicy": "STREAM_WHEN_AVAILABLE",
                "componentType": "PROCESS_GROUP",
            },
            "externalControllerServices": {},
            "parameterContexts": [],
            "flowEncodingVersion": "1.0",
            "parameterProviders": [],
            "latest": False,
        }

def nifi_id(value: str) -> str:
    return str(uuid5(NAMESPACE_URL, value))
