import json
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import yaml
from rdflib import Graph


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from compilers import PipelineGenerator, ProjectBuilder  # noqa: E402
from rdfine import GraphReader  # noqa: E402


class NifiConfigCompilerTest(unittest.TestCase):
    """Contract tests for NifiConfigCompiler against the Urban Sense definition."""

    @classmethod
    def setUpClass(cls):
        data_dir = ROOT / "data"
        graph = Graph()
        for filename in [
            "catalog.ttl",
            "pipeline_definition_nifi.ttl",
            "tcs_shapes.ttl",
        ]:
            graph.parse(data_dir / filename, format="turtle")

        graph.parse(
            data="""
                @prefix : <http://example.org/example/> .
                @prefix nifi: <http://example.org/example/nifi/> .
                @prefix tcs: <https://w3id.org/toolchain#> .

                :AzureStorageCredentialsProperties
                    nifi:sasToken [
                        a tcs:SecretReference ;
                        tcs:secretName "TEST_AZURE_SAS_TOKEN"
                    ] .
            """,
            format="turtle",
        )

        cls.reader = GraphReader(graph).infer(data_dir / "inference_rules.yaml")
        report = cls.reader.validate(advanced=True, inference="rdfs")
        if not report.ask("?report sh:conforms true"):
            violations = report.select(
                "?focus ?message",
                """
                ?result a sh:ValidationResult ;
                    sh:focusNode ?focus ;
                    sh:resultMessage ?message .
                """,
            )
            details = "\n".join(
                f"{row.focus}: {row.message}" for row in violations.itertuples(index=False)
            )
            raise AssertionError(f"SHACL does not conform:\n{details}")

        cls.local_sentinel = "MUST_NOT_ENTER_LOCAL_GENERATED_FILES"
        with patch.dict(
            os.environ, {"TEST_AZURE_SAS_TOKEN": cls.local_sentinel}
        ):
            build = PipelineGenerator(":DemonstratorPipeline", cls.reader.graph).compile()
        files = GraphReader(build).select(
            "?name ?content",
            """
            ?file a spdx:File ;
                tcs:filename ?name ;
                tcs:literal ?content .
            """,
        )
        cls.build = build
        cls.by_name = {
            str(row.name): str(row.content) for row in files.itertuples(index=False)
        }
        cls.flow = json.loads(cls.by_name["flow.json"])
        cls.root = cls.flow["rootGroup"]

    def test_emits_flow_json_with_expected_component_kinds(self):
        self.assertEqual([], self.flow["parameterContexts"])
        self.assertEqual([], self.flow["parameterProviders"])
        self.assertTrue(self.root["processors"])
        self.assertTrue(self.root["funnels"])
        self.assertTrue(self.root["connections"])
        self.assertEqual(1, len(self.root["controllerServices"]))
        self.assertEqual(6, len(self.root["processors"]))
        self.assertEqual(2, len(self.root["funnels"]))
        self.assertEqual(9, len(self.root["connections"]))

    def test_local_compose_configures_secret_references_after_startup(self):
        self.assertNotIn(self.local_sentinel, "\n".join(self.by_name.values()))
        compose = yaml.safe_load(self.by_name["docker-compose.yml"])
        self.assertIn("nifip", compose["services"])
        configurator = compose["services"]["nifi-configure"]
        self.assertEqual(["nifip"], configurator["depends_on"])
        self.assertEqual(["TEST_AZURE_SAS_TOKEN"], configurator["secrets"])
        self.assertEqual(
            {"environment": "TEST_AZURE_SAS_TOKEN"},
            compose["secrets"]["TEST_AZURE_SAS_TOKEN"],
        )
        self.assertIn("configure_local.py", self.by_name)
        module = {"__name__": "configure_local"}
        exec(
            compile(
                self.by_name["configure_local.py"],
                "configure_local.py",
                "exec",
            ),
            module,
        )
        self.assertEqual("SAS Token", module["BINDINGS"][0]["property"])
        self.assertEqual("ENABLED", module["BINDINGS"][0]["state"])
        self.assertEqual(
            self.root["controllerServices"][0]["instanceIdentifier"],
            module["BINDINGS"][0]["id"],
        )
        self.assertEqual(
            "TEST_AZURE_SAS_TOKEN", module["BINDINGS"][0]["secret"]
        )
        self.assertIn("TEST_AZURE_SAS_TOKEN=", self.by_name[".env.example"])

        with tempfile.TemporaryDirectory() as target, io.StringIO() as output:
            with redirect_stdout(output):
                ProjectBuilder(self.build).write(target)
            self.assertIn("Deployment secrets are required", output.getvalue())

    def test_connections_use_channel_topology_and_embedded_routes(self):
        type_pairs = [
            (c["source"]["type"], c["destination"]["type"])
            for c in self.root["connections"]
        ]
        self.assertCountEqual(
            [("PROCESSOR", "PROCESSOR")] * 5 + [("PROCESSOR", "FUNNEL")] * 4,
            type_pairs,
        )
        self.assertCountEqual(
            [
                ("success",),
                ("success",),
                ("splits",),
                ("failure",),
                ("success",),
                ("failure",),
                ("data",),
                ("unparseable", "valueNotFound"),
                ("Failure", "No Retry", "Response", "Retry"),
            ],
            [
                tuple(c["selectedRelationships"])
                for c in self.root["connections"]
            ],
        )

    def test_controller_service_step_uuid_wired_into_processor(self):
        fetch_azure = next(
            p for p in self.root["processors"] if p["name"] == "FetchAzureBlobStorage_v12"
        )
        credentials = self.root["controllerServices"][0]
        self.assertEqual("ENABLED", credentials["scheduledState"])
        self.assertEqual(
            credentials["identifier"],
            fetch_azure["properties"]["Storage Credentials"],
        )
        self.assertTrue(
            fetch_azure["propertyDescriptors"]["Storage Credentials"][
                "identifiesControllerService"
            ]
        )
        self.assertTrue(credentials["propertyDescriptors"]["SAS Token"]["sensitive"])
        self.assertNotIn("SAS Token", credentials["properties"])

    def test_connected_relationships_are_removed_from_auto_terminated(self):
        split_text = next(p for p in self.root["processors"] if p["name"] == "SplitText")
        self.assertEqual(["original"], split_text["autoTerminatedRelationships"])

        ldes_sink = next(p for p in self.root["processors"] if p["name"] == "ldessink")
        self.assertEqual(["Original"], ldes_sink["autoTerminatedRelationships"])

        fetch_azure = next(
            p for p in self.root["processors"] if p["name"] == "FetchAzureBlobStorage_v12"
        )
        self.assertEqual(["failure"], fetch_azure["autoTerminatedRelationships"])

    def test_layout_is_left_to_right_on_dag_edges(self):
        by_id = {
            c["identifier"]: c
            for c in self.root["processors"] + self.root["funnels"]
        }
        for connection in self.root["connections"]:
            source = by_id[connection["source"]["id"]]
            destination = by_id[connection["destination"]["id"]]
            self.assertLess(source["position"]["x"], destination["position"]["x"])

    def test_processors_default_to_running_when_authored(self):
        generate = next(
            p for p in self.root["processors"] if p["name"] == "GenerateFlowFile"
        )
        self.assertEqual("RUNNING", generate["scheduledState"])
        self.assertEqual("10 sec", generate["schedulingPeriod"])


