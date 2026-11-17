"""Exercise the Java generation script."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

from generation import describe, select_targets
from testsupport import RepoTestCase, load_script, run_script

script = load_script("generate-java.py")

ROOT_EXTENSION = "https://example.test/owner/v1/extension.yaml"
ADDON_EXTENSION = "https://example.test/addon/v1/extension.yaml"


def java(artifact: str, package: str, directory: str, version: str = "0.1.0") -> dict:
    return {
        "coordinate": f"org.example.bindings:{artifact}",
        "name": package,
        "version": version,
        "sourceDirectory": directory,
        "languageVersion": "21.0.12",
        "publicationMode": "registry",
        "registryId": "maven-central",
    }


class JavaGenerationTests(RepoTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.packages["owner"]["languages"]["java"] = java(
            "owner-bindings", "org.example.bindings.owner", "bindings/owner/java"
        )
        self.packages["owner"]["rootExtension"] = ROOT_EXTENSION
        self.packages["addon"]["languages"]["java"] = java(
            "addon-bindings", "org.example.bindings.addon", "bindings/addon/java"
        )
        self.packages["addon"]["rootExtension"] = ADDON_EXTENSION
        self.write_catalog()
        self.catalog_document = {
            "repositoryUrl": "https://github.com/example/extensions",
            "packages": self.packages,
        }

    def test_configure_reports_the_jdk_version_and_whether_java_is_selected(self) -> None:
        output = self.root.parent / "github-output"
        for targets, version, has_java in [
            ("owner:java", "21.0.12", "true"),
            ("owner:go", "", "false"),
            ("owner:go,owner:java", "21.0.12", "true"),
        ]:
            with self.subTest(targets=targets):
                values = run_script(
                    "generate-java.py",
                    self.root,
                    output,
                    "configure",
                    "--targets",
                    targets,
                )
                self.assertEqual(values, {"java-version": version, "has-java": has_java})

    def test_the_emitter_digest_follows_its_sources_and_not_its_build(self) -> None:
        emitter = self.root / "emitter"
        for name, data in {
            "build.gradle.kts": "plugins {}\n",
            "settings.gradle.kts": "\n",
            "gradle.lockfile": "\n",
            "package-target.schema.yaml": "type: object\n",
            "src/main/java/Plan.java": "class Plan {}\n",
            "src/main/resources/emitter.properties": "version=1\n",
            "templates/pom.xml.tmpl": "<project/>\n",
        }.items():
            path = emitter / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data, encoding="utf-8")
        first = script.emitter_digest(emitter)
        (emitter / "build/classes").mkdir(parents=True)
        (emitter / "build/classes/Plan.class").write_text("built\n", encoding="utf-8")
        (emitter / "src/test").mkdir()
        (emitter / "src/test/PlanTest.java").write_text("class PlanTest {}\n", encoding="utf-8")
        self.assertEqual(script.emitter_digest(emitter), first)
        (emitter / "src/main/java/Plan.java").write_text("class Plan { int x; }\n", encoding="utf-8")
        self.assertNotEqual(script.emitter_digest(emitter), first)

    def test_the_package_manifest_lists_every_file_but_itself_in_the_go_format(self) -> None:
        package = self.root / "package"
        (package / "src/main/resources/runtimeconditions").mkdir(parents=True)
        (package / "src/main/resources/runtimeconditions" / script.FILE_MANIFEST).write_text("inner\n", encoding="utf-8")
        (package / "pom.xml").write_text("a\n", encoding="utf-8")
        (package / script.FILE_MANIFEST).write_text("stale\n", encoding="utf-8")
        digest = lambda text: hashlib.sha256(text.encode()).hexdigest()
        self.assertEqual(
            script.file_manifest(package),
            "apiVersion: runtimeconditions.io/file-manifest/v1alpha1\n"
            "kind: RuntimeConditionsFileManifest\n"
            "files:\n"
            f"    pom.xml: {digest('a' + chr(10))}\n"
            f"    src/main/resources/runtimeconditions/{script.FILE_MANIFEST}: {digest('inner' + chr(10))}\n",
        )

    def test_the_resource_manifest_lists_the_resources_relative_to_their_directory(self) -> None:
        resources = self.root / "resources"
        resources.mkdir()
        (resources / script.MODEL_FILE).write_text("model\n", encoding="utf-8")
        (resources / script.EXTENSION_FILE).write_text("extension\n", encoding="utf-8")
        (resources / script.FILE_MANIFEST).write_text("stale\n", encoding="utf-8")
        digest = lambda text: hashlib.sha256(text.encode()).hexdigest()
        self.assertEqual(
            script.resource_manifest(resources),
            "apiVersion: runtimeconditions.io/file-manifest/v1alpha1\n"
            "kind: RuntimeConditionsFileManifest\n"
            "files:\n"
            f"    {script.MODEL_FILE}: {digest('model' + chr(10))}\n"
            f"    {script.EXTENSION_FILE}: {digest('extension' + chr(10))}\n",
        )

    def test_the_package_target_comes_from_the_catalog_and_the_model(self) -> None:
        model = {"dependencyEdges": [{"from": ROOT_EXTENSION, "to": ADDON_EXTENSION}]}
        target = script.package_target(self.catalog_document, "owner", model, "f" * 64)
        self.assertEqual(target["groupId"], "org.example.bindings")
        self.assertEqual(target["artifactId"], "owner-bindings")
        self.assertEqual(target["javaPackage"], "org.example.bindings.owner")
        self.assertEqual(target["sourceDirectory"], "bindings/owner/java")
        self.assertEqual(target["minimumJavaVersion"], "17")
        self.assertEqual(target["registryId"], "maven-central")
        self.assertEqual(target["repositoryUrl"], "https://github.com/example/extensions")
        self.assertEqual(target["emitterSha256"], "f" * 64)
        self.assertEqual(
            target["dependencies"],
            [
                {
                    "extension": ADDON_EXTENSION,
                    "groupId": "org.example.bindings",
                    "artifactId": "addon-bindings",
                    "javaPackage": "org.example.bindings.addon",
                    "version": "0.1.0",
                }
            ],
        )

    def test_a_package_without_dependency_edges_has_no_dependencies(self) -> None:
        target = script.package_target(self.catalog_document, "addon", {}, "f" * 64)
        self.assertEqual(target["dependencies"], [])

    def test_a_dependency_needs_exactly_one_java_provider(self) -> None:
        del self.packages["addon"]["languages"]["java"]
        model = {"dependencyEdges": [{"from": ROOT_EXTENSION, "to": ADDON_EXTENSION}]}
        with self.assertRaisesRegex(ValueError, "found 0"):
            script.package_target(self.catalog_document, "owner", model, "f" * 64)

    def test_a_coordinate_must_be_a_group_and_an_artifact(self) -> None:
        for coordinate in ("artifact-only", ":artifact", "group:", "a:b:c"):
            with self.subTest(coordinate=coordinate):
                self.packages["owner"]["languages"]["java"]["coordinate"] = coordinate
                with self.assertRaisesRegex(ValueError, "groupId:artifactId"):
                    script.package_target(self.catalog_document, "owner", {}, "f" * 64)

    def test_the_root_extension_is_read_from_the_catalog_and_checked_against_the_lock(self) -> None:
        source = b"extension: definition\n"
        self.write("catalog/owner/owner-v1.yaml", source.decode())
        lock = {
            "extensions": [
                {
                    "id": ROOT_EXTENSION,
                    "sourceBackend": "catalog",
                    "sourceLocator": "catalog:owner/owner-v1.yaml",
                    "sourceSha256": hashlib.sha256(source).hexdigest(),
                }
            ]
        }
        self.assertEqual(script.root_extension_source(self.root, lock, ROOT_EXTENSION), source)
        lock["extensions"][0]["sourceSha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "differs from the dependency lock"):
            script.root_extension_source(self.root, lock, ROOT_EXTENSION)
        lock["extensions"][0]["sourceLocator"] = "https:example.test/owner.yaml"
        with self.assertRaisesRegex(ValueError, "only catalog extension sources"):
            script.root_extension_source(self.root, lock, ROOT_EXTENSION)
        with self.assertRaisesRegex(ValueError, "exactly once"):
            script.root_extension_source(self.root, lock, ADDON_EXTENSION)

    def test_generation_needs_the_built_emitter(self) -> None:
        with patch.dict("os.environ", {}, clear=True), self.assertRaisesRegex(
            ValueError, "build the Java emitter first"
        ):
            script.tools(self.root)
        built = self.root / "emitter"
        built.write_text("", encoding="utf-8")
        with patch.dict("os.environ", {"RC_JAVA_EMITTER": str(built)}, clear=True):
            self.assertEqual(script.tools(self.root), built)

    def generated(self, files: dict[str, str]):
        def generate(root, catalog, key, rc, core, work):
            package = work / key
            for name, data in files.items():
                path = package / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(data, encoding="utf-8")
            return package

        return generate

    def test_update_replaces_the_committed_tree_and_check_compares_it(self) -> None:
        self.write("tooling/extension-bindings/packages.yaml", self.catalog.read_text(encoding="utf-8"))
        self.write("bindings/owner/java/stale.txt", "old generated source\n")
        targets = select_targets(self.catalog, "owner:java")
        files = {"pom.xml": "<project/>\n", "src/main/java/Bindings.java": "new\n"}
        with patch.object(script, "generate", self.generated(files)):
            with self.assertRaisesRegex(ValueError, "stale.txt"):
                script.run(self.root, targets, Path("rc"), Path("core.yaml"), False)
            script.run(self.root, targets, Path("rc"), Path("core.yaml"), True)
            script.run(self.root, targets, Path("rc"), Path("core.yaml"), False)
        committed = self.root / "bindings/owner/java"
        self.assertFalse((committed / "stale.txt").exists())
        self.assertEqual((committed / "src/main/java/Bindings.java").read_text(encoding="utf-8"), "new\n")

    def test_update_leaves_other_languages_alone(self) -> None:
        targets = select_targets(self.catalog, "owner:go")
        with patch.object(script, "generate") as generate:
            script.run(self.root, targets, Path("rc"), Path("core.yaml"), True)
        generate.assert_not_called()
        self.assertTrue((self.root / "bindings/owner/go/stale.go").exists())

    def test_java_targets_carry_their_directory_and_exact_jdk_version(self) -> None:
        self.assertIn(
            ("owner:java", "bindings/owner/java", "21.0.12"),
            select_targets(self.catalog, ""),
        )
        self.assertEqual(describe(select_targets(self.catalog, "owner:go,owner:java")), "Go and Java")


if __name__ == "__main__":
    import unittest

    unittest.main()
