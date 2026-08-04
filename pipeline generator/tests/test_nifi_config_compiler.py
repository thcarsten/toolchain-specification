import json
import sys
import unittest
from pathlib import Path

from rdflib import Graph


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

        self.assertEqual(2, len(root["processors"]))
        self.assertEqual(1, len(root["funnels"]))
        self.assertEqual(2, len(root["connections"]))
        self.assertEqual(
            [("PROCESSOR", "PROCESSOR"), ("PROCESSOR", "FUNNEL")],
            [
                (connection["source"]["type"], connection["destination"]["type"])
                for connection in root["connections"]
            ],
        )
        self.assertEqual(
            [["success"], ["Response"]],
            [
                connection["selectedRelationships"]
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
        self.assertEqual(
            ["Failure", "No Retry", "Original", "Retry"],
            invoke_http["autoTerminatedRelationships"],
        )


if __name__ == "__main__":
    unittest.main()
