"""Publish binding packages to every registry from one workflow run.

binding-publish-all.yml runs two commands from this script:

preflight  Looks up every selected package on its registry and reports, per
           language, which versions are already published, which are new, and
           which are a package's first release. A first release needs a one-time
           registry setup that this workflow cannot do: npm only lets a trusted
           publisher be added to a package that already exists, and PyPI needs a
           pending trusted publisher for a project that does not exist yet. A
           real run stops before publishing anything when npm would need a first
           release, and warns about a PyPI first release.

dispatch   Starts one of the per-registry publish workflows (binding-promote.yml,
           binding-publish-pypi.yml, binding-publish-npm.yml or
           binding-publish-maven.yml) with workflow_dispatch and waits for it to
           finish. Each registry is still published by its own workflow, so npm
           and PyPI trusted publishing keep matching those workflow files.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import yaml

from generation import LANGUAGES, summarize, write_outputs

TOOLING = Path("tooling/extension-bindings")
WORKFLOWS = {
    "go": "binding-promote.yml",
    "python": "binding-publish-pypi.yml",
    "typescript": "binding-publish-npm.yml",
    "java": "binding-publish-maven.yml",
}
REGISTRIES = {"go": "GitHub tags", "python": "PyPI", "typescript": "npm", "java": "Maven Central"}
PUBLISHED, NEW_VERSION, FIRST_RELEASE = "published", "new version", "first release"


@dataclass(frozen=True)
class Package:
    key: str
    language: str
    coordinate: str
    version: str


@dataclass(frozen=True)
class Finding:
    package: Package
    status: str
    note: str = ""
    blocking: bool = False


def selected_languages(text: str) -> list[str]:
    """Languages from a comma-separated list, in the usual order; empty or all selects every language."""
    names = [item.strip() for item in text.split(",") if item.strip()]
    if not names or names == ["all"]:
        return list(LANGUAGES)
    unknown = [name for name in names if name not in LANGUAGES]
    if unknown:
        raise ValueError(f"unknown language {unknown[0]}; choose from {', '.join(LANGUAGES)}")
    return [language for language in LANGUAGES if language in names]


def selected_keys(catalog: dict, text: str) -> list[str]:
    """Package keys from a comma-separated list; empty or all selects every package."""
    keys = [item.strip() for item in text.split(",") if item.strip()]
    if not keys or keys == ["all"]:
        return sorted(catalog["packages"])
    unknown = [key for key in keys if key not in catalog["packages"]]
    if unknown:
        raise ValueError(f"unknown package {unknown[0]}; packages are listed in packages.yaml")
    return sorted(set(keys))


def packages(catalog: dict, keys: list[str], languages: list[str]) -> list[Package]:
    result = []
    for key in keys:
        for language in languages:
            config = catalog["packages"][key]["languages"].get(language)
            if config is not None:
                result.append(Package(key, language, str(config["coordinate"]), str(config["version"])))
    return result


def child_targets(keys: list[str], every: bool, language: str, chosen: list[Package]) -> str:
    """The targets input of one language's publish workflow."""
    if every:
        return "all"
    return ",".join(f"{item.key}:{language}" for item in chosen if item.language == language)


# Registry lookups. Each returns the versions a registry lists for a package, or
# None when the package does not exist there yet.
def _get(url: str) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def npm_versions(package: Package) -> set[str] | None:
    body = _get("https://registry.npmjs.org/" + package.coordinate.replace("/", "%2F"))
    return None if body is None else set(json.loads(body).get("versions", {}))


def pypi_versions(package: Package) -> set[str] | None:
    body = _get(f"https://pypi.org/pypi/{package.coordinate}/json")
    return None if body is None else set(json.loads(body).get("releases", {}))


def maven_versions(package: Package) -> set[str] | None:
    group, artifact = package.coordinate.split(":")
    body = _get(f"https://repo1.maven.org/maven2/{group.replace('.', '/')}/{artifact}/maven-metadata.xml")
    if body is None:
        return None
    return {(item.text or "").strip() for item in ET.fromstring(body).findall("./versioning/versions/version")}


