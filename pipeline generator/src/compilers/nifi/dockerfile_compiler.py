from textwrap import dedent

from rdfine import GraphReader, receive_first
from rdflib import Graph

from ..compiler_abc import Compiler
from ..utils import attach_file, extract_config


class NifiDockerfileCompiler(Compiler):
    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        if graph_reader.ask(
            '?pipeline a tcs:PipelineDefinition ; nifi:deploymentMode "remote" .'
        ):
            return False
        return not graph_reader.select(
            "?container ?config",
            """
            ?container a tcs:DockerContainer ;
                tcs:instantiates nifi:Orchestrator .

            nifi:Orchestrator tcs:config ?config .
            ?config a tcs:DockerImageConfig .
            """,
        ).empty

    def compile(self) -> Graph:
        self.output_reader = attach_file(
            self.output_reader,
            filename="Dockerfile",
            filepath="nifi",
            content=self.extract_dockerfile(),
        )
        return self.output_reader.graph

    def extract_dockerfile(self) -> str:
        config_id = receive_first(
            self.output_reader.select(
                "?config",
                """
                nifi:Orchestrator tcs:config ?config .
                ?config a tcs:DockerImageConfig .
                """,
            )["config"]
        )

        body = extract_config(self.output_reader, config_id)
        if not isinstance(body, str):
            raise TypeError(
                f"NiFi DockerImageConfig {config_id} must contain text"
            )

        return dedent(body).strip() + "\n"
