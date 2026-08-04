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
    def test_channel_wiring_emits_funnel_and_connections(self):
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

        reader = GraphReader(graph).infer(data_dir / "inference_rules.yaml")
        report = reader.validate(advanced=True, inference="rdfs")
        self.assertTrue(report.ask("?report sh:conforms true"))

        build = PipelineGenerator(":DemonstratorPipeline", reader.graph).compile()
        files = GraphReader(build).select(
            "?content",
            """
            ?file a spdx:File ;
                tcs:filename "flow.json" ;
                tcs:literal ?content .
            """,
        )
        flow = json.loads(str(files.iloc[0]["content"]))
        root = flow["rootGroup"]

        self.assertEqual(5, len(root["processors"]))
        self.assertEqual(2, len(root["funnels"]))
        self.assertEqual(8, len(root["connections"]))
        self.assertEqual(1, len(root["controllerServices"]))
        self.assertCountEqual(
            [("PROCESSOR", "PROCESSOR")] * 4
            + [("PROCESSOR", "FUNNEL")] * 4,
            [
                (connection["source"]["type"], connection["destination"]["type"])
                for connection in root["connections"]
            ],
        )
        self.assertCountEqual(
            [("success",)] * 4
            + [("failure",)] * 2
            + [("Response",), ("splits",)],
            [
                tuple(connection["selectedRelationships"])
                for connection in root["connections"]
            ],
        )
        invoke_http = next(
            processor
            for processor in root["processors"]
            if processor["name"] == "InvokeEndpoint"
        )
        generate_flow_file = next(
            processor
            for processor in root["processors"]
            if processor["name"] == "GenerateFlowFile"
        )
        self.assertEqual("RUNNING", generate_flow_file["scheduledState"])
        self.assertEqual("10 sec", generate_flow_file["schedulingPeriod"])
        self.assertEqual("RUNNING", invoke_http["scheduledState"])
        self.assertEqual("0 sec", invoke_http["schedulingPeriod"])
        fetch_azure = next(
            processor
            for processor in root["processors"]
            if processor["name"] == "FetchAzureBlobStorage_v12"
        )
        credentials = root["controllerServices"][0]
        self.assertEqual("DISABLED", credentials["scheduledState"])
        self.assertTrue(
            credentials["propertyDescriptors"]["SAS Token"]["sensitive"]
        )
        self.assertEqual(
            "org.apache.nifi.services.azure.storage.AzureStorageCredentialsService_v12",
            credentials["controllerServiceApis"][0]["type"],
        )
        self.assertEqual(
            credentials["identifier"],
            fetch_azure["properties"]["Storage Credentials"],
        )
        self.assertTrue(
            fetch_azure["propertyDescriptors"]["Storage Credentials"]
            ["identifiesControllerService"]
        )
        self.assertEqual(["failure"], fetch_azure["autoTerminatedRelationships"])
        split_text = next(
            processor
            for processor in root["processors"]
            if processor["name"] == "SplitText"
        )
        self.assertEqual("1", split_text["properties"]["Line Split Count"])
        self.assertEqual(["original"], split_text["autoTerminatedRelationships"])
        execute_groovy = next(
            processor
            for processor in root["processors"]
            if processor["name"] == "ExecuteGroovyScript"
        )
        self.assertEqual("rollback", execute_groovy["properties"]["Failure Strategy"])
        self.assertEqual([], execute_groovy["autoTerminatedRelationships"])
        funnel_ids = {funnel["identifier"] for funnel in root["funnels"]}
        self.assertEqual(
            [2, 2],
            sorted(
                sum(
                    connection["destination"]["id"] == funnel_id
                    for connection in root["connections"]
                )
                for funnel_id in funnel_ids
            ),
        )
        self.assertEqual(
            ["Failure", "No Retry", "Original", "Retry"],
            invoke_http["autoTerminatedRelationships"],
        )


if __name__ == "__main__":
    unittest.main()