def go_versions(package: Package, root: Path) -> set[str] | None:
    prefix = f"refs/tags/bindings/{package.key}/go/v"
    output = subprocess.run(
        ["git", "ls-remote", "--tags", "origin", prefix + "*"],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout
    versions = {line.split()[1][len(prefix):].removesuffix("^{}") for line in output.splitlines() if line.strip()}
    return versions or None


def inspect(package: Package, versions: set[str] | None, execute: bool) -> Finding:
    """What publishing a package's version would do, and whether it needs setup first."""
    if versions is not None and package.version.removeprefix("v") in {item.removeprefix("v") for item in versions}:
        return Finding(package, PUBLISHED, "already on the registry; skipped")
    if versions is not None:
        return Finding(package, NEW_VERSION, "will be published")
    if package.language == "typescript":
        return Finding(
            package, FIRST_RELEASE,
            "not on npm yet. npm only accepts a trusted publisher for a package that exists, so publish this "
            "first version once with an npm token, then add binding-publish-npm.yml as its trusted publisher",
            blocking=execute,
        )
    if package.language == "python":
        return Finding(
            package, FIRST_RELEASE,
            "not on PyPI yet. Before approving the pypi environment, add a pending trusted publisher on PyPI "
            f"(Account, Publishing) for project {package.coordinate}, workflow binding-publish-pypi.yml and "
            "environment pypi; the first upload then creates the project",
        )
    if package.language == "java":
        return Finding(package, FIRST_RELEASE, "first version on Maven Central; the verified namespace covers it")
    return Finding(package, FIRST_RELEASE, "first Go tag; the docs site already serves its module path")


def preflight(
    root: Path, keys_text: str, languages_text: str, execute: bool,
    lookup: Callable[[Package], set[str] | None] | None = None,
) -> tuple[list[Finding], list[str], bool]:
    """Inspect every selected package. Returns the findings, the languages with work to do, and whether to stop."""
    catalog = yaml.safe_load((root / TOOLING / "packages.yaml").read_text(encoding="utf-8"))
    keys = selected_keys(catalog, keys_text)
    languages = selected_languages(languages_text)
    chosen = packages(catalog, keys, languages)
    if lookup is None:
        lookups = {"go": lambda item: go_versions(item, root), "python": pypi_versions,
                   "typescript": npm_versions, "java": maven_versions}
        lookup = lambda item: lookups[item.language](item)  # noqa: E731
    findings = [inspect(item, lookup(item), execute) for item in chosen]
    pending = [language for language in languages
               if any(item.package.language == language and item.status != PUBLISHED for item in findings)]
    return findings, pending, any(item.blocking for item in findings)


def report(findings: list[Finding], execute: bool) -> str:
    lines = [
        f"## Binding publication plan ({'real run' if execute else 'dry run'})",
        "",
        "| Package | Language | Registry | Version | Status | Note |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in findings:
        mark = " ⛔" if item.blocking else (" ⚠️" if item.status == FIRST_RELEASE else "")
        lines.append(
            f"| {item.package.coordinate} | {LANGUAGES[item.package.language]} | {REGISTRIES[item.package.language]} "
            f"| {item.package.version} | {item.status}{mark} | {item.note} |"
        )
    if not findings:
        lines.append("| | | | | nothing selected | |")
    return "\n".join(lines)


class GitHub:
    """The few Actions REST calls dispatch needs, authenticated with the job's token."""

    def __init__(self, repository: str, token: str, api: str = "https://api.github.com") -> None:
        self.repository, self.token, self.api = repository, token, api

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            f"{self.api}/repos/{self.repository}/{path}", data=data, method=method,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read()
        return json.loads(payload) if payload else {}


def dispatch(
    github: GitHub, workflow: str, ref: str, inputs: dict[str, str], timeout: float,
    poll: float = 30, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Start a workflow_dispatch run and wait for it to finish. Returns the finished run."""
    started = now() - timedelta(seconds=5)
    github.request("POST", f"actions/workflows/{workflow}/dispatches", {"ref": ref, "inputs": inputs})
    deadline = now() + timedelta(seconds=timeout)
    run = None
    while run is None:
        listing = github.request("GET", f"actions/workflows/{workflow}/runs?event=workflow_dispatch&branch={ref}&per_page=20")
        mine = [
            item for item in listing.get("workflow_runs", [])
            if datetime.fromisoformat(item["created_at"].replace("Z", "+00:00")) >= started
            and item.get("triggering_actor", {}).get("login") == "github-actions[bot]"
        ]
        if mine:
            run = min(mine, key=lambda item: item["created_at"])
            break
        if now() > deadline:
            raise TimeoutError(f"{workflow} did not start a run")
        sleep(5)
    print(f"{workflow}: {run['html_url']}", flush=True)
    while run.get("status") != "completed":
        if now() > deadline:
            raise TimeoutError(f"{workflow} did not finish in time: {run['html_url']}")
        sleep(poll)
        run = github.request("GET", f"actions/runs/{run['id']}")
    return run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("preflight", help="report what each registry would receive")
    check.add_argument("--root", type=Path, default=Path.cwd())
    check.add_argument("--packages", default="all", help="comma-separated package keys, or all")
    check.add_argument("--languages", default="all", help="comma-separated go, python, typescript, java, or all")
    check.add_argument("--execute", action="store_true", help="a real run, which stops on blocking findings")
    start = commands.add_parser("dispatch", help="run one language's publish workflow and wait for it")
    start.add_argument("--root", type=Path, default=Path.cwd())
    start.add_argument("--language", required=True, choices=list(WORKFLOWS))
    start.add_argument("--packages", default="all")
    start.add_argument("--ref", default="main")
    start.add_argument("--execute", action="store_true")
    start.add_argument("--maven-auto-publish", action="store_true")
    start.add_argument("--timeout", type=float, default=3 * 3600)
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if args.command == "preflight":
            findings, pending, blocked = preflight(root, args.packages, args.languages, args.execute)
            summarize(report(findings, args.execute))
            if blocked:
                summarize("⛔ A real run cannot publish these first releases. Nothing was published.")
            write_outputs(languages=json.dumps(pending), blocked=str(blocked).lower())
            if blocked:
                sys.exit(1)
            return
        catalog = yaml.safe_load((root / TOOLING / "packages.yaml").read_text(encoding="utf-8"))
        keys = selected_keys(catalog, args.packages)
        every = args.packages.strip() in ("", "all")
        targets = child_targets(keys, every, args.language, packages(catalog, keys, [args.language]))
        inputs = {"targets": targets, "dry_run": "false" if args.execute else "true"}
        if args.language == "java":
            inputs["auto_publish"] = "true" if args.maven_auto_publish else "false"
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
        github = GitHub(os.environ["GITHUB_REPOSITORY"], token)
        run = dispatch(github, WORKFLOWS[args.language], args.ref, inputs, args.timeout)
        conclusion = run.get("conclusion")
        summarize(f"{LANGUAGES[args.language]}: [{WORKFLOWS[args.language]}]({run['html_url']}) finished: {conclusion}.")
        if conclusion != "success":
            sys.exit(1)
    except (ValueError, KeyError, OSError, TimeoutError, subprocess.CalledProcessError, ET.ParseError) as error:
        parser.exit(1, f"Binding publication failed: {error}\n")


if __name__ == "__main__":
    main()
