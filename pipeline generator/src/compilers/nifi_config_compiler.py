"""NiFi configuration compiler.

Translates the NiFi slice of a ``tcs:PipelineBuild`` graph into a persisted
NiFi 2 ``flow.json``, attached as an ``spdx:File`` under ``nifi/``.

Authoring model (Turtle → NiFi)
-------------------------------
* **Topology** — ``tcs:writesTo`` / ``tcs:readsFrom`` on steps name the
  channels that become NiFi connections.
* **Relationships** — which NiFi relationship feeds a channel is authored
  on the *writer* step as ``tcs:embedded`` / ``nifi:route`` (not on the
  Channel resource). Readers only declare ``tcs:readsFrom``.
* **Properties** — predicates in ``tcs:embedded`` are renamed via
  ``nifi:propertyName`` on the component's ``configShape``. Only authored
  keys are emitted; NiFi fills the rest from the NAR at load time.
* **Controller services** — plan steps like processors/funnels
  (``tcs:InstancePipelineComponent`` + ``tcs:runs``), referenced from
  processor configs by step IRI.
"""

import json
import re
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from rdflib import Graph, Literal, URIRef
from rdfine import GraphReader, receive_first
import pandas as pd

from .base import Compiler
from .utils import attach_file, parse_docker_compose_config

_PROCESSOR_X_SPACING = 650.0
_PROCESSOR_Y_SPACING = 250.0

# Shared by every NiFi step fetch (processors, funnels, controller services).
_NIFI_RUNS_STEP = """
?container a tcs:DockerContainer ;
    tcs:instantiates nifi:Orchestrator ;
    tcs:runs ?step .
?step prov:specializationOf ?component .
"""


