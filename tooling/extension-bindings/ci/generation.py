"""Shared target selection and commit-back for the generate-<language>.py scripts."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import yaml

LANGUAGES = {"go": "Go", "python": "Python", "typescript": "TypeScript", "java": "Java"}

Target = tuple[str, str, str]


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


def language_of(target: Target) -> str:
    return target[0].rsplit(":", 1)[1]


def of_language(targets: list[Target], language: str) -> list[Target]:
    return [target for target in targets if language_of(target) == language]


def exact_version(targets: list[Target], language: str) -> str:
    versions = {target[2] for target in of_language(targets, language)}
    return versions.pop() if versions else ""


def join_labels(labels: list[str]) -> str:
    if len(labels) < 3:
        return " and ".join(labels)
    return ", ".join(labels[:-1]) + " and " + labels[-1]


def describe(targets: list[Target]) -> str:
    return join_labels(
        [
            label
            for language, label in LANGUAGES.items()
            if of_language(targets, language)
        ]
    )


def load_targets(
    catalog: Path, languages: tuple[str, ...] = tuple(LANGUAGES)
) -> dict[str, Target]:
    packages = yaml.safe_load(catalog.read_text(encoding="utf-8"))["packages"]
    targets = {}
    for key, package in packages.items():
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", key):
            raise ValueError(f"invalid package key: {key}")
        for language in languages:
            config = package["languages"].get(language)
            if config is None:
                continue
            directory = f"bindings/{key}/{language}"
            if config["sourceDirectory"] != directory:
                raise ValueError(f"{key}:{language}: unexpected source directory")
            version = str(config["languageVersion"])
            if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
                raise ValueError(
                    f"{key}:{language}: invalid {LANGUAGES[language]} version"
                )
            targets[f"{key}:{language}"] = (f"{key}:{language}", directory, version)
    return targets


def select_targets(
    catalog: Path, requested: str, languages: tuple[str, ...] = tuple(LANGUAGES)
) -> list[Target]:
    if requested.strip() == "all":
        requested = ""
    targets = load_targets(catalog, languages)
    keys = (
        [item.strip() for item in requested.split(",")]
        if requested.strip()
        else sorted(targets)
    )
    if not keys or len(set(keys)) != len(keys):
        raise ValueError("select at least one binding target without duplicates")
    enabled = join_labels([LANGUAGES[language] for language in languages])
    for key in keys:
        if key not in targets:
            raise ValueError(
                f"unknown or disabled target: {key}; this workflow enables {enabled} only"
            )
    selected = [targets[key] for key in sorted(keys)]
    for language in languages:
        versions = {target[2] for target in of_language(selected, language)}
        if len(versions) > 1:
            raise ValueError(
                f"one generation run requires one exact {LANGUAGES[language]} version"
            )
    return selected


def summarize(message: str) -> None:
    print(message)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(path).open("a", encoding="utf-8") as stream:
            stream.write(message + "\n\n")


def write_outputs(**outputs: str) -> None:
    if path := os.environ.get("GITHUB_OUTPUT"):
        with Path(path).open("a", encoding="utf-8") as stream:
            for name, value in outputs.items():
                stream.write(f"{name.replace('_', '-')}={value}\n")


CATALOG_FILE = "tooling/extension-bindings/packages.yaml"
BOT_NAME = "github-actions[bot]"
EXTENSION_KIND = "RuntimeConditionsExtensionDefinition"
# Inputs every generated package depends on: a change regenerates all targets.
SHARED_INPUTS = (
    ".github/workflows/binding-generate.yml",
    ".github/actions/binding-tools/",
    "tooling/common/go_bindings.py",
    "tooling/extension-bindings/toolchain.lock.yaml",
    "tooling/extension-bindings/model/",
    "tooling/extension-bindings/normalizer/",
    "tooling/extension-bindings/orchestrator/",
    "tooling/extension-bindings/ci/generation.py",
)
# Inputs of one language: a change regenerates that language's targets.
LANGUAGE_INPUTS = {
    language: (f"tooling/extension-bindings/emitters/{language}/", f"tooling/extension-bindings/ci/generate-{language}.py")
    for language in LANGUAGES
}


def _yaml_at(root: Path, revision: str, path: str) -> object:
    """A YAML file at a revision, or None when it does not exist there or is not YAML."""
    try:
        text = git(root, "show", f"{revision}:{path}")
    except subprocess.CalledProcessError:
        return None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return None


def _extension_id(document: object) -> str | None:
    if isinstance(document, dict) and document.get("kind") == EXTENSION_KIND:
        identifier = (document.get("metadata") or {}).get("id")
        return identifier if isinstance(identifier, str) else None
    return None


def _extension_graph(root: Path, revision: str) -> dict[str, list[str]]:
    """Every catalog extension at a revision and the extensions it depends on."""
    graph: dict[str, list[str]] = {}
    names = git(root, "ls-tree", "-r", "--name-only", revision, "--", "catalog").splitlines()
    for path in names:
        if not path.endswith((".yaml", ".yml")):
            continue
        document = _yaml_at(root, revision, path)
        identifier = _extension_id(document)
        if identifier is not None:
            assert isinstance(document, dict)
            graph[identifier] = list((document.get("spec") or {}).get("dependencies") or [])
    return graph


def _closure(graph: dict[str, list[str]], identifier: str) -> set[str]:
    seen: set[str] = set()
    pending = [identifier]
    while pending:
        current = pending.pop()
        if current not in seen:
            seen.add(current)
            pending.extend(graph.get(current, []))
    return seen


def last_successful_head(
    repository: str, token: str, workflow: str = "binding-generate.yml", branch: str = "main",
    api: str = "https://api.github.com", opener=urllib.request.urlopen,
) -> str | None:
    """The commit the last successful generation run on a branch was started for, or None.

    Comparing with it instead of the commit before the push means a run that
    failed or was cancelled does not lose its changes: the next run still sees
    them. A successful run left every target current up to that commit, so
    nothing it covered is selected again.
    """
    request = urllib.request.Request(
        f"{api}/repos/{repository}/actions/workflows/{workflow}/runs?branch={branch}&status=success&per_page=1",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
    )
    try:
        with opener(request, timeout=30) as response:
            runs = json.load(response).get("workflow_runs", [])
    except (urllib.error.URLError, OSError, ValueError):
        return None
    sha = runs[0].get("head_sha") if runs else None
    return sha if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha) else None


def choose_base(root: Path, candidates: list[str], head: str = "HEAD") -> str:
    """The first candidate commit that is an ancestor of head, or "" when none is."""
    for candidate in candidates:
        if not candidate or not re.fullmatch(r"[0-9a-f]{7,40}", candidate) or set(candidate) == {"0"}:
            continue
        try:
            git(root, "merge-base", "--is-ancestor", candidate, head)
        except subprocess.CalledProcessError:
            continue
        return candidate
    return ""


def automatic_targets(root: Path, base: str, head: str = "HEAD") -> tuple[list[str], list[str]]:
    """The targets a push from base to head needs regenerated, and why.

    A shared generation input regenerates every target; an emitter regenerates
    its language; a changed or added package entry regenerates that package; a
    changed catalog extension regenerates every package whose root extension is
    it or depends on it; and an edited generated tree is regenerated to undo the
    hand edit. Without a usable base every target is selected.
    """
    targets = load_targets(root / CATALOG_FILE)
    every = sorted(targets)
    try:
        git(root, "cat-file", "-e", f"{base}^{{commit}}")
    except (subprocess.CalledProcessError, ValueError):
        return every, ["no earlier commit to compare with, so every target"]
    if not re.fullmatch(r"[0-9a-f]{7,40}", base) or set(base) == {"0"}:
        return every, ["no earlier commit to compare with, so every target"]
    changed = [line for line in git(root, "diff", "--name-only", "--no-renames", base, head).splitlines() if line]
    # Generated trees change in this workflow's own commits; only someone else's edit needs undoing.
    hand_edited: set[str] = set()
    author = ""
    for line in git(root, "log", "--no-merges", "--format=%x00%an", "--name-only", f"{base}..{head}").splitlines():
        if line.startswith("\0"):
            author = line[1:]
        elif line and author != BOT_NAME:
            hand_edited.add(line)
    chosen: set[str] = set()
    reasons: list[str] = []

    def add(keys: set[str], reason: str) -> None:
        new = keys & set(targets) - chosen
        if new:
            chosen.update(new)
            reasons.append(f"{reason}: {', '.join(sorted(new))}")

    for path in changed:
        if path.startswith(SHARED_INPUTS) or path in SHARED_INPUTS:
            add(set(targets), f"{path} is shared by every package")
        for language, prefixes in LANGUAGE_INPUTS.items():
            if path.startswith(prefixes):
                add({key for key in targets if key.endswith(":" + language)}, f"{path} changes {LANGUAGES[language]}")
        match = re.fullmatch(r"bindings/([a-z0-9-]+)/(go|python|typescript|java)/.+", path)
        if match and path in hand_edited:
            add({f"{match.group(1)}:{match.group(2)}"}, f"{path} is generated source")

    if CATALOG_FILE in changed:
        old = (_yaml_at(root, base, CATALOG_FILE) or {}).get("packages", {}) or {}
        new = (_yaml_at(root, head, CATALOG_FILE) or {}).get("packages", {}) or {}
        for key, package in new.items():
            before = old.get(key)
            if before is None:
                add({f"{key}:{language}" for language in package.get("languages", {})}, f"package {key} is new")
                continue
            shared = {name: value for name, value in package.items() if name != "languages"}
            shared_before = {name: value for name, value in before.items() if name != "languages"}
            for language, config in package.get("languages", {}).items():
                if shared != shared_before or config != (before.get("languages") or {}).get(language):
                    add({f"{key}:{language}"}, f"package {key} changed")

    extension_files = [path for path in changed if path.startswith("catalog/") and path.endswith((".yaml", ".yml"))]
    if extension_files:
        touched = {
            identifier
            for path in extension_files
            for revision in (base, head)
            if (identifier := _extension_id(_yaml_at(root, revision, path))) is not None
        }
        if touched:
            graph = _extension_graph(root, head)
            packages = (_yaml_at(root, head, CATALOG_FILE) or {}).get("packages", {}) or {}
            for key, package in packages.items():
                hit = _closure(graph, str(package.get("rootExtension", ""))) & touched
                if hit:
                    add(
                        {f"{key}:{language}" for language in package.get("languages", {})},
                        f"extension {sorted(hit)[0]} changed",
                    )
    return sorted(chosen), reasons


def commit(root: Path, targets: list[Target], source_commit: str) -> bool:
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ValueError("a full source commit is required")
    if git(root, "rev-parse", "HEAD").strip() != source_commit:
        raise ValueError("checkout moved after generation started")
    if git(root, "diff", "--cached", "--name-only", "-z"):
        raise ValueError("index must be empty before staging generated files")
    changed = set(
        filter(
            None,
            (
                git(root, "diff", "--name-only", "--no-renames", "-z", "HEAD")
                + git(root, "ls-files", "--others", "--exclude-standard", "-z")
            ).split("\0"),
        )
    )
    directories = [directory for _, directory, _ in targets]
    unexpected = sorted(
        path
        for path in changed
        if not any(path.startswith(directory + "/") for directory in directories)
    )
    if unexpected:
        raise ValueError(
            f"refusing to commit changes outside selected bindings: {unexpected}"
        )
    if not changed:
        summarize(f"{describe(targets)} bindings are current; no commit was created.")
        return False
    remote = git(root, "ls-remote", "origin", "refs/heads/main").split()
    if not remote or remote[0] != source_commit:
        raise ValueError("main moved during generation; rerun against its current head")
    git(root, "add", "-A", "--", *directories)
    git(
        root,
        "-c",
        "user.name=github-actions[bot]",
        "-c",
        "user.email=41898282+github-actions[bot]@users.noreply.github.com",
        "commit",
        "-m",
        f"chore(bindings): regenerate {describe(targets)} bindings",
        "-m",
        f"Generated and verified from {source_commit}.",
    )
    git(root, "push", "origin", "HEAD:refs/heads/main")
    generated_commit = git(root, "rev-parse", "HEAD").strip()
    summarize(
        f"Committed verified {describe(targets)} bindings to main: `{generated_commit}`."
    )
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["configure", "commit", "automatic"])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--targets", default="")
    parser.add_argument("--source-commit", default="")
    parser.add_argument("--base", default="", help="automatic: the commit before the push")
    parser.add_argument("--head", default="HEAD", help="automatic: the pushed commit")
    parser.add_argument(
        "--since-last-success", action="store_true",
        help="automatic: compare with the commit of the last successful generation run, falling back to --base",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if args.command == "automatic":
            candidates = [args.base]
            token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
            repository = os.environ.get("GITHUB_REPOSITORY")
            if args.since_last_success and token and repository:
                candidates.insert(0, last_successful_head(repository, token) or "")
            base = choose_base(root, candidates, args.head)
            chosen, reasons = automatic_targets(root, base, args.head)
            if base:
                reasons.insert(0, f"compared with {base[:12]}")
            write_outputs(targets=",".join(chosen))
            if chosen:
                summarize("Automatic targets:\n\n" + "\n".join(f"- {reason}" for reason in reasons))
            else:
                summarize("Nothing in this push changes generated bindings; no targets selected.")
            return
        targets = select_targets(
            root / "tooling/extension-bindings/packages.yaml", args.targets
        )
        if args.command == "configure":
            write_outputs(
                targets=",".join(target[0] for target in targets),
                source_commit=git(root, "rev-parse", "HEAD").strip(),
            )
            summarize(
                f"Selected {describe(targets)} targets: "
                + ", ".join(target[0] for target in targets)
            )
        else:
            commit(root, targets, args.source_commit)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr)
        parser.exit(1, f"binding generation failed: {error}\n")


if __name__ == "__main__":
    main()
