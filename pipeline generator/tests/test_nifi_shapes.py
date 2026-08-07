import sys
import unittest
from pathlib import Path

from rdflib import BNode, Graph, Literal, Namespace, RDF


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rdfine import GraphReader  # noqa: E402


EX = Namespace("http://example.org/example/")
NIFI = Namespace("http://example.org/example/nifi/")
PPLAN = Namespace("http://purl.org/net/p-plan#")
PROV = Namespace("http://www.w3.org/ns/prov#")
TCS = Namespace("https://w3id.org/toolchain#")


class NifiShapesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = ROOT / "data"
        cls.base = Graph()
        for filename in [
            "catalog.ttl",
            "pipeline_definition_nifi.ttl",
            "pipeline_definition_nifi.deployment.ttl",
            "tcs_shapes.ttl",
        ]:
            cls.base.parse(cls.data_dir / filename, format="turtle")

    def graph(self):
        graph = Graph()
        for prefix, namespace in self.base.namespaces():
            graph.bind(prefix, namespace)
        graph += self.base
        return graph

    def validate(self, graph):
        return GraphReader(graph).infer(
            self.data_dir / "inference_rules.yaml"
        ).validate(advanced=True, inference="rdfs")

    def assert_violation(self, mutate, message_fragment):
        graph = self.graph()
        expected_focus = mutate(graph)
        report = self.validate(graph)
        self.assertFalse(report.ask("?report sh:conforms true"))
        results = report.select(
            "?focus ?message",
            """
            ?result a sh:ValidationResult ;
                sh:focusNode ?focus ;
                sh:resultMessage ?message .
            """,
        )
        matches = results.loc[
            results["message"].astype(str).str.contains(
                message_fragment, regex=False
            )
        ]
        self.assertFalse(
            matches.empty,
            f"Expected message containing {message_fragment!r}; got\n{results}",
        )
        expected_focus = (
            f"_:{expected_focus}"
            if isinstance(expected_focus, BNode)
            else report.prefix_store.compact_string(str(expected_focus))
        )
        self.assertIn(expected_focus, set(matches["focus"].astype(str)))

    def test_reference_pipeline_conforms(self):
        report = self.validate(self.graph())
        self.assertTrue(report.ask("?report sh:conforms true"))

    def test_component_must_have_exactly_one_kind(self):
        def mutate(graph):
            graph.add(
                (
                    NIFI.GenerateFlowFile,
                    NIFI.controllerServiceType,
                    Literal("example.InvalidControllerService"),
                )
            )
            return NIFI.GenerateFlowFile

        self.assert_violation(mutate, "must describe exactly one kind")

    def test_remote_deployment_requires_deployment_config(self):
        def mutate(graph):
            graph.add(
                (EX.DemonstratorPipeline, NIFI.deploymentMode, Literal("remote"))
            )
            return EX.DemonstratorPipeline

        self.assert_violation(mutate, "requires exactly one nifi:deploymentConfig")

    def test_controller_service_must_belong_to_same_pipeline(self):
        def mutate(graph):
            graph.add((EX.OtherPipeline, RDF.type, TCS.PipelineDefinition))
            graph.remove(
                (
                    EX.AzureStorageCredentials,
                    PPLAN.isStepOfPlan,
                    EX.DemonstratorPipeline,
                )
            )
            graph.add(
                (
                    EX.AzureStorageCredentials,
                    PPLAN.isStepOfPlan,
                    EX.OtherPipeline,
                )
            )
            return EX.FetchAzureBlobStorage_v12

        self.assert_violation(mutate, "must belong to the same pipeline")

    def test_route_relationship_must_be_declared_by_component(self):
        def mutate(graph):
            route = next(graph.subjects(NIFI.channel, EX.nifi_channel_1))
            graph.remove((route, NIFI.selectedRelationship, None))
            graph.add(
                (route, NIFI.selectedRelationship, Literal("not-a-relationship"))
            )
            return route

        self.assert_violation(mutate, "is not declared by its PipelineComponent")

    def test_channel_may_not_have_multiple_nifi_writers(self):
        def mutate(graph):
            graph.add((EX.SplitText, TCS.writesTo, EX.nifi_channel_1))
            return EX.nifi_channel_1

        self.assert_violation(mutate, "has more than one NiFi writer")

    def test_scheduled_state_must_match_component_kind(self):
        def mutate(graph):
            graph.remove((EX.GenerateFlowFile, NIFI.scheduledState, None))
            graph.add(
                (EX.GenerateFlowFile, NIFI.scheduledState, Literal("ENABLED"))
            )
            return EX.GenerateFlowFile

        self.assert_violation(mutate, "must use scheduled state RUNNING or DISABLED")

    def test_secret_name_must_be_environment_variable_name(self):
        def mutate(graph):
            secret = graph.value(
                EX.AzureStorageCredentialsProperties, NIFI.sasToken
            )
            graph.remove((secret, TCS.secretName, None))
            graph.add((secret, TCS.secretName, Literal("not a valid name")))
            return secret

        self.assert_violation(mutate, "must be a non-empty environment-variable name")


if __name__ == "__main__":
    unittest.main()
