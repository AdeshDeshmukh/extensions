"""Dependent extensions get a new release when a dependency gets a newer one."""

from __future__ import annotations

from pathlib import Path
import tempfile
import textwrap
import unittest

from downstream_releases import discover, main, next_version, stale_dependents, version_key
from serialization import read_document

BASE = "https://runtimeconditions.io/extensions"


def definition(identifier: str, version: str, dependencies: list[str], quoted: bool = False) -> str:
    version_text = f'"{version}"' if quoted else version
    deps = "".join(f"\n  - {item}" for item in dependencies)
    dependency_block = f"  dependencies:{deps}\n" if dependencies else ""
    return textwrap.dedent(
        f"""\
        apiVersion: runtimeconditions.io/v1alpha1
        kind: RuntimeConditionsExtensionDefinition
        metadata:
          id: {identifier}
          version: {version_text}
          description: Kept exactly as written.
        spec:
        """
    ) + dependency_block + "  kinds:\n  - {name: example, description: Kept too.}\n"


def rc_id(name: str, version: str) -> str:
    return f"{BASE}/rc/{name}/{version}/runtimeconditions.extension.yaml"


class DownstreamReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="downstream-releases-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write_rc(self, name: str, version: str, dependencies: list[str]) -> Path:
        path = self.root / "catalog/rc" / name / f"{name}-{version}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(definition(rc_id(name, version), version, dependencies), encoding="utf-8")
        return path

    def test_versions_order_and_advance(self) -> None:
        self.assertLess(version_key("v1alpha2"), version_key("v1alpha10"))
        self.assertLess(version_key("v1alpha9"), version_key("v1beta1"))
        self.assertLess(version_key("v1beta1"), version_key("v1"))
        self.assertLess(version_key("0.9.0"), version_key("0.10.0"))
        self.assertEqual(next_version("v1alpha3"), "v1alpha4")
        self.assertEqual(next_version("v1"), "v2")
        self.assertEqual(next_version("0.2.5"), "0.3.0")

    def test_nothing_is_stale_when_dependencies_are_newest(self) -> None:
        self.write_rc("common", "v1alpha1", [])
        self.write_rc("env", "v1alpha1", [rc_id("common", "v1alpha1")])
        self.assertEqual(stale_dependents(discover(self.root)), [])
        self.assertEqual(main(["--root", str(self.root), "--check"]), 0)

    def test_check_reports_a_stale_dependent(self) -> None:
        self.write_rc("common", "v1alpha1", [])
        self.write_rc("common", "v1alpha2", [])
        self.write_rc("env", "v1alpha1", [rc_id("common", "v1alpha1")])
        stale = stale_dependents(discover(self.root))
        self.assertEqual([item.release.id for item in stale], [rc_id("env", "v1alpha1")])
        self.assertEqual(stale[0].replacements, {rc_id("common", "v1alpha1"): rc_id("common", "v1alpha2")})
        self.assertEqual(main(["--root", str(self.root), "--check"]), 1)
        self.assertEqual(main(["--root", str(self.root)]), 0)
        self.assertFalse((self.root / "catalog/rc/env/env-v1alpha2.yaml").exists())

    def test_write_releases_a_chain_and_keeps_everything_else(self) -> None:
        self.write_rc("common", "v1alpha1", [])
        self.write_rc("common", "v1alpha2", [])
        old_env = self.write_rc("env", "v1alpha1", [rc_id("common", "v1alpha1")])
        self.write_rc("secrets", "v1alpha1", [rc_id("env", "v1alpha1"), rc_id("common", "v1alpha2")])

        self.assertEqual(main(["--root", str(self.root), "--write"]), 0)

        env = self.root / "catalog/rc/env/env-v1alpha2.yaml"
        secrets = self.root / "catalog/rc/secrets/secrets-v1alpha2.yaml"
        self.assertEqual(
            env.read_text(encoding="utf-8"),
            old_env.read_text(encoding="utf-8")
            .replace(rc_id("env", "v1alpha1"), rc_id("env", "v1alpha2"))
            .replace("version: v1alpha1", "version: v1alpha2")
            .replace(rc_id("common", "v1alpha1"), rc_id("common", "v1alpha2")),
        )
        released = read_document(secrets)
        self.assertEqual(released["metadata"]["id"], rc_id("secrets", "v1alpha2"))
        self.assertEqual(released["spec"]["dependencies"], [rc_id("env", "v1alpha2"), rc_id("common", "v1alpha2")])
        self.assertEqual(released["spec"]["kinds"], [{"name": "example", "description": "Kept too."}])
        self.assertEqual(stale_dependents(discover(self.root)), [])

    def test_write_supports_the_releases_layout(self) -> None:
        base = f"{BASE}/acme/base"
        for version in ("0.1.0", "0.2.0"):
            path = self.root / "catalog/acme/base/releases" / version / "runtimeconditions.extension.yaml"
            path.parent.mkdir(parents=True)
            path.write_text(definition(f"{base}/{version}/runtimeconditions.extension.yaml", version, [], quoted=True), encoding="utf-8")
        user = f"{BASE}/acme/user"
        path = self.root / "catalog/acme/user/releases/0.4.0/runtimeconditions.extension.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(
            definition(f"{user}/0.4.0/runtimeconditions.extension.yaml", "0.4.0", [f"{base}/0.1.0/runtimeconditions.extension.yaml"], quoted=True),
            encoding="utf-8",
        )

        self.assertEqual(main(["--root", str(self.root), "--write"]), 0)

        written = self.root / "catalog/acme/user/releases/0.5.0/runtimeconditions.extension.yaml"
        document = read_document(written)
        self.assertEqual(document["metadata"]["id"], f"{user}/0.5.0/runtimeconditions.extension.yaml")
        self.assertEqual(document["metadata"]["version"], "0.5.0")
        self.assertIn('version: "0.5.0"', written.read_text(encoding="utf-8"))
        self.assertEqual(document["spec"]["dependencies"], [f"{base}/0.2.0/runtimeconditions.extension.yaml"])

    def test_write_refuses_to_overwrite_a_release(self) -> None:
        self.write_rc("common", "v1alpha1", [])
        self.write_rc("common", "v1alpha2", [])
        self.write_rc("env", "v1alpha1", [rc_id("common", "v1alpha1")])
        # A hand-written env v1alpha2 that is not the newest by ordering cannot exist,
        # so simulate a clash with an unrelated file at the target path.
        clash = self.root / "catalog/rc/env/env-v1alpha2.yaml"
        clash.write_text("kind: Notes\n", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            main(["--root", str(self.root), "--write"])


if __name__ == "__main__":
    unittest.main()
