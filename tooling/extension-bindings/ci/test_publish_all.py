"""Exercise the one-button publication helper: the preflight plan and the workflow dispatch."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml

import publish_all as script

CATALOG = {
    "packages": {
        "common": {
            "languages": {
                "go": {"coordinate": "example.com/x/common", "version": "0.4.0"},
                "python": {"coordinate": "example-common", "version": "0.3.0"},
                "typescript": {"coordinate": "@example/common", "version": "0.4.0"},
                "java": {"coordinate": "io.example:common", "version": "0.2.0"},
            }
        },
        "env": {
            "languages": {
                "python": {"coordinate": "example-env", "version": "0.3.0"},
                "typescript": {"coordinate": "@example/env", "version": "0.4.0"},
            }
        },
    }
}


class PlanTests(unittest.TestCase):
    def setUp(self) -> None:
        summary = patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": "", "GITHUB_OUTPUT": ""})
        summary.start()
        self.addCleanup(summary.stop)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        catalog = self.root / script.TOOLING / "packages.yaml"
        catalog.parent.mkdir(parents=True)
        catalog.write_text(yaml.safe_dump(CATALOG), encoding="utf-8")

    def test_languages_and_packages_are_parsed_and_unknown_ones_refused(self) -> None:
        self.assertEqual(script.selected_languages("java, go"), ["go", "java"])
        self.assertEqual(script.selected_languages("all"), ["go", "python", "typescript", "java"])
        with self.assertRaisesRegex(ValueError, "unknown language rust"):
            script.selected_languages("rust")
        self.assertEqual(script.selected_keys(CATALOG, "env,common,env"), ["common", "env"])
        with self.assertRaisesRegex(ValueError, "unknown package nope"):
            script.selected_keys(CATALOG, "nope")

    def test_each_child_workflow_gets_its_own_language_targets(self) -> None:
        chosen = script.packages(CATALOG, ["common", "env"], ["python"])
        self.assertEqual(script.child_targets(["common", "env"], False, "python", chosen), "common:python,env:python")
        self.assertEqual(script.child_targets(["common", "env"], True, "python", chosen), "all")

    def test_published_new_and_first_release_versions_are_told_apart(self) -> None:
        go, python, typescript, java = script.packages(CATALOG, ["common"], list(script.LANGUAGES))
        self.assertEqual(script.inspect(go, {"v0.3.0", "v0.4.0"}, True).status, script.PUBLISHED)
        self.assertEqual(script.inspect(python, {"0.2.0"}, True).status, script.NEW_VERSION)
        first_npm = script.inspect(typescript, None, True)
        self.assertEqual(first_npm.status, script.FIRST_RELEASE)
        self.assertTrue(first_npm.blocking, "npm cannot take a first release through trusted publishing")
        self.assertIn("npm token", first_npm.note)
        self.assertFalse(script.inspect(typescript, None, False).blocking, "a dry run only reports it")
        first_pypi = script.inspect(python, None, True)
        self.assertFalse(first_pypi.blocking)
        self.assertIn("pending trusted publisher", first_pypi.note)
        self.assertIn("example-common", first_pypi.note)
        self.assertFalse(script.inspect(java, None, True).blocking)

    def test_preflight_lists_only_languages_with_something_new(self) -> None:
        listed = {
            "example.com/x/common": {"0.4.0"},
            "example-common": {"0.2.0"},
            "example-env": {"0.3.0"},
            "@example/common": {"0.4.0"},
            "@example/env": {"0.4.0"},
            "io.example:common": {"0.1.0"},
        }
        findings, pending, blocked = script.preflight(
            self.root, "all", "all", True, lookup=lambda item: listed[item.coordinate]
        )
        self.assertEqual(len(findings), 6)
        self.assertEqual(pending, ["python", "java"])
        self.assertFalse(blocked)
        table = script.report(findings, True)
        self.assertIn("| example-common | Python | PyPI | 0.3.0 | new version |", table)
        self.assertIn("| @example/env | TypeScript | npm | 0.4.0 | published |", table)

    def test_a_real_run_with_a_first_npm_release_is_blocked(self) -> None:
        _, pending, blocked = script.preflight(
            self.root, "env", "typescript", True, lookup=lambda item: None
        )
        self.assertEqual(pending, ["typescript"])
        self.assertTrue(blocked)


class FakeGitHub:
    def __init__(self, runs: list[dict], states: list[dict]) -> None:
        self.runs, self.states, self.calls = runs, states, []

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        self.calls.append((method, path, body))
        if method == "POST":
            return {}
        if path.startswith("actions/workflows/"):
            return {"workflow_runs": self.runs}
        return self.states.pop(0)


class DispatchTests(unittest.TestCase):
    def test_the_run_started_by_the_dispatch_is_waited_for(self) -> None:
        start = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)
        clock = iter(start + timedelta(seconds=step) for step in range(100))
        bot = {"login": "github-actions[bot]"}
        runs = [
            {"id": 1, "created_at": "2026-11-25T09:00:00Z", "triggering_actor": bot, "status": "completed",
             "html_url": "old"},
            {"id": 2, "created_at": "2026-11-25T10:00:03Z", "triggering_actor": {"login": "someone"},
             "status": "queued", "html_url": "other"},
            {"id": 3, "created_at": "2026-11-25T10:00:04Z", "triggering_actor": bot, "status": "queued",
             "html_url": "mine"},
        ]
        github = FakeGitHub(runs, [{"id": 3, "status": "in_progress", "html_url": "mine"},
                                   {"id": 3, "status": "completed", "conclusion": "success", "html_url": "mine"}])
        run = script.dispatch(
            github, "binding-publish-npm.yml", "main", {"targets": "all", "dry_run": "true"}, timeout=3600,
            now=lambda: next(clock), sleep=lambda _: None,
        )
        self.assertEqual(run["conclusion"], "success")
        self.assertEqual(github.calls[0], (
            "POST", "actions/workflows/binding-publish-npm.yml/dispatches",
            {"ref": "main", "inputs": {"targets": "all", "dry_run": "true"}},
        ))
        self.assertEqual([call[1] for call in github.calls[2:]], ["actions/runs/3", "actions/runs/3"])

    def test_a_run_that_never_starts_times_out(self) -> None:
        start = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)
        clock = iter(start + timedelta(minutes=step) for step in range(100))
        with self.assertRaisesRegex(TimeoutError, "did not start a run"):
            script.dispatch(FakeGitHub([], []), "binding-promote.yml", "main", {}, timeout=120,
                            now=lambda: next(clock), sleep=lambda _: None)


if __name__ == "__main__":
    unittest.main()
