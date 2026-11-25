"""Exercise the automatic choice of generation targets from what a push changed."""

from __future__ import annotations

import yaml

import io
import json
import urllib.error

from generation import automatic_targets, choose_base, git, last_successful_head
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

    def test_the_workflows_own_commits_to_generated_source_are_not_hand_edits(self) -> None:
        self.write("bindings/owner/go/stale.go", "regenerated\n")
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.name=github-actions[bot]", "commit", "-m", "chore(bindings): regenerate Go bindings")
        head = git(self.root, "rev-parse", "HEAD").strip()
        self.assertEqual(automatic_targets(self.root, self.base, head)[0], [])

    def test_changes_since_a_failed_run_are_still_selected(self) -> None:
        # The last successful run was for the base; a later run failed, and this push follows it.
        self.write("catalog/example/addon/addon.yaml", extension(ADDON, [], "Changed in the failed run."))
        self.commit_all("Change regenerated by a run that failed")
        self.write("catalog/example/owner/README.md", "Docs only.\n")
        head = self.commit_all("Docs")
        self.assertEqual(automatic_targets(self.root, self.base, head)[0], ["addon:go", "owner:go", "owner:python"])

    def test_the_base_is_the_first_candidate_that_is_an_ancestor(self) -> None:
        git(self.root, "checkout", "-q", "-b", "side")
        self.write("side.txt", "x\n")
        side = self.commit_all("Side")
        git(self.root, "checkout", "-q", "main")
        self.write("main.txt", "y\n")
        head = self.commit_all("Main")
        self.assertEqual(choose_base(self.root, ["", "0" * 40, side, self.base], head), self.base)
        self.assertEqual(choose_base(self.root, [side], head), "")

    def test_the_last_successful_run_is_read_from_the_actions_api(self) -> None:
        sha = "a" * 40
        requests = []

        def opener(request, timeout):
            requests.append(request.full_url)
            return io.BytesIO(json.dumps({"workflow_runs": [{"head_sha": sha}]}).encode())

        self.assertEqual(last_successful_head("org/repo", "token", opener=opener), sha)
        self.assertIn("/repos/org/repo/actions/workflows/binding-generate.yml/runs?branch=main&status=success", requests[0])
        empty = lambda request, timeout: io.BytesIO(b'{"workflow_runs": []}')  # noqa: E731
        self.assertIsNone(last_successful_head("org/repo", "token", opener=empty))

        def broken(request, timeout):
            raise urllib.error.URLError("offline")

        self.assertIsNone(last_successful_head("org/repo", "token", opener=broken))
