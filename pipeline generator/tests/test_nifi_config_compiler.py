import json
import sys
import unittest
from pathlib import Path

from rdflib import Graph, Literal, Namespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from compilers import PipelineGenerator  # noqa: E402
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

        example = Namespace("http://example.org/example/")
        nifi = Namespace("http://example.org/example/nifi/")
        graph.add(
            (
                example.AzureStorageCredentialsProperties,
                nifi.sasToken,
                Literal("test-sas-token"),
            )
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

        build = PipelineGenerator(":DemonstratorPipeline", cls.reader.graph).compile()
        files = GraphReader(build).select(
            "?content",
            """
            ?file a spdx:File ;
                tcs:filename "flow.json" ;
                tcs:literal ?content .
            """,
        )
        cls.flow = json.loads(str(files.iloc[0]["content"]))
        cls.root = cls.flow["rootGroup"]

    def test_emits_flow_json_with_expected_component_kinds(self):
        self.assertTrue(self.root["processors"])
        self.assertTrue(self.root["funnels"])
        self.assertTrue(self.root["connections"])
        self.assertEqual(1, len(self.root["controllerServices"]))
        self.assertEqual(6, len(self.root["processors"]))
        self.assertEqual(2, len(self.root["funnels"]))
        self.assertEqual(9, len(self.root["connections"]))

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


if __name__ == "__main__":
    unittest.main()
