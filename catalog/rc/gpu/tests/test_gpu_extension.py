"""Contract checks for the first GPU requirement release."""

import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
import yaml


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parents[2]
EXTENSION_ID = (
    "https://runtimeconditions.io/extensions/rc/gpu/0.2.0/"
    "runtimeconditions.extension.yaml"
)


class GPURequirementTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.definition = yaml.safe_load(
            (ROOT / "releases/0.2.0/runtimeconditions.extension.yaml").read_text(encoding="utf-8")
        )
        cls.validator = Draft202012Validator(cls.definition["spec"]["schemas"][0]["schema"])

    def test_release_identity_and_delivery_target(self):
        self.assertEqual(self.definition["metadata"]["id"], EXTENSION_ID)
        self.assertEqual(self.definition["metadata"]["version"], "0.2.0")
        self.assertEqual(
            [kind["name"] for kind in self.definition["spec"]["kinds"]], ["rc.gpu"]
        )
        packages = yaml.safe_load(
            (REPOSITORY / "tooling/extension-bindings/packages.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(packages["packages"]["gpu"]["rootExtension"], EXTENSION_ID)

    def test_inference_profile_example_declares_one_gpu_with_16_gib(self):
        profile = yaml.safe_load((ROOT / "examples/inference-profile.yaml").read_text(encoding="utf-8"))
        self.assertIn(f"{EXTENSION_ID}:0.2.0", profile["extensions"])
        self.assertEqual(len(profile["conditions"]), 1)
        condition = profile["conditions"][0]
        self.assertEqual(condition["kind"], "rc.gpu")
        self.assertEqual(condition["interface"], {
            "type": "device", "count": 1, "minMemoryMiB": 16384, "vendor": "nvidia",
        })
        self.assertEqual(list(self.validator.iter_errors(condition)), [])

    def test_schema_requires_positive_whole_gpu_count(self):
        valid = {"kind": "rc.gpu", "interface": {"type": "device", "count": 2}}
        self.assertEqual(list(self.validator.iter_errors(valid)), [])
        for count in (0, -1, 1.5, "1"):
            with self.subTest(count=count):
                invalid = {"kind": "rc.gpu", "interface": {"type": "device", "count": count}}
                self.assertTrue(list(self.validator.iter_errors(invalid)))
        missing_count = {"kind": "rc.gpu", "interface": {"type": "device"}}
        self.assertTrue(list(self.validator.iter_errors(missing_count)))

    def test_schema_accepts_an_optional_vendor_requirement(self):
        base = {"kind": "rc.gpu", "interface": {"type": "device", "count": 1}}
        self.assertEqual(list(self.validator.iter_errors(base)), [])
        for vendor in ("nvidia", "amd", "intel"):
            with self.subTest(vendor=vendor):
                condition = {**base, "interface": {**base["interface"], "vendor": vendor}}
                self.assertEqual(list(self.validator.iter_errors(condition)), [])

    def test_schema_rejects_invalid_memory_vendor_and_unknown_fields(self):
        base = {"kind": "rc.gpu", "interface": {"type": "device", "count": 1}}
        for value in (0, -10, 1.5, "16384"):
            with self.subTest(memory=value):
                invalid = {**base, "interface": {**base["interface"], "minMemoryMiB": value}}
                self.assertTrue(list(self.validator.iter_errors(invalid)))
        unknown_vendor = {**base, "interface": {**base["interface"], "vendor": "other"}}
        self.assertTrue(list(self.validator.iter_errors(unknown_vendor)))
        unknown_field = {**base, "interface": {**base["interface"], "driver": "12"}}
        self.assertTrue(list(self.validator.iter_errors(unknown_field)))


if __name__ == "__main__":
    unittest.main()
