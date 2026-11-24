"""Contract checks for the first observability extension release."""

import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parents[2]
DEFINITION = ROOT / "releases/0.1.0/runtimeconditions.extension.yaml"
EXTENSION_ID = (
    "https://runtimeconditions.io/extensions/rc/observability/0.1.0/"
    "runtimeconditions.extension.yaml"
)


class ObservabilityExtensionTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(DEFINITION.is_file(), "observability release is missing")
        self.definition = yaml.safe_load(DEFINITION.read_text(encoding="utf-8"))

    def validator(self, interface_type):
        matching = [
            entry
            for entry in self.definition["spec"]["schemas"]
            if entry["appliesToInterfaceType"] == interface_type
        ]
        self.assertEqual(len(matching), 1)
        return Draft202012Validator(matching[0]["schema"])

    def assert_valid(self, interface_type, interface):
        condition = {"kind": "rc.observability", "interface": interface}
        self.assertEqual(
            list(self.validator(interface_type).iter_errors(condition)), []
        )

    def assert_invalid(self, interface_type, interface):
        condition = {"kind": "rc.observability", "interface": interface}
        self.assertTrue(list(self.validator(interface_type).iter_errors(condition)))

    def test_release_identity_and_package_target(self):
        self.assertEqual(self.definition["metadata"]["id"], EXTENSION_ID)
        self.assertEqual(self.definition["metadata"]["version"], "0.1.0")
        self.assertEqual(
            [entry["name"] for entry in self.definition["spec"]["kinds"]],
            ["rc.observability"],
        )
        self.assertEqual(
            {entry["name"] for entry in self.definition["spec"]["interfaceTypes"]},
            {"otlp_export", "prometheus_scrape"},
        )
        packages = yaml.safe_load(
            (REPOSITORY / "tooling/extension-bindings/packages.yaml").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            packages["packages"]["observability"]["rootExtension"], EXTENSION_ID
        )

    def test_otlp_requires_supported_signals_and_transport(self):
        for transport in ("grpc", "http/protobuf", "http/json"):
            with self.subTest(transport=transport):
                self.assert_valid(
                    "otlp_export",
                    {
                        "type": "otlp_export",
                        "signals": ["traces", "metrics", "logs"],
                        "transport": transport,
                    },
                )
        base = {"type": "otlp_export", "signals": ["traces"], "transport": "grpc"}
        for signals in ([], ["traces", "traces"], ["profiles"], "traces"):
            with self.subTest(signals=signals):
                self.assert_invalid("otlp_export", {**base, "signals": signals})
        self.assert_invalid("otlp_export", {**base, "transport": "udp"})
        self.assert_invalid("otlp_export", {"type": "otlp_export", "transport": "grpc"})
        self.assert_invalid(
            "otlp_export", {"type": "otlp_export", "signals": ["traces"]}
        )

    def test_prometheus_requires_scrape_coordinates(self):
        for scheme in ("http", "https"):
            with self.subTest(scheme=scheme):
                self.assert_valid(
                    "prometheus_scrape",
                    {
                        "type": "prometheus_scrape",
                        "port": 9464,
                        "path": "/metrics",
                        "scheme": scheme,
                    },
                )
        base = {
            "type": "prometheus_scrape",
            "port": 9464,
            "path": "/metrics",
            "scheme": "http",
        }
        for port in (0, 65536, "9464", 1.5):
            with self.subTest(port=port):
                self.assert_invalid("prometheus_scrape", {**base, "port": port})
        self.assert_invalid("prometheus_scrape", {**base, "path": "metrics"})
        self.assert_invalid("prometheus_scrape", {**base, "scheme": "ftp"})
        for field in ("port", "path", "scheme"):
            with self.subTest(missing=field):
                self.assert_invalid(
                    "prometheus_scrape",
                    {key: value for key, value in base.items() if key != field},
                )

    def test_examples_use_their_own_interface_schema(self):
        examples = {
            "otlp-export-profile.yaml": "otlp_export",
            "prometheus-scrape-profile.yaml": "prometheus_scrape",
        }
        for filename, interface_type in examples.items():
            with self.subTest(example=filename):
                profile = yaml.safe_load(
                    (ROOT / "examples" / filename).read_text(encoding="utf-8")
                )
                self.assertIn(f"{EXTENSION_ID}:0.1.0", profile["extensions"])
                self.assertEqual(len(profile["conditions"]), 1)
                condition = profile["conditions"][0]
                self.assertEqual(condition["kind"], "rc.observability")
                self.assertEqual(condition["interface"]["type"], interface_type)
                self.assertEqual(
                    list(self.validator(interface_type).iter_errors(condition)), []
                )


if __name__ == "__main__":
    unittest.main()
