"""Exercise the automatic choice of generation targets from what a push changed."""

from __future__ import annotations

import yaml

from generation import automatic_targets, git
from testsupport import RepoTestCase

OWNER = "https://example.invalid/owner:1.0.0"
ADDON = "https://example.invalid/addon:1.0.0"


def extension(identifier: str, dependencies: list[str], description: str = "") -> str:
    document = {
        "apiVersion": "runtimeconditions.io/v1alpha1",
        "kind": "RuntimeConditionsExtensionDefinition",
        "metadata": {"id": identifier, "version": "1.0.0", "description": description},
        "spec": {"dependencies": dependencies} if dependencies else {},
    }
    return yaml.safe_dump(document)


class AutomaticTargetTests(RepoTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.packages["owner"]["rootExtension"] = OWNER
        self.packages["addon"]["rootExtension"] = ADDON
        self.write_catalog()
        self.write("catalog/example/owner/owner.yaml", extension(OWNER, [ADDON]))
        self.write("catalog/example/addon/addon.yaml", extension(ADDON, []))
        self.write("catalog/example/owner/README.md", "Owner extension.\n")
        self.base = self.commit_all("Catalog and packages")

    def commit_all(self, message: str) -> str:
        git(self.root, "add", "-A")
        git(self.root, "commit", "-m", message)
        return git(self.root, "rev-parse", "HEAD").strip()

    def after(self, files: dict[str, str]) -> list[str]:
        for name, data in files.items():
            self.write(name, data)
        head = self.commit_all("Change")
        return automatic_targets(self.root, self.base, head)[0]

    def test_a_dependency_change_regenerates_its_package_and_every_dependent(self) -> None:
        targets = self.after({"catalog/example/addon/addon.yaml": extension(ADDON, [], "Now documented.")})
        self.assertEqual(targets, ["addon:go", "owner:go", "owner:python"])

    def test_a_root_extension_change_regenerates_only_its_package(self) -> None:
        targets = self.after({"catalog/example/owner/owner.yaml": extension(OWNER, [ADDON], "Now documented.")})
        self.assertEqual(targets, ["owner:go", "owner:python"])

    def test_docs_beside_an_extension_and_publication_scripts_change_nothing(self) -> None:
        targets = self.after({
            "catalog/example/owner/README.md": "Owner extension, explained.\n",
            "tooling/extension-bindings/ci/publish-npm.py": "# publication only\n",
        })
        self.assertEqual(targets, [])

    def test_an_emitter_change_regenerates_its_language(self) -> None:
        targets = self.after({"tooling/extension-bindings/emitters/python/src/x.py": "changed = True\n"})
        self.assertEqual(targets, ["owner:python"])

    def test_a_shared_tool_change_regenerates_everything(self) -> None:
        targets = self.after({"tooling/extension-bindings/normalizer/normalize.go": "package normalizer\n"})
        self.assertEqual(targets, ["addon:go", "owner:go", "owner:python"])

    def test_a_new_package_and_a_changed_entry_are_regenerated(self) -> None:
        self.packages["owner"]["languages"]["python"]["version"] = "0.2.0"
        self.packages["fresh"] = {
            "rootExtension": "https://example.invalid/fresh:1.0.0",
            "languages": {"go": {"sourceDirectory": "bindings/fresh/go", "languageVersion": "1.25.0"}},
        }
        self.write_catalog()
        head = self.commit_all("Catalog entries")
        targets, reasons = automatic_targets(self.root, self.base, head)
        self.assertEqual(targets, ["fresh:go", "owner:python"])
        self.assertTrue(any("package fresh is new" in reason for reason in reasons))

    def test_a_hand_edit_to_generated_source_is_regenerated(self) -> None:
        targets = self.after({"bindings/owner/go/stale.go": "edited by hand\n"})
        self.assertEqual(targets, ["owner:go"])

    def test_without_an_earlier_commit_every_target_is_selected(self) -> None:
        for base in ("", "0" * 40, "f" * 40):
            with self.subTest(base=base):
                self.assertEqual(automatic_targets(self.root, base)[0], ["addon:go", "owner:go", "owner:python"])
