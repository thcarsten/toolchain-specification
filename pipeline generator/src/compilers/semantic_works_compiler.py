from rdflib import Graph, URIRef, Literal
from rdfine import GraphReader, receive_first
import pandas as pd
import json

from .base import Compiler
from .utils import extract_config, parse_docker_compose_config


class SemanticWorksCompiler(Compiler):
    """
    Updates DockerComposeConfigs of mu:Microservices by the environment variables which were assigned to InstancePipelineComponents as part of the PipelineDefinition.
    """

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        # Intermediate state — populated in ``compile``.
        self.component_df: pd.DataFrame = pd.DataFrame()

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Triggered by any ``tcs:PipelineComponent`` in the ``sw:`` namespace,
        but only once ``PipelineAssembler`` has produced ``tcs:DockerContainer``
        nodes — otherwise there is nothing to fold env vars into yet."""
        if graph_reader.filter(pred="rdf:type", obj="tcs:DockerContainer").df.empty:
            return False
        df = graph_reader.filter(pred="rdf:type", obj="tcs:PipelineComponent").df
        return bool(df["sub"].str.startswith("sw:").any())

    def compile(self) -> Graph:
        self.fetch_components()
        for component_id in self.component_df["component"].to_list():
            self.update_docker_config(component_id)
        return self.output_reader.graph

    def fetch_components(self) -> None:
        """
        Identifies any SemanticWorks-components which need to be considered
        """

        # Checking that a bunch of conditions are met
        component_df = self.output_reader.select(
            "?component ?step ?step_config ?docker_config",
            """
            ?component a tcs:PipelineComponent .
            ?step prov:specializationOf ?component .
            ?step p-plan:hasInputVar ?step_config .
            ?component tcs:config ?docker_config .
            ?docker_config a tcs:DockerComposeConfig .
            """,
        )

        # Making sure it is a semantic.works component
        component_df = component_df.loc[component_df["component"].str.startswith("sw:")]
        self.component_df = component_df

    def update_docker_config(self, component_id) -> None:
        """
        The strategy here is surprisingly simple:
        - fetch the DockerComposeConfig via the shared parser, which
          returns a normalized ``{"services": {<name>: {body}}, ...}``
          dict regardless of the source config's shape
        - fetch the StepConfig as a plain dict
        - fold the StepConfig into the (single) service body's
          ``environment:``
        - write the edited config back to the graph via ``tcs:literal``
        """

        # Grabbing the right references
        step_config_id = receive_first(
            self.component_df.loc[
                self.component_df["component"] == component_id, "step_config"
            ],
        )
        docker_config_id = receive_first(
            self.component_df.loc[
                self.component_df["component"] == component_id, "docker_config"
            ],
        )

        # StepConfig is a generic ``tcs:Config`` (not a
        # DockerComposeConfig) so the plain ``extract_config`` helper
        # is enough. The DockerComposeConfig goes through the shared
        # parser, which normalizes to the compose-file layout — so
        # the service body always lives at
        # ``normalized["services"][<name>]`` regardless of how the
        # source config was written.
        step_config_dict = extract_config(self.output_reader, step_config_id)
        normalized = parse_docker_compose_config(self.output_reader, docker_config_id)

        if not normalized["services"]:
            return  # nothing to fold env vars into

        # SemanticWorks configs are single-service by construction, so
        # take the (only) service the parser found.
        service_name = next(iter(normalized["services"]))
        service_body = normalized["services"][service_name]

        # Checking whether target_dict already contains a "environment"-key
        key_list = list(service_body.keys())
        key_list = [k for k in key_list if "environment" in k]
        if len(key_list) > 0:
            environment_key = key_list[0]
        else:
            environment_key = "environment"
            service_body[environment_key] = {}

        # Updating the target_dict by the step_config_dict, then
        # dropping any residual prefixes on the whole body so
        # ``json.dumps`` produces a clean, portable string.
        service_body[environment_key].update(step_config_dict)
        normalized["services"][service_name] = self.output_reader.prefix_store.drop(
            service_body
        )
        docker_config_str = json.dumps(normalized)

        # Updating the docker compose config in the graph
        remove_triples = self.output_reader.filter(
            sub=docker_config_id, pred=["tcs:literal", "tcs:embedded"]
        ).graph
        self.output_reader = self.output_reader.remove(remove_triples)
        config_record = {
            "sub": docker_config_id,
            "pred": "tcs:literal",
            "obj": docker_config_str,
            "sub_type": URIRef,
            "obj_type": Literal,
        }
        add_triples = GraphReader(
            pd.DataFrame.from_records([config_record]), self.output_reader.prefix_store
        ).graph
        self.output_reader = self.output_reader.add(add_triples)