class NifiConfigCompiler(Compiler):
    """Compile a NiFi ``nifi/flow.json`` from the pipeline-build graph.

    Intermediate state on the instance (filled during :meth:`compile`):
    * ``df_steps`` — processor rows (type, bundle, config, scheduling).
    * ``output`` — the assembled flow.json dict before attach.
    """

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        # Intermediate state — populated in ``compile``.
        self.df_steps: pd.DataFrame = pd.DataFrame()
        self.output: dict = {}

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """True when a container instantiates ``nifi:Orchestrator``."""
        return not graph_reader.filter(
            pred="tcs:instantiates",
            obj="nifi:Orchestrator",
        ).df.empty

    def compile(self) -> Graph:
        """Fetch > plan edges > layout > emit JSON > attach ``flow.json``."""
        expected_steps = self.fetch_expected_steps()
        self.df_steps = self.fetch_steps()
        funnels = self.fetch_funnels()
        controller_services = self.fetch_controller_services()

        if expected_steps.empty:
            raise ValueError("NiFi container exists but runs no pipeline steps")

        # Every tcs:runs step must resolve as processor, funnel, or CS.
        implemented_steps = pd.concat(
            [
                self.df_steps[["step"]],
                funnels[["step"]],
                controller_services[["step"]],
            ],
            ignore_index=True,
        )
        missing_steps = expected_steps.loc[
            ~expected_steps["step"].isin(implemented_steps["step"])
        ]
        if not missing_steps.empty:
            components = ", ".join(missing_steps["component"].astype(str))
            raise ValueError(
                "NiFi steps have incomplete NiFi catalog metadata: "
                f"{components}"
            )

        duplicate_steps = self.df_steps.loc[
            self.df_steps["step"].duplicated(keep=False)
        ]
        if not duplicate_steps.empty:
            steps = ", ".join(sorted(set(duplicate_steps["step"].astype(str))))
            raise ValueError(f"NiFi steps must have at most one configuration: {steps}")

        duplicate_services = controller_services.loc[
            controller_services["step"].duplicated(keep=False)
        ]
        if not duplicate_services.empty:
            services = ", ".join(
                sorted(set(duplicate_services["step"].astype(str)))
            )
            raise ValueError(
                f"NiFi controller services must have at most one configuration: {services}"
            )

        self.df_steps = self.df_steps.sort_values("step").reset_index(drop=True)
        funnels = funnels.sort_values("step").reset_index(drop=True)
        pipeline_id = receive_first(
            self.output_reader.filter(
                pred="rdf:type", obj="tcs:PipelineDefinition"
            ).df["sub"]
        )
        group_id = nifi_id(f"{pipeline_id}:group")

        # Connection endpoints are processors and funnels only (not CS).
        component_types = {
            **{str(step): "PROCESSOR" for step in self.df_steps["step"]},
            **{str(step): "FUNNEL" for step in funnels["step"]},
        }
        connection_plans = self.plan_connections(component_types)
        positions = self.build_positions(
            component_types,
            [(plan["source"], plan["destination"]) for plan in connection_plans],
        )
        # Relationships already wired as connections must not stay auto-terminated.
        connected_by_source = {
            plan["source"]: set() for plan in connection_plans
        }
        for plan in connection_plans:
            connected_by_source[plan["source"]].update(plan["selectedRelationships"])

        processors = [
            self.build_processor(
                row,
                positions[str(row.step)],
                group_id,
                connected_by_source.get(str(row.step), set()),
            )
            for row in sorted(
                self.df_steps.itertuples(index=False),
                key=lambda row: positions[str(row.step)],
            )
        ]
        funnel_objects = [
            self.build_funnel(row.step, positions[str(row.step)], group_id)
            for row in sorted(
                funnels.itertuples(index=False),
                key=lambda row: positions[str(row.step)],
            )
        ]
        controller_service_objects = [
            self.build_controller_service(row, group_id)
            for row in controller_services.itertuples(index=False)
        ]
        connections = [
            self.materialize_connection(plan, group_id, component_types)
            for plan in connection_plans
        ]

        self.output = self.build_flow(
            pipeline_id,
            group_id,
            processors,
            funnel_objects,
            connections,
            controller_service_objects,
        )

        self.output_reader = attach_file(
            self.output_reader,
            filename="flow.json",
            filepath="nifi",
            content=json.dumps(self.output, indent=4),
        )
        if not self.output_reader.ask(
            '?pipeline a tcs:PipelineDefinition ; nifi:deploymentMode "remote" .'
        ):
            bindings = self.fetch_secret_bindings()
            if bindings:
                self.add_local_configurator(bindings)

        return self.output_reader.graph

    def fetch_secret_bindings(self) -> list[dict[str, str]]:
        """Map sensitive component properties to late-bound Docker secrets."""
        rows = self.output_reader.select(
            "?step ?property_name ?secret_name ?processor_type "
            "?controller_service_type ?scheduled_state",
            f"""
            {_NIFI_RUNS_STEP}
            ?step p-plan:hasInputVar/tcs:embedded ?properties .
            ?component dcat:qualifiedRelation [
                    dcat:hadRole "configShape" ;
                    dct:relation ?shape
                ] .
            ?shape sh:property ?property_shape .
            ?property_shape sh:path ?predicate ;
                nifi:propertyName ?property_name ;
                nifi:sensitive true .
            ?properties ?predicate ?secret .
            ?secret a tcs:SecretReference ;
                tcs:secretName ?secret_name .
            OPTIONAL {{ ?component nifi:processorType ?processor_type . }}
            OPTIONAL {{
                ?component nifi:controllerServiceType ?controller_service_type .
            }}
            OPTIONAL {{ ?step nifi:scheduledState ?scheduled_state . }}
            """,
        )
        bindings = []
        for row in rows.itertuples(index=False):
            secret_name = str(row.secret_name)
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", secret_name):
                raise ValueError(
                    f"NiFi secretName must be an environment variable name: "
                    f"{secret_name}"
                )
            endpoint = (
                "controller-services"
                if not pd.isna(row.controller_service_type)
                else "processors"
            )
            desired_state = (
                "ENABLED" if endpoint == "controller-services" else "RUNNING"
            )
            if not pd.isna(row.scheduled_state):
                desired_state = str(row.scheduled_state)
                if endpoint == "processors" and desired_state == "ENABLED":
                    desired_state = "RUNNING"
            bindings.append(
                {
                    "endpoint": endpoint,
                    "id": nifi_id(f"{row.step}:instance"),
                    "property": str(row.property_name),
                    "secret": secret_name,
                    "state": desired_state,
                }
            )
        return bindings

    def add_local_configurator(self, bindings: list[dict[str, str]]) -> None:
        """Add a one-shot service that applies secrets after local NiFi starts."""
        config_id = receive_first(
            self.output_reader.select(
                "?config",
                """
                nifi:Orchestrator tcs:config ?config .
                ?config a tcs:DockerComposeConfig .
                """,
            )["config"]
        )
        compose = parse_docker_compose_config(self.output_reader, config_id)
        nifi_environment = compose["services"]["nifip"]["environment"]
        secret_names = sorted({binding["secret"] for binding in bindings})
        compose["services"]["nifi-configure"] = {
            "image": "python:3.12-slim",
            "depends_on": ["nifip"],
            "working_dir": "/deployment",
            "volumes": ["./nifi/configure_local.py:/deployment/configure_local.py:ro"],
            "environment": {
                "NIFI_BASE_URL": "https://nifip:8443",
                "NIFI_USERNAME": nifi_environment[
                    "SINGLE_USER_CREDENTIALS_USERNAME"
                ],
                "NIFI_PASSWORD": nifi_environment[
                    "SINGLE_USER_CREDENTIALS_PASSWORD"
                ],
            },
            "secrets": secret_names,
            "command": ["python", "/deployment/configure_local.py"],
            "restart": "on-failure",
        }
        compose["secrets"] = {
            name: {"environment": name} for name in secret_names
        }

        old_body = self.output_reader.filter(
            sub=config_id, pred=["tcs:literal", "tcs:embedded"]
        ).graph
        self.output_reader = self.output_reader.remove(old_body)
        body = GraphReader(
            pd.DataFrame.from_records(
                [
                    {
                        "sub": config_id,
                        "pred": "tcs:literal",
                        "obj": json.dumps(compose),
                        "sub_type": URIRef,
                        "obj_type": Literal,
                    }
                ]
            ),
            prefix_store=self.output_reader.prefix_store,
        ).graph
        self.output_reader = self.output_reader.add(body)
        self.output_reader = attach_file(
            self.output_reader,
            filename="configure_local.py",
            filepath="nifi",
            content=_LOCAL_CONFIGURATOR_SCRIPT.replace(
                "__SECRET_BINDINGS__", json.dumps(bindings, indent=4)
            ).lstrip(),
        )
        self.output_reader = attach_file(
            self.output_reader,
            filename=".env.example",
            filepath=".",
            content=(
                "# Copy this file to .env and fill in the values.\n"
                "# Docker Compose loads .env automatically from this directory.\n"
                "# Never commit .env.\n"
                + "".join(f"{name}=\n" for name in secret_names)
            ),
        )

    def fetch_expected_steps(self) -> pd.DataFrame:
        """Return every step assigned to a NiFi container.

        This deliberately does not require NiFi-specific component metadata,
        so ``compile`` can report incomplete catalog entries instead of
        silently dropping them from the generated flow.
        """
        return self.output_reader.select(
            "DISTINCT ?step ?component",
            _NIFI_RUNS_STEP,
        )

    def fetch_steps(self) -> pd.DataFrame:
        """Processor steps: catalog type/bundle + optional config and scheduling."""
        return self.output_reader.select(
            "?step ?component ?processor_type ?bundle_group "
            "?bundle_artifact ?bundle_version ?config "
            "?scheduled_state ?scheduling_period",
            f"""
            {_NIFI_RUNS_STEP}

            ?component nifi:processorType ?processor_type ;
                nifi:bundleGroup ?bundle_group ;
                nifi:bundleArtifact ?bundle_artifact ;
                nifi:bundleVersion ?bundle_version .

            OPTIONAL {{
                ?step p-plan:hasInputVar ?config .
            }}
            OPTIONAL {{
                ?step nifi:scheduledState ?scheduled_state .
            }}
            OPTIONAL {{
                ?step nifi:schedulingPeriod ?scheduling_period .
            }}
            """,
        )

    def fetch_funnels(self) -> pd.DataFrame:
        """Funnel steps (catalog ``nifi:componentType "FUNNEL"``)."""
        return self.output_reader.select(
            "?step ?component",
            f"""
            {_NIFI_RUNS_STEP}
            ?component nifi:componentType "FUNNEL" .
            """,
        )

    def fetch_controller_services(self) -> pd.DataFrame:
        """Return controller-service steps assigned to the NiFi container."""
        return self.output_reader.select(
            "DISTINCT ?step ?component ?controller_service_type "
            "?bundle_group ?bundle_artifact ?bundle_version ?service_api_type "
            "?service_api_bundle_group ?service_api_bundle_artifact "
            "?service_api_bundle_version ?config ?scheduled_state",
            f"""
            {_NIFI_RUNS_STEP}

            ?component nifi:controllerServiceType ?controller_service_type ;
                nifi:bundleGroup ?bundle_group ;
                nifi:bundleArtifact ?bundle_artifact ;
                nifi:bundleVersion ?bundle_version ;
                nifi:serviceApiType ?service_api_type ;
                nifi:serviceApiBundleGroup ?service_api_bundle_group ;
                nifi:serviceApiBundleArtifact ?service_api_bundle_artifact ;
                nifi:serviceApiBundleVersion ?service_api_bundle_version .

            OPTIONAL {{ ?step p-plan:hasInputVar ?config . }}
            OPTIONAL {{ ?step nifi:scheduledState ?scheduled_state . }}
            """,
        )

    def build_processor(
        self,
        row,
        position: tuple[float, float],
        group_id: str,
        connected_relationships: set[str],
    ) -> dict:
        """One NiFi processor object;

        Defaults: ``scheduledState`` → ``RUNNING``, ``schedulingPeriod`` →
        ``60 sec``. Remaining scheduling/backoff fields are fixed POC defaults.
        """
        properties, property_descriptors = self.fetch_mapped_properties(
            row.config, row.component, row.step
        )
        processor_id = nifi_id(str(row.step))
        scheduled_state = (
            "RUNNING" if pd.isna(row.scheduled_state) else str(row.scheduled_state)
        )
        if scheduled_state not in {"DISABLED", "ENABLED", "RUNNING"}:
            raise ValueError(
                f"NiFi step {row.step} has invalid nifi:scheduledState "
                f"{scheduled_state!r}"
            )
        scheduling_period = (
            "60 sec" if pd.isna(row.scheduling_period) else str(row.scheduling_period)
        )
        auto_terminated = [
            relationship
            for relationship in self.fetch_auto_terminated_relationships(row.component)
            if relationship not in connected_relationships
        ]

        return {
            "identifier": processor_id,
            "instanceIdentifier": nifi_id(f"{row.step}:instance"),
            "name": str(row.step).removeprefix(":"),
            "comments": "",
            "position": {
                "x": position[0],
                "y": position[1],
            },
            "type": row.processor_type,
            "bundle": {
                "group": row.bundle_group,
                "artifact": row.bundle_artifact,
                "version": row.bundle_version,
            },
            "properties": properties,
            "propertyDescriptors": property_descriptors,
            "style": {},
            "schedulingPeriod": scheduling_period,
            "schedulingStrategy": "TIMER_DRIVEN",
            "executionNode": "ALL",
            "penaltyDuration": "30 sec",
            "yieldDuration": "1 sec",
            "bulletinLevel": "WARN",
            "runDurationMillis": 0,
            "concurrentlySchedulableTaskCount": 1,
            "autoTerminatedRelationships": auto_terminated,
            "scheduledState": scheduled_state,
            "retryCount": 10,
            "retriedRelationships": [],
            "backoffMechanism": "PENALIZE_FLOWFILE",
            "maxBackoffPeriod": "10 mins",
            "componentType": "PROCESSOR",
            "groupIdentifier": group_id,
        }

    def build_controller_service(self, row, group_id: str) -> dict:
        properties, property_descriptors = self.fetch_mapped_properties(
            row.config, row.component, row.step
        )
        scheduled_state = (
            "ENABLED" if pd.isna(row.scheduled_state) else str(row.scheduled_state)
        )
        if scheduled_state not in {"DISABLED", "ENABLED"}:
            raise ValueError(
                f"NiFi controller service {row.step} has invalid "
                f"nifi:scheduledState {scheduled_state!r}"
            )

        return {
            "identifier": nifi_id(str(row.step)),
            "instanceIdentifier": nifi_id(f"{row.step}:instance"),
            "name": str(row.step).removeprefix(":"),
            "comments": "",
            "type": row.controller_service_type,
            "bundle": {
                "group": row.bundle_group,
                "artifact": row.bundle_artifact,
                "version": row.bundle_version,
            },
            "properties": properties,
            "propertyDescriptors": property_descriptors,
            "controllerServiceApis": [
                {
                    "type": row.service_api_type,
                    "bundle": {
                        "group": row.service_api_bundle_group,
                        "artifact": row.service_api_bundle_artifact,
                        "version": row.service_api_bundle_version,
                    },
                }
            ],
            "scheduledState": scheduled_state,
            "bulletinLevel": "WARN",
            "componentType": "CONTROLLER_SERVICE",
            "groupIdentifier": group_id,
        }

    def build_funnel(
        self, step: Any, position: tuple[float, float], group_id: str
    ) -> dict:
        return {
            "identifier": nifi_id(str(step)),
            "instanceIdentifier": nifi_id(f"{step}:instance"),
            "position": {
                "x": position[0],
                "y": position[1],
            },
            "componentType": "FUNNEL",
            "groupIdentifier": group_id,
        }

    def build_positions(
        self,
        component_types: dict[str, str],
        edges: list[tuple[str, str]],
    ) -> dict[str, tuple[float, float]]:
        """Column layout from channel DAG depth (IRI keys, no UUIDs).

        Horizontal position = topological depth; parallel nodes at the same
        depth get distinct vertical slots. Cyclic leftovers get a deterministic
        fallback column (NiFi allows loops; we still need stable coordinates).
        """
        nodes = sorted(component_types)
        successors = {node: set() for node in nodes}
        indegree = {node: 0 for node in nodes}

        for source, destination in edges:
            if destination not in successors[source]:
                successors[source].add(destination)
                indegree[destination] += 1

        depths = {node: 0 for node in nodes}
        ready = sorted(node for node in nodes if indegree[node] == 0)
        processed = set()
        while ready:
            source = ready.pop(0)
            processed.add(source)
            for destination in sorted(successors[source]):
                depths[destination] = max(
                    depths[destination], depths[source] + 1
                )
                indegree[destination] -= 1
                if indegree[destination] == 0:
                    ready.append(destination)
                    ready.sort()

        # NiFi permits loops, for which no topological order exists. Keep any
        # cyclic remainder deterministic instead of rejecting an otherwise valid flow.
        next_depth = max((depths[node] for node in processed), default=-1) + 1
        for node in sorted(set(nodes) - processed):
            depths[node] = next_depth
            next_depth += 1

        positions = {}
        for depth in sorted(set(depths.values())):
            column = sorted(node for node in nodes if depths[node] == depth)
            for row, node in enumerate(column):
                positions[node] = (
                    depth * _PROCESSOR_X_SPACING,
                    row * _PROCESSOR_Y_SPACING,
                )
        return positions

    def plan_connections(
        self,
        component_types: dict[str, str],
    ) -> list[dict]:
        """IRI-keyed connection plans from channels + writer ``nifi:route``s.

        Each plan has ``source``, ``destination``, ``channel``, and
        ``selectedRelationships``. Relationship resolution:

        1. ``nifi:route`` on the writer matching this channel, else
        2. catalog ``nifi:outgoingRelationship`` default.

        Selected relationships must appear in the catalog's outgoing or
        auto-terminated lists. UUIDs are minted later in
        :meth:`materialize_connection`.
        """
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
                ?source p-plan:hasInputVar/tcs:embedded/nifi:route ?route .
                ?route nifi:channel ?channel ;
                    nifi:selectedRelationship ?selected_relationship .
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

        plans = []
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
                    f"NiFi route on {source} for channel {channel} selects "
                    f"relationships not declared by "
                    f"{group.iloc[0]['source_component']}: {unknown}"
                )

            relationships = sorted(
                selected_relationships
                or set(group["default_relationship"].dropna().astype(str))
            )
            if source_type == "PROCESSOR" and not relationships:
                raise ValueError(
                    f"NiFi processor {source} writes to {channel} but has no "
                    "nifi:route selectedRelationship and its catalog component "
                    "has no nifi:outgoingRelationship"
                )

            plans.append(
                {
                    "source": source,
                    "destination": destination,
                    "channel": channel,
                    "selectedRelationships": relationships,
                }
            )

        return plans

    def materialize_connection(
        self,
        plan: dict,
        group_id: str,
        component_types: dict[str, str],
    ) -> dict:
        """Turn an IRI connection plan into a NiFi CONNECTION JSON object."""
        source = plan["source"]
        destination = plan["destination"]
        channel = plan["channel"]
        connection_key = f"{source}:{channel}:{destination}"
        return {
            "identifier": nifi_id(connection_key),
            "instanceIdentifier": nifi_id(f"{connection_key}:instance"),
            "name": channel.removeprefix(":"),
            "source": {
                "id": nifi_id(source),
                "type": component_types[source],
                "groupId": group_id,
            },
            "destination": {
                "id": nifi_id(destination),
                "type": component_types[destination],
                "groupId": group_id,
            },
            "labelIndex": 1,
            "zIndex": 0,
            "selectedRelationships": plan["selectedRelationships"],
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

    def fetch_mapped_properties(
        self, config: str, component: str, owner: str
    ) -> tuple[dict, dict]:
        """Map ``tcs:embedded`` predicates to NiFi property name/value pairs.

        Lookup uses ``nifi:propertyName`` (and optional ``nifi:sensitive``) on
        the component ``configShape``. ``nifi:route`` blocks are skipped.
        Values that specialize a controller-service component become that
        service's deterministic UUID, with ``identifiesControllerService``.
        """
        if pd.isna(config):
            return {}, {}

        properties = self.output_reader.select(
            "?predicate ?value ?property_name ?sensitive ?secret_name "
            "?is_controller_service ?controller_service_type",
            f"""
            {config} tcs:embedded ?embedded .
            ?embedded ?predicate ?value .

            OPTIONAL {{
                {component} dcat:qualifiedRelation ?relationship .
                ?relationship dcat:hadRole "configShape" ;
                    dct:relation ?shape .

                ?shape sh:property ?property_shape .
                ?property_shape sh:path ?predicate ;
                    nifi:propertyName ?property_name .
                OPTIONAL {{ ?property_shape nifi:sensitive ?sensitive . }}
            }}
            OPTIONAL {{
                ?value a tcs:SecretReference ;
                    tcs:secretName ?secret_name .
            }}
            OPTIONAL {{
                ?value prov:specializationOf ?cs_component .
                ?cs_component nifi:controllerServiceType ?controller_service_type .
                BIND (true AS ?is_controller_service)
            }}

            FILTER (?predicate != rdf:type && ?predicate != nifi:route)
            """,
        )

        unmapped = properties[properties["property_name"].isna()]
        if not unmapped.empty:
            predicates = ", ".join(unmapped["predicate"].astype(str))
            raise ValueError(
                f"NiFi component {component} has unmapped "
                f"configuration properties: {predicates}"
            )

        unresolved_services = properties.loc[
            properties["is_controller_service"].notna()
            & properties["controller_service_type"].isna()
        ]
        if not unresolved_services.empty:
            services = ", ".join(unresolved_services["value"].astype(str))
            raise ValueError(
                f"NiFi component {owner} references controller services with "
                f"incomplete catalog metadata: {services}"
            )

        duplicate_properties = properties.loc[
            properties["property_name"].duplicated(keep=False)
        ]
        if not duplicate_properties.empty:
            names = ", ".join(
                sorted(set(duplicate_properties["property_name"].astype(str)))
            )
            raise ValueError(
                f"NiFi component {owner} assigns properties more than once: {names}"
            )

        sensitive = properties["sensitive"].astype(str).str.lower() == "true"
        invalid_secrets = properties.loc[sensitive & properties["secret_name"].isna()]
        if not invalid_secrets.empty:
            names = ", ".join(invalid_secrets["property_name"].astype(str))
            raise ValueError(
                f"Sensitive NiFi properties on {owner} must use "
                f"tcs:SecretReference: {names}"
            )

        values = {
            str(row.property_name): (
                nifi_id(str(row.value))
                if not pd.isna(row.controller_service_type)
                else row.value
            )
            for row in properties.loc[~sensitive].itertuples(index=False)
        }
        descriptors = {
            str(row.property_name): {
                "name": str(row.property_name),
                "displayName": str(row.property_name),
                "identifiesControllerService": not pd.isna(
                    row.controller_service_type
                ),
                "sensitive": (
                    False if pd.isna(row.sensitive) else str(row.sensitive).lower() == "true"
                ),
                "dynamic": False,
            }
            for row in properties.itertuples(index=False)
        }
        return values, descriptors

    def fetch_auto_terminated_relationships(self, component: str) -> list[str]:
        """Catalog ``nifi:autoTerminatedRelationship`` list for a component."""
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
        controller_services: list[dict],
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
                "controllerServices": controller_services,
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


_LOCAL_CONFIGURATOR_SCRIPT = r'''
"""Apply late-bound secrets to a generated local NiFi flow using stdlib only."""

import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


BINDINGS = __SECRET_BINDINGS__
TLS_CONTEXT = ssl._create_unverified_context()


def request(path, *, token=None, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json", "Host": "localhost:8443"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{os.environ['NIFI_BASE_URL'].rstrip('/')}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, context=TLS_CONTEXT) as response:
        payload = response.read()
    return json.loads(payload) if payload else None


def token_request(credentials):
    req = urllib.request.Request(
        f"{os.environ['NIFI_BASE_URL'].rstrip('/')}/nifi-api/access/token",
        data=credentials,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Host": "localhost:8443",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=TLS_CONTEXT) as response:
            return response.read().decode()
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise RuntimeError(
            f"POST /nifi-api/access/token failed: {error.code} {detail}"
        ) from error


def authenticate():
    credentials = urllib.parse.urlencode(
        {
            "username": os.environ["NIFI_USERNAME"],
            "password": os.environ["NIFI_PASSWORD"],
        }
    ).encode()
    for attempt in range(90):
        try:
            return token_request(credentials)
        except (urllib.error.URLError, RuntimeError):
            if attempt == 89:
                raise
            time.sleep(2)


def wait_for_state(endpoint, component_id, token, state):
    for attempt in range(60):
        entity = request(f"/nifi-api/{endpoint}/{component_id}", token=token)
        if entity["component"]["state"] == state:
            return entity
        if attempt == 59:
            raise RuntimeError(f"NiFi component {component_id} did not reach {state}")
        time.sleep(1)


def set_state(endpoint, component_id, token, entity, state):
    request(
        f"/nifi-api/{endpoint}/{component_id}/run-status",
        token=token,
        method="PUT",
        body={
            "revision": entity["revision"],
            "state": state,
            "disconnectedNodeAcknowledged": False,
        },
    )
    return wait_for_state(endpoint, component_id, token, state)


def main():
    token = authenticate()
    grouped = {}
    for binding in BINDINGS:
        key = (binding["endpoint"], binding["id"])
        config = grouped.setdefault(
            key, {"properties": {}, "state": binding["state"]}
        )
        config["properties"][binding["property"]] = (
            Path("/run/secrets") / binding["secret"]
        ).read_text().rstrip("\r\n")

    for (endpoint, component_id), config in grouped.items():
        entity = request(f"/nifi-api/{endpoint}/{component_id}", token=token)
        inactive_state = "DISABLED" if endpoint == "controller-services" else "STOPPED"
        if entity["component"]["state"] != inactive_state:
            entity = set_state(
                endpoint, component_id, token, entity, inactive_state
            )
        entity = request(
            f"/nifi-api/{endpoint}/{component_id}",
            token=token,
            method="PUT",
            body={
                "revision": entity["revision"],
                "component": {
                    "id": component_id,
                    "properties": config["properties"],
                },
            },
        )
        if config["state"] != inactive_state:
            set_state(endpoint, component_id, token, entity, config["state"])
    print(f"Configured {len(BINDINGS)} local NiFi secret-backed properties.")


if __name__ == "__main__":
    main()
'''


def nifi_id(value: str) -> str:
    return str(uuid5(NAMESPACE_URL, value))