class NifiRemoteCompilerTest(unittest.TestCase):
    def test_remote_mode_replaces_local_nifi_with_one_shot_deployer(self):
        data_dir = ROOT / "data"
        graph = Graph()
        for filename in [
            "catalog.ttl",
            "pipeline_definition_nifi.ttl",
            "tcs_shapes.ttl",
        ]:
            graph.parse(data_dir / filename, format="turtle")

        graph.parse(
            data="""
                @prefix : <http://example.org/example/> .
                @prefix nifi: <http://example.org/example/nifi/> .
                @prefix tcs: <https://w3id.org/toolchain#> .

                :DemonstratorPipeline
                    nifi:deploymentMode "remote" ;
                    nifi:deploymentConfig [
                        a tcs:PipelineConfig ;
                        tcs:embedded [
                            nifi:dshUsername [
                                a tcs:SecretReference ;
                                tcs:secretName "TEST_DSH_USERNAME"
                            ] ;
                            nifi:dshPassword [
                                a tcs:SecretReference ;
                                tcs:secretName "TEST_DSH_PASSWORD"
                            ] ;
                            nifi:dshGatewayUrl "https://gateway.example/token" ;
                            nifi:baseUrl "https://nifi.example" ;
                            nifi:parentProcessGroupId "parent-id"
                        ]
                ] .

                :AzureStorageCredentialsProperties
                    nifi:storageAccountName [
                        a tcs:SecretReference ;
                        tcs:secretName "TEST_AZURE_STORAGE_ACCOUNT_NAME"
                    ] ;
                    nifi:sasToken [
                        a tcs:SecretReference ;
                        tcs:secretName "TEST_AZURE_SAS_TOKEN"
                    ] .
            """,
            format="turtle",
        )
        reader = GraphReader(graph).infer(data_dir / "inference_rules.yaml")
        report = reader.validate(advanced=True, inference="rdfs")
        self.assertTrue(report.ask("?report sh:conforms true"))
        sentinel = "MUST_NOT_ENTER_GENERATED_FILES"
        with patch.dict(
            os.environ,
            {
                "TEST_DSH_USERNAME": sentinel,
                "TEST_DSH_PASSWORD": sentinel,
                "TEST_AZURE_STORAGE_ACCOUNT_NAME": sentinel,
                "TEST_AZURE_SAS_TOKEN": sentinel,
            },
        ):
            build = PipelineGenerator(":DemonstratorPipeline", reader.graph).compile()
        files = GraphReader(build).select(
            "?name ?content",
            """
            ?file a spdx:File ;
                tcs:filename ?name ;
                tcs:literal ?content .
            """,
        )
        by_name = {str(row.name): str(row.content) for row in files.itertuples()}
        self.assertNotIn(sentinel, "\n".join(by_name.values()))

        with tempfile.TemporaryDirectory() as target, io.StringIO() as output:
            with redirect_stdout(output):
                ProjectBuilder(build).write(target)
            self.assertIn("Deployment secrets are required", output.getvalue())
            self.assertIn(".env", output.getvalue())

        self.assertNotIn("Dockerfile", by_name)
        self.assertNotIn("configure_local.py", by_name)
        self.assertIn("deploy_flow.py", by_name)
        self.assertEqual(
            {
                "TEST_AZURE_SAS_TOKEN=",
                "TEST_AZURE_STORAGE_ACCOUNT_NAME=",
                "TEST_DSH_PASSWORD=",
                "TEST_DSH_USERNAME=",
            },
            {
                line
                for line in by_name[".env.example"].splitlines()
                if line and not line.startswith("#")
            },
        )
        deployer_module = {"__name__": "deploy_flow"}
        exec(
            compile(by_name["deploy_flow.py"], "deploy_flow.py", "exec"),
            deployer_module,
        )
        multipart, content_type = deployer_module["multipart"](
            {"groupName": "test"}, "flow.json", b"{}"
        )
        self.assertIn(b'name="groupName"', multipart)
        self.assertIn(b'name="file"; filename="flow.json"', multipart)
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        deployable_flow = json.loads(by_name["flow_definition.json"])
        self.assertIn("flowContents", deployable_flow)
        self.assertNotIn("rootGroup", deployable_flow)
        self.assertNotIn("maxTimerDrivenThreadCount", deployable_flow)
        self.assertEqual({}, deployable_flow["parameterContexts"])
        self.assertEqual({}, deployable_flow["parameterProviders"])
        self.assertTrue(
            all(
                processor["scheduledState"] == "ENABLED"
                for processor in deployable_flow["flowContents"]["processors"]
            )
        )
        credentials = deployable_flow["flowContents"]["controllerServices"][0]
        self.assertNotIn("SAS Token", credentials["properties"])

        compose = yaml.safe_load(by_name["docker-compose.yml"])
        self.assertNotIn("nifip", compose["services"])
        self.assertNotIn("nifi-configure", compose["services"])
        deployer = compose["services"]["nifi-deploy"]
        self.assertEqual("no", deployer["restart"])
        self.assertEqual(["./nifi:/deployment:ro"], deployer["volumes"])
        self.assertEqual("python:3.12-slim", deployer["image"])
        self.assertNotIn("DSH_USERNAME", deployer["environment"])
        self.assertNotIn("DSH_PASSWORD", deployer["environment"])
        self.assertEqual("parent-id", deployer["environment"]["NIFI_PARENT_PG_ID"])
        self.assertEqual(
            [
                "AZURE_SAS_TOKEN",
                "AZURE_STORAGE_ACCOUNT_NAME",
                "DSH_PASSWORD",
                "DSH_USERNAME",
            ],
            deployer["secrets"],
        )
        self.assertEqual(
            {"environment": "TEST_DSH_PASSWORD"},
            compose["secrets"]["DSH_PASSWORD"],
        )


if __name__ == "__main__":
    unittest.main()
