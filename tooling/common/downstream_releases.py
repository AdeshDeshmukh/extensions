"""Release dependent extensions when an extension they depend on gets a new release.

An extension release pins the exact releases it depends on. When a new release
of an extension is published, extensions whose newest release still depends on
an older release of it are stale. This tool finds them and, with --write, adds
the next release of each one that depends on the newest releases instead.

    python tooling/common/downstream_releases.py           # report stale dependents
    python tooling/common/downstream_releases.py --check   # also exit 1 if any are stale
    python tooling/common/downstream_releases.py --write   # add the next releases

A written release copies the dependent's newest definition and changes only its
metadata.id, its metadata.version and the stale dependency identifiers. Writing
repeats until nothing is stale, so a chain A <- B <- C is released in one run.
Both catalog layouts are supported: catalog/<ns>/<name>/<name>-<version>.yaml and
catalog/<ns>/<name>/releases/<version>/runtimeconditions.extension.yaml.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from serialization import read_document

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFINITION_FILE = "runtimeconditions.extension.yaml"
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
API_VERSION = re.compile(r"^v(\d+)(?:(alpha|beta)(\d+))?$")


@dataclass(frozen=True)
class Release:
    path: Path
    id: str
    version: str
    family: str
    dependencies: tuple[str, ...]


@dataclass(frozen=True)
class StaleDependency:
    release: Release
    replacements: dict[str, str]  # stale dependency id -> newest release id


def version_key(version: str) -> tuple[int, ...]:
    """Order releases of one extension: 0.1.0 < 0.2.0, v1alpha1 < v1alpha2 < v1beta1 < v1."""
    if match := SEMVER.match(version):
        return (0, *map(int, match.groups()))
    if match := API_VERSION.match(version):
        major, stage, number = match.groups()
        stages = {"alpha": 0, "beta": 1, None: 2}
        return (1, int(major), stages[stage], int(number or 0))
    raise ValueError(f"unsupported extension version {version!r}")


def next_version(version: str) -> str:
    """The release after version: 0.2.0 -> 0.3.0, v1alpha3 -> v1alpha4, v1 -> v2."""
    if match := SEMVER.match(version):
        major, minor, _ = map(int, match.groups())
        return f"{major}.{minor + 1}.0"
    if match := API_VERSION.match(version):
        major, stage, number = match.groups()
        if stage:
            return f"v{major}{stage}{int(number) + 1}"
        return f"v{int(major) + 1}"
    raise ValueError(f"unsupported extension version {version!r}")


def split_identifier(identifier: str) -> tuple[str, str]:
    """Split .../<family>/<version>/runtimeconditions.extension.yaml into family and version."""
    family, version, filename = identifier.rsplit("/", 2)
    if filename != DEFINITION_FILE:
        raise ValueError(f"{identifier}: expected an identifier ending in /<version>/{DEFINITION_FILE}")
    return family, version


def discover(root: Path) -> list[Release]:
    catalog = root / "catalog"
    paths = sorted(catalog.glob(f"*/*/releases/*/{DEFINITION_FILE}"))
    paths += sorted(path for path in catalog.glob("*/*/*.yaml") if read_document(path).get("kind") == "RuntimeConditionsExtensionDefinition")
    releases = []
    for path in paths:
        document = read_document(path)
        identifier = document["metadata"]["id"]
        family, version = split_identifier(identifier)
        declared = str(document["metadata"].get("version", version))
        if declared != version:
            raise ValueError(f"{path}: metadata.version {declared!r} differs from the identifier version {version!r}")
        dependencies = tuple(document.get("spec", {}).get("dependencies") or ())
        releases.append(Release(path, identifier, version, family, dependencies))
    return releases


def newest_releases(releases: list[Release]) -> dict[str, Release]:
    newest: dict[str, Release] = {}
    for release in releases:
        current = newest.get(release.family)
        if current is None or version_key(release.version) > version_key(current.version):
            newest[release.family] = release
    return newest


def stale_dependents(releases: list[Release]) -> list[StaleDependency]:
    newest = newest_releases(releases)
    result = []
    for release in sorted(newest.values(), key=lambda item: item.id):
        replacements = {}
        for dependency in release.dependencies:
            family, _ = split_identifier(dependency)
            latest = newest.get(family)
            if latest is not None and latest.id != dependency and version_key(latest.version) > version_key(split_identifier(dependency)[1]):
                replacements[dependency] = latest.id
        if replacements:
            result.append(StaleDependency(release, replacements))
    return result


def release_path(release: Release, version: str) -> Path:
    if release.path.name == DEFINITION_FILE and release.path.parent.name == release.version:
        return release.path.parent.parent / version / DEFINITION_FILE
    if release.version in release.path.name:
        return release.path.with_name(release.path.name.replace(release.version, version))
    raise ValueError(f"{release.path}: cannot derive the file name of release {version}")


def write_next_release(stale: StaleDependency) -> Path:
    release = stale.release
    version = next_version(release.version)
    family, _ = split_identifier(release.id)
    identifier = f"{family}/{version}/{DEFINITION_FILE}"
    destination = release_path(release, version)
    if destination.exists():
        raise FileExistsError(f"{destination} already exists; resolve the dependency by hand")
    text = release.path.read_text(encoding="utf-8")
    text = text.replace(release.id, identifier)
    text, count = re.subn(
        r"^(  version: )(['\"]?)" + re.escape(release.version) + r"\2[ \t]*$",
        lambda match: f"{match.group(1)}{match.group(2)}{version}{match.group(2)}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise ValueError(f"{release.path}: metadata.version line not found")
    for old, new in stale.replacements.items():
        text = text.replace(old, new)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    written = read_document(destination)
    expected = sorted(stale.replacements.get(item, item) for item in release.dependencies)
    if written["metadata"]["id"] != identifier or str(written["metadata"].get("version")) != version or sorted(written["spec"].get("dependencies") or ()) != expected:
        destination.unlink()
        raise ValueError(f"{destination}: the written release does not have the expected identity and dependencies")
    return destination


def describe(stale: StaleDependency, root: Path) -> str:
    lines = [f"{stale.release.path.relative_to(root)} ({stale.release.version}) depends on older releases:"]
    for old, new in sorted(stale.replacements.items()):
        lines.append(f"  {old}\n    -> newest is {new}")
    return "\n".join(lines)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="exit 1 when a dependent is stale")
    mode.add_argument("--write", action="store_true", help="add the next release of each stale dependent")
    args = parser.parse_args(arguments)
    root = args.root.resolve()

    stale = stale_dependents(discover(root))
    if not stale:
        print("Every extension's newest release depends on the newest releases of its dependencies.")
        return 0
    if not args.write:
        for item in stale:
            print(describe(item, root))
        return 1 if args.check else 0
    while stale:
        for item in stale:
            destination = write_next_release(item)
            print(f"{describe(item, root)}\n  wrote {destination.relative_to(root)}")
        stale = stale_dependents(discover(root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
