"""Exercise the Maven Central publication script."""

from __future__ import annotations

import io
import urllib.error
from unittest.mock import patch

import yaml

from generation import select_targets
from testsupport import RepoTestCase, load_script

script = load_script("publish-maven.py")

GROUP = "io.example.bindings"


def pom(artifact: str, version: str = "0.1.0", dependencies: list[str] | None = None, omit: str = "") -> str:
    parts = {
        "licenses": "<licenses><license><name>MIT License</name><url>https://opensource.org/license/mit</url></license></licenses>",
        "developers": "<developers><developer><name>Example</name></developer></developers>",
    }
    items = "".join(
        f"<dependency><groupId>{GROUP}</groupId><artifactId>{name}</artifactId><version>0.1.0</version></dependency>"
        for name in dependencies or []
    )
    return (
        '<project xmlns="http://maven.apache.org/POM/4.0.0">'
        f"<modelVersion>4.0.0</modelVersion><groupId>{GROUP}</groupId><artifactId>{artifact}</artifactId>"
        f"<version>{version}</version><name>{artifact}</name><description>Example bindings</description>"
        "<url>https://example.invalid/repo</url>"
        + "".join(value for key, value in parts.items() if key != omit)
        + "<scm><url>https://example.invalid/repo</url><connection>scm:git:https://example.invalid/repo.git</connection>"
        "<developerConnection>scm:git:https://example.invalid/repo.git</developerConnection></scm>"
        + (f"<dependencies>{items}</dependencies>" if items else "")
        + "</project>"
    )


