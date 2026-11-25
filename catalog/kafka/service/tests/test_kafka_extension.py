"""Validate the Kafka capability contract and its example profiles."""

import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parents[2]
EXTENSION_ID = (
    "https://runtimeconditions.io/extensions/kafka/service/0.1.0/"
    "runtimeconditions.extension.yaml"
)


class KafkaExtensionTest(unittest.TestCase):
    def setUp(self):
        definition = ROOT / "releases/0.1.0/runtimeconditions.extension.yaml"
        self.assertTrue(definition.is_file(), "Kafka release is missing")
        self.definition = yaml.safe_load(definition.read_text(encoding="utf-8"))
        schemas = self.definition["spec"]["schemas"]
        self.assertEqual(len(schemas), 1)
        Draft202012Validator.check_schema(schemas[0]["schema"])
        self.validator = Draft202012Validator(schemas[0]["schema"])

    def condition(self, operations):
        return {
            "kind": "kafka.service",
            "interface": {"type": "service", "operations": operations},
        }

    def assert_valid(self, condition):
        self.assertEqual(list(self.validator.iter_errors(condition)), [])

    def assert_invalid(self, condition):
        self.assertTrue(list(self.validator.iter_errors(condition)))

    def test_release_and_all_language_targets_are_registered(self):
        self.assertEqual(self.definition["metadata"]["id"], EXTENSION_ID)
        self.assertEqual(self.definition["metadata"]["version"], "0.1.0")
        packages = yaml.safe_load(
            (REPOSITORY / "tooling/extension-bindings/packages.yaml").read_text(
                encoding="utf-8"
            )
        )["packages"]
        self.assertEqual(packages["kafka-service"]["rootExtension"], EXTENSION_ID)
        self.assertEqual(
            set(packages["kafka-service"]["languages"]),
            {"go", "python", "typescript", "java"},
        )

    def test_topic_access_does_not_require_a_consumer_group(self):
        for action in ("read", "write"):
            with self.subTest(action=action):
                self.assert_valid(
                    self.condition(
                        [{"resource": "topic", "action": action, "topic": "orders"}]
                    )
                )

    def test_shared_operation_fields_have_consistent_descriptions(self):
        schema = self.definition["spec"]["schemas"][0]["schema"]
        branches = schema["properties"]["interface"]["properties"]["operations"][
            "items"
        ]["oneOf"]
        for field in ("resource", "action"):
            with self.subTest(field=field):
                descriptions = {
                    branch["properties"][field]["description"] for branch in branches
                }
                self.assertEqual(
                    len(descriptions),
                    1,
                    "Shared enum documentation must remain valid after branches merge",
                )

    def test_group_access_can_be_declared_independently_or_with_topic_access(self):
        group = {"resource": "group", "action": "read", "groupId": "order-workers"}
        topic = {"resource": "topic", "action": "read", "topic": "orders"}
        self.assert_valid(self.condition([group]))
        self.assert_valid(self.condition([topic, group]))

    def test_rejects_unsupported_or_incomplete_operations(self):
        invalid = [
            {},
            {"resource": "topic", "action": "read"},
            {"resource": "group", "action": "read"},
            {"resource": "group", "action": "write", "groupId": "workers"},
            {"resource": "queue", "action": "read", "topic": "orders"},
            {"resource": "topic", "action": "create", "topic": "orders"},
            {"resource": "topic", "action": "read", "groupId": "workers"},
            {"resource": "group", "action": "read", "topic": "orders"},
            {"resource": "topic", "action": "write", "topic": "orders", "typo": 1},
            {"resource": "topic", "action": "write", "topic": ["orders"]},
            {
                "resource": "topic",
                "action": "read",
                "topic": "orders",
                "groupId": "workers",
            },
        ]
        for operation in invalid:
            with self.subTest(operation=operation):
                self.assert_invalid(self.condition([operation]))

    def test_rejects_empty_duplicate_or_nonarray_operations(self):
        topic = {"resource": "topic", "action": "read", "topic": "orders"}
        for operations in ([], [topic, topic], topic, None):
            with self.subTest(operations=operations):
                self.assert_invalid(self.condition(operations))
        self.assert_invalid({"kind": "kafka.service", "interface": {"type": "service"}})

    def test_topic_names_follow_kafka_constraints(self):
        for topic in ("orders", "a", ".a", "..a", "...", "A_0-.", "a" * 249):
            with self.subTest(valid=topic):
                self.assert_valid(
                    self.condition(
                        [{"resource": "topic", "action": "read", "topic": topic}]
                    )
                )
        for topic in (
            "",
            ".",
            "..",
            "*",
            "orders.*",
            "orders/1",
            "a b",
            "é",
            "a\n",
            "a" * 250,
        ):
            with self.subTest(invalid=topic):
                self.assert_invalid(
                    self.condition(
                        [{"resource": "topic", "action": "read", "topic": topic}]
                    )
                )

    def test_group_ids_are_nonempty_literal_strings(self):
        for group_id in ("workers", "a.b_c-1", "a/b", "a" * 250):
            with self.subTest(valid=group_id):
                self.assert_valid(
                    self.condition(
                        [{"resource": "group", "action": "read", "groupId": group_id}]
                    )
                )
        for group_id in ("", "*", "workers*", None, 42):
            with self.subTest(invalid=group_id):
                self.assert_invalid(
                    self.condition(
                        [{"resource": "group", "action": "read", "groupId": group_id}]
                    )
                )

    def test_condition_and_interface_remain_open_for_composition(self):
        condition = self.condition(
            [{"resource": "topic", "action": "read", "topic": "orders"}]
        )
        condition["configuration"] = {"env": [{"property": "url", "name": "KAFKA_URL"}]}
        condition["interface"]["otherExtensionField"] = "value"
        self.assert_valid(condition)

    def test_examples_preserve_producer_and_consumer_requirements(self):
        expected = {
            "producer-profile.yaml": [
                {"resource": "topic", "action": "write", "topic": "orders"}
            ],
            "group-consumer-profile.yaml": [
                {"resource": "topic", "action": "read", "topic": "orders"},
                {"resource": "group", "action": "read", "groupId": "order-workers"},
            ],
            "direct-consumer-profile.yaml": [
                {"resource": "topic", "action": "read", "topic": "orders"}
            ],
        }
        for filename, operations in expected.items():
            with self.subTest(example=filename):
                profile = yaml.safe_load(
                    (ROOT / "examples" / filename).read_text(encoding="utf-8")
                )
                self.assertIn(f"{EXTENSION_ID}:0.1.0", profile["extensions"])
                self.assertEqual(len(profile["conditions"]), 1)
                condition = profile["conditions"][0]
                self.assertEqual(condition["interface"]["operations"], operations)
                self.assert_valid(condition)


if __name__ == "__main__":
    unittest.main()
