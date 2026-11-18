"""Exercise the PyPI publication script."""

from __future__ import annotations

import io
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import patch

from generation import select_targets
from testsupport import RepoTestCase, load_script

script = load_script("publish-pypi.py")

RESOURCES = [
    "runtimeconditions.bindings.yaml",
    "runtimeconditions.binding-model.yaml",
    "runtimeconditions.extension.yaml",
    "runtimeconditions.binding-release.yaml",
]


class PyPIPublicationTests(RepoTestCase):
    def setUp(self) -> None:
        super().setUp()
        for key, name in (("owner", "example-owner"), ("addon", "example-addon")):
            self.packages[key]["languages"]["python"] = {
                "coordinate": name,
                "name": name.replace("-", "_"),
                "version": "0.1.0",
                "sourceDirectory": f"bindings/{key}/python",
                "languageVersion": "3.12.10",
                "publicationMode": "registry",
                "registryId": "pypi",
            }
        self.write_catalog()
        self.package("addon", "example-addon")
        self.package("owner", "example-owner")
        self.targets = select_targets(self.catalog, "owner:python,addon:python")
        self.dist = Path(self.temp.name) / "dist"

    def package(self, key: str, name: str, version: str = "0.1.0") -> None:
        module = name.replace("-", "_")
        data = ", ".join(f'"{item}"' for item in ["py.typed", *RESOURCES])
        self.write(
            f"bindings/{key}/python/pyproject.toml",
            f'[project]\nname = "{name}"\nversion = "{version}"\n\n'
            f'[tool.setuptools.package-data]\n"{module}" = [{data}]\n',
        )

    def project(self, key: str = "addon") -> dict:
        target = next(item for item in self.targets if item[0] == f"{key}:python")
        return script.read_project(self.root, yaml_catalog(self.catalog), target)

    def wheel(self, path: Path, module: str, names: list[str]) -> Path:
        with zipfile.ZipFile(path, "w") as archive:
            for name in names:
                archive.writestr(f"{module}/{name}", "")
        return path

    def fake_build(self, source: Path, destination: Path) -> list[Path]:
        name = source.parent.name
        module = f"example_{name}"
        files = [destination / f"{module}-0.1.0.tar.gz", destination / f"{module}-0.1.0-py3-none-any.whl"]
        files[0].write_bytes(b"sdist")
        self.wheel(files[1], module, RESOURCES)
        return files

    def test_the_committed_project_must_match_the_catalog(self) -> None:
        self.assertEqual(self.project()["project"]["name"], "example-addon")
        self.package("addon", "example-addon", version="0.2.0")
        with self.assertRaisesRegex(ValueError, "example-addon==0.2.0, but the catalog says example-addon==0.1.0"):
            self.project()
        self.package("addon", "someone-else")
        with self.assertRaisesRegex(ValueError, "someone-else"):
            self.project()

    def test_index_names_are_normalized_like_pypi_does(self) -> None:
        self.assertEqual(script.index_name("Runtime_Conditions.Common--Integrations"), "runtime-conditions-common-integrations")

    def test_a_version_on_pypi_is_found_and_a_missing_one_is_not(self) -> None:
        with patch.object(script.urllib.request, "urlopen", return_value=io.BytesIO(b'{"info": {"version": "0.1.0"}}')) as opened:
            self.assertTrue(script.already_published("Example_Addon", "0.1.0"))
        self.assertEqual(opened.call_args.args[0], "https://pypi.org/pypi/example-addon/0.1.0/json")
        missing = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        with patch.object(script.urllib.request, "urlopen", side_effect=missing):
            self.assertFalse(script.already_published("example-addon", "0.1.0"))
        broken = urllib.error.HTTPError("u", 503, "Unavailable", {}, None)
        with patch.object(script.urllib.request, "urlopen", side_effect=broken), self.assertRaises(urllib.error.HTTPError):
            script.already_published("example-addon", "0.1.0")

    def test_the_binding_resources_come_from_the_package_data(self) -> None:
        self.assertEqual(script.resource_files(self.project()), sorted(f"example_addon/{name}" for name in RESOURCES))

    def test_a_wheel_without_a_binding_resource_is_refused(self) -> None:
        wheel = Path(self.temp.name) / "x.whl"
        self.wheel(wheel, "example_addon", RESOURCES)
        script.check_wheel(wheel, self.project())
        self.wheel(wheel, "example_addon", RESOURCES[:-1])
        with self.assertRaisesRegex(ValueError, "example_addon/runtimeconditions.binding-release.yaml"):
            script.check_wheel(wheel, self.project())

    def test_only_versions_that_are_not_on_pypi_are_built(self) -> None:
        with patch.object(script, "build_package", side_effect=self.fake_build) as build, patch.object(
            script, "already_published", side_effect=lambda name, version: name == "example-addon"
        ):
            count = script.publish(self.root, self.targets, self.dist)
        self.assertEqual(count, 1)
        self.assertEqual(build.call_count, 1)
        self.assertTrue(str(build.call_args.args[0]).endswith("bindings/owner/python"))
        self.assertEqual(sorted(path.name for path in self.dist.iterdir()),
                         ["example_owner-0.1.0-py3-none-any.whl", "example_owner-0.1.0.tar.gz"])

    def test_nothing_new_builds_nothing(self) -> None:
        with patch.object(script, "build_package") as build, patch.object(script, "already_published", return_value=True):
            self.assertEqual(script.publish(self.root, self.targets, self.dist), 0)
        build.assert_not_called()

    def test_a_build_that_is_not_one_wheel_and_one_sdist_is_refused(self) -> None:
        def only_sdist(source: Path, destination: Path) -> list[Path]:
            path = destination / "x.tar.gz"
            path.write_bytes(b"")
            return [path]

        with patch.object(script, "build_package", side_effect=only_sdist), patch.object(
            script, "already_published", return_value=False
        ), self.assertRaisesRegex(ValueError, "one wheel and one sdist"):
            script.publish(self.root, self.targets, self.dist)

    def test_a_dist_directory_that_is_not_empty_is_refused(self) -> None:
        self.dist.mkdir()
        (self.dist / "old.whl").write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "new or empty"):
            script.publish(self.root, self.targets, self.dist)

    def test_other_languages_are_not_published(self) -> None:
        targets = select_targets(self.catalog, "owner:go")
        with patch.object(script, "build_package") as build:
            self.assertEqual(script.publish(self.root, targets, self.dist), 0)
        build.assert_not_called()


def yaml_catalog(path: Path) -> dict:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    import unittest

    unittest.main()