class MavenPublicationTests(RepoTestCase):
    def setUp(self) -> None:
        super().setUp()
        for key in ("owner", "addon"):
            self.packages[key]["languages"]["java"] = {
                "coordinate": f"{GROUP}:{key}",
                "name": f"io.example.{key}",
                "version": "0.1.0",
                "sourceDirectory": f"bindings/{key}/java",
                "languageVersion": "21.0.12",
                "publicationMode": "registry",
                "registryId": "maven-central",
            }
        self.write_catalog()
        self.write("bindings/addon/java/pom.xml", pom("addon"))
        self.write("bindings/owner/java/pom.xml", pom("owner", dependencies=["addon"]))
        self.targets = select_targets(self.catalog, "owner:java,addon:java")

    def catalog_data(self) -> dict:
        return yaml.safe_load(self.catalog.read_text(encoding="utf-8"))

    def target(self, key: str):
        return next(item for item in self.targets if item[0] == f"{key}:java")

    def commands(self, calls) -> list[list[str]]:
        return [call.args[0] for call in calls]

    def test_the_committed_pom_must_match_the_catalog(self) -> None:
        found = script.read_pom(self.root, self.catalog_data(), self.target("owner"))
        self.assertEqual(found["coordinate"], (GROUP, "owner", "0.1.0"))
        self.assertEqual(found["dependencies"], [(GROUP, "addon", "0.1.0")])
        self.write("bindings/owner/java/pom.xml", pom("owner", version="0.2.0"))
        with self.assertRaisesRegex(ValueError, f"{GROUP}:owner:0.2.0, but the catalog says {GROUP}:owner:0.1.0"):
            script.read_pom(self.root, self.catalog_data(), self.target("owner"))

    def test_a_pom_without_what_central_requires_is_refused(self) -> None:
        self.write("bindings/owner/java/pom.xml", pom("owner", omit="licenses"))
        with self.assertRaisesRegex(ValueError, "lacks what Maven Central requires: license"):
            script.read_pom(self.root, self.catalog_data(), self.target("owner"))
        self.write("bindings/owner/java/pom.xml", pom("owner", omit="developers"))
        with self.assertRaisesRegex(ValueError, "developer"):
            script.read_pom(self.root, self.catalog_data(), self.target("owner"))

    def test_a_version_on_central_is_found_and_a_missing_one_is_not(self) -> None:
        coordinate = (GROUP, "addon", "0.1.0")
        self.assertEqual(
            script.location(coordinate),
            "https://repo1.maven.org/maven2/io/example/bindings/addon/0.1.0/addon-0.1.0.pom",
        )
        with patch.object(script.urllib.request, "urlopen", return_value=io.BytesIO(b"<project/>")):
            self.assertTrue(script.already_published(coordinate))
        missing = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        with patch.object(script.urllib.request, "urlopen", side_effect=missing):
            self.assertFalse(script.already_published(coordinate))
        broken = urllib.error.HTTPError("u", 503, "Unavailable", {}, None)
        with patch.object(script.urllib.request, "urlopen", side_effect=broken), self.assertRaises(urllib.error.HTTPError):
            script.already_published(coordinate)

    def test_dependencies_are_ordered_before_their_dependents(self) -> None:
        a, b, c = (GROUP, "a", "1"), (GROUP, "b", "1"), (GROUP, "c", "1")
        packages = {b: {"dependencies": [a, ("other", "lib", "1")]}, a: {"dependencies": []}, c: {"dependencies": [b]}}
        self.assertEqual(script.publication_order(packages), [a, b, c])
        packages[a] = {"dependencies": [c]}
        with self.assertRaisesRegex(ValueError, "cycle"):
            script.publication_order(packages)

    def test_version_requirements_are_matched_like_maven_does(self) -> None:
        for version, spec, expected in (
            ("0.1.0", "0.1.0", True),
            ("0.1.1", "0.1.0", False),
            ("0.1.0", "[0.1.0,0.2.0)", True),
            ("0.1.9", "[0.1.0,0.2.0)", True),
            ("0.2.0", "[0.1.0,0.2.0)", False),
            ("0.0.9", "[0.1.0,0.2.0)", False),
            ("0.2.0", "[0.1.0,0.2.0]", True),
            ("0.1.0", "(0.1.0,0.2.0)", False),
            ("1.0.0", "[0.1.0,)", True),
            ("0.1.0", "[0.1.0]", True),
            ("0.1.1", "[0.1.0]", False),
            ("0.2.0-rc.1", "[0.1.0,1.0.0)", False),
        ):
            self.assertEqual(script.in_range(version, spec), expected, f"{version} in {spec}")
        with self.assertRaisesRegex(ValueError, "cannot read the version range"):
            script.in_range("0.1.0", "[nonsense")

    def test_a_range_is_met_by_any_version_central_lists(self) -> None:
        dependency = (GROUP, "addon", "[0.1.0,0.2.0)")
        with patch.object(script, "metadata_versions", return_value=["0.0.9", "0.1.3"]):
            self.assertTrue(script.available(dependency))
        with patch.object(script, "metadata_versions", return_value=["0.2.0"]):
            self.assertFalse(script.available(dependency))
        with patch.object(script, "metadata_versions", return_value=[]):
            self.assertFalse(script.available(dependency))
        with patch.object(script, "already_published", return_value=True) as exact:
            self.assertTrue(script.available((GROUP, "addon", "0.1.0")))
        exact.assert_called_once()

    def test_central_metadata_lists_versions_and_a_missing_artifact_lists_none(self) -> None:
        body = b"<metadata><versioning><versions><version>0.1.0</version><version>0.1.1</version></versions></versioning></metadata>"
        with patch.object(script.urllib.request, "urlopen", return_value=io.BytesIO(body)) as opened:
            self.assertEqual(script.metadata_versions(GROUP, "addon"), ["0.1.0", "0.1.1"])
        self.assertEqual(
            opened.call_args.args[0], "https://repo1.maven.org/maven2/io/example/bindings/addon/maven-metadata.xml"
        )
        missing = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        with patch.object(script.urllib.request, "urlopen", side_effect=missing):
            self.assertEqual(script.metadata_versions(GROUP, "addon"), [])

    def test_a_dependency_on_a_range_is_ordered_after_the_selected_package_that_meets_it(self) -> None:
        needs, provides = (GROUP, "aaa-needs", "0.1.0"), (GROUP, "zzz-provides", "0.1.0")
        packages = {needs: {"dependencies": [(GROUP, "zzz-provides", "[0.1.0,0.2.0)")]}, provides: {"dependencies": []}}
        self.assertEqual(script.publication_order(packages), [provides, needs])
        packages = {needs: {"dependencies": [(GROUP, "zzz-provides", "[0.2.0,0.3.0)")]}, provides: {"dependencies": []}}
        self.assertEqual(script.publication_order(packages), [needs, provides], "the selected version is not what it needs")

    def test_a_dry_run_builds_into_a_local_repository_and_never_uploads(self) -> None:
        with patch.object(script, "run") as run, patch.object(script, "already_published", return_value=False):
            count = script.publish(self.root, self.targets, False)
        self.assertEqual(count, 2)
        commands = self.commands(run.call_args_list)
        self.assertFalse(any("deploy" in command for command in commands))
        for command in commands:
            self.assertEqual(command[:4], ["mvn", "-B", "-Prelease", "-Dgpg.skip=true"])
            self.assertEqual(command[-1], "install")
        self.assertEqual(len({command[4] for command in commands}), 1, "one local repository for the whole run")
        self.assertTrue(str(run.call_args_list[0].args[1]).endswith("sources/addon"))
        self.assertTrue(str(run.call_args_list[1].args[1]).endswith("sources/owner"))

    def test_versions_already_on_central_are_skipped(self) -> None:
        with patch.object(script, "run") as run, patch.object(
            script, "already_published", side_effect=lambda coordinate: coordinate[1] == "addon"
        ):
            count = script.publish(self.root, self.targets, False)
        self.assertEqual(count, 1)
        self.assertTrue(str(run.call_args.args[1]).endswith("sources/owner"))

    def test_a_real_run_uploads_with_a_signing_release_build(self) -> None:
        with patch.object(script, "run") as run, patch.object(script, "already_published", return_value=False):
            count = script.publish(self.root, select_targets(self.catalog, "addon:java"), True)
        self.assertEqual(count, 1)
        self.assertEqual(run.call_args.args[0], ["mvn", "-B", "-Prelease", "deploy"])

    def test_a_package_is_not_uploaded_before_what_it_depends_on_is_on_central(self) -> None:
        # The upload is released by hand, so in one run the dependency is uploaded but not yet on Central.
        with patch.object(script, "run") as run, patch.object(
            script, "already_published", return_value=False
        ), self.assertRaisesRegex(ValueError, f"{GROUP}:owner:0.1.0 depends on {GROUP}:addon:0.1.0, which is not on Maven Central"):
            script.publish(self.root, self.targets, True)
        self.assertEqual(len(run.call_args_list), 1)
        self.assertTrue(str(run.call_args.args[1]).endswith("sources/addon"))

    def test_the_dependent_is_uploaded_once_its_dependency_is_on_central(self) -> None:
        with patch.object(script, "run") as run, patch.object(
            script, "already_published", side_effect=lambda coordinate: coordinate[1] == "addon"
        ):
            script.publish(self.root, self.targets, True)
        self.assertEqual(run.call_args.args[0], ["mvn", "-B", "-Prelease", "deploy"])
        self.assertTrue(str(run.call_args.args[1]).endswith("sources/owner"))

    def test_other_languages_are_not_published(self) -> None:
        targets = select_targets(self.catalog, "owner:go")
        with patch.object(script, "run") as run:
            self.assertEqual(script.publish(self.root, targets, True), 0)
        run.assert_not_called()


if __name__ == "__main__":
    import unittest

    unittest.main()
