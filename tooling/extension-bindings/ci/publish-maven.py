"""Build the committed Java bindings and upload them to Maven Central through the Central Portal.

A dry run builds and checks every selected package, with sources and Javadoc jars, and installs each into a
throwaway local repository so that a package finds the selected packages it depends on. A real run signs each
package and uploads it with manual release: the Portal validates the upload, and nothing is public until someone
presses Publish on the deployment. Versions already on Maven Central are never uploaded again.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

from generation import Target, of_language, select_targets, summarize, write_outputs

LANGUAGE = "java"
TOOLING = Path("tooling/extension-bindings")
CENTRAL = "https://repo1.maven.org/maven2"
DEPLOYMENTS = "https://central.sonatype.com/publishing/deployments"
NS = {"m": "http://maven.apache.org/POM/4.0.0"}
Coordinate = tuple[str, str, str]


def text(node: ET.Element | None, path: str) -> str:
    found = node.find(path, NS) if node is not None else None
    return (found.text or "").strip() if found is not None and found.text else ""


def read_pom(root: Path, catalog: dict, target: Target) -> dict:
    """Read a committed pom.xml, check it against the catalog, and check it has what Maven Central requires."""
    key, directory, _ = target
    pom = ET.parse(root / directory / "pom.xml").getroot()
    found = (text(pom, "m:groupId"), text(pom, "m:artifactId"), text(pom, "m:version"))
    expected = catalog["packages"][key.split(":")[0]]["languages"][LANGUAGE]
    group, artifact = expected["coordinate"].split(":")
    if found != (group, artifact, str(expected["version"])):
        raise ValueError(
            f"{key}: {directory}/pom.xml is {':'.join(found)}, but the catalog says "
            f"{group}:{artifact}:{expected['version']}"
        )
    required = {
        "name": text(pom, "m:name"),
        "description": text(pom, "m:description"),
        "url": text(pom, "m:url"),
        "license": text(pom.find("m:licenses/m:license", NS), "m:name"),
        "developer": text(pom.find("m:developers/m:developer", NS), "m:name"),
        "scm url": text(pom, "m:scm/m:url"),
        "scm connection": text(pom, "m:scm/m:connection"),
        "scm developerConnection": text(pom, "m:scm/m:developerConnection"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError(f"{key}: {directory}/pom.xml lacks what Maven Central requires: {', '.join(missing)}")
    dependencies = [
        (text(item, "m:groupId"), text(item, "m:artifactId"), text(item, "m:version"))
        for item in pom.findall("m:dependencies/m:dependency", NS)
    ]
    return {"coordinate": found, "dependencies": dependencies, "directory": root / directory}


def location(coordinate: Coordinate) -> str:
    group, artifact, version = coordinate
    return f"{CENTRAL}/{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}.pom"


def already_published(coordinate: Coordinate) -> bool:
    """Whether Maven Central already has this version. Versions there are immutable."""
    try:
        with urllib.request.urlopen(location(coordinate), timeout=30):
            return True
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise


def publication_order(packages: dict[Coordinate, dict]) -> list[Coordinate]:
    """Order the selected packages so every one comes after the selected packages it depends on."""
    ordered: list[Coordinate] = []
    state: dict[Coordinate, int] = {}

    def visit(coordinate: Coordinate) -> None:
        if state.get(coordinate) == 2:
            return
        if state.get(coordinate) == 1:
            raise ValueError(f"package dependency cycle at {':'.join(coordinate)}")
        state[coordinate] = 1
        for dependency in sorted(packages[coordinate]["dependencies"]):
            if dependency in packages:
                visit(dependency)
        state[coordinate] = 2
        ordered.append(coordinate)

    for coordinate in sorted(packages):
        visit(coordinate)
    return ordered


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, check=True, text=True)


def check_dependencies(coordinate: Coordinate, dependencies: list[Coordinate]) -> None:
    """A real upload is built against Maven Central, so what a package depends on must be public first."""
    for dependency in dependencies:
        if not already_published(dependency):
            raise ValueError(
                f"{':'.join(coordinate)} depends on {':'.join(dependency)}, which is not on Maven Central yet. "
                f"Publish it first, press Publish in the Portal at {DEPLOYMENTS}, wait until it is on Central, "
                "then run this again."
            )


def publish(root: Path, targets: list[Target], execute: bool) -> int:
    """Build and check, and when execute is true upload, the packages Central does not have yet. Return how many."""
    selected = of_language(targets, LANGUAGE)
    if not selected:
        summarize("No Java targets selected; nothing to publish.")
        return 0
    catalog = yaml.safe_load((root / TOOLING / "packages.yaml").read_text(encoding="utf-8"))
    packages = {}
    for target in selected:
        package = read_pom(root, catalog, target)
        packages[package["coordinate"]] = package
    done = []
    with tempfile.TemporaryDirectory(prefix="maven-bindings-") as name:
        workspace = Path(name)
        local_repository = workspace / "repository"
        for coordinate in publication_order(packages):
            label = ":".join(coordinate)
            if already_published(coordinate):
                summarize(f"{label} is already on Maven Central; skipped.")
                continue
            copy = workspace / "sources" / coordinate[1]
            shutil.copytree(packages[coordinate]["directory"], copy, ignore=shutil.ignore_patterns("target"))
            if execute:
                check_dependencies(coordinate, packages[coordinate]["dependencies"])
                run(["mvn", "-B", "-Prelease", "deploy"], copy)
                summarize(
                    f"Uploaded {label} to the Central Portal and it passed validation. It is not public until "
                    f"someone presses Publish on the deployment at {DEPLOYMENTS}."
                )
            else:
                run(
                    ["mvn", "-B", "-Prelease", "-Dgpg.skip=true", f"-Dmaven.repo.local={local_repository}", "install"],
                    copy,
                )
                summarize(f"Built and checked {label}, with sources and Javadoc jars; dry run, nothing was uploaded.")
            done.append(label)
    summarize(("Uploaded: " if execute else "Would upload: ") + (", ".join(done) if done else "nothing new."))
    return len(done)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--targets", default="")
    parser.add_argument("--execute", action="store_true", help="sign and upload; without it the packages are only built and checked")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        targets = select_targets(root / TOOLING / "packages.yaml", args.targets)
        count = publish(root, targets, args.execute)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError, urllib.error.URLError, ET.ParseError) as error:
        parser.exit(1, f"Maven publication failed: {error}\n")
    write_outputs(count=str(count))


if __name__ == "__main__":
    main()
