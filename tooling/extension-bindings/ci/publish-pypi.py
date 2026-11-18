"""Build the committed Python bindings and collect the ones that are not on PyPI yet.

The files go to a directory; a later workflow step uploads them with PyPI trusted publishing, so no token is
stored. Building and checking never need credentials, so a dry run does everything except the upload.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import tomllib
import yaml

from generation import Target, of_language, select_targets, summarize, write_outputs

LANGUAGE = "python"
TOOLING = Path("tooling/extension-bindings")
INDEX = "https://pypi.org/pypi"


def read_project(root: Path, catalog: dict, target: Target) -> dict:
    """Read a committed pyproject.toml and check it against the catalog entry."""
    key, directory, _ = target
    project = tomllib.loads((root / directory / "pyproject.toml").read_text(encoding="utf-8"))
    expected = catalog["packages"][key.split(":")[0]]["languages"][LANGUAGE]
    found = project["project"]
    if found["name"] != expected["coordinate"] or found["version"] != str(expected["version"]):
        raise ValueError(
            f"{key}: {directory}/pyproject.toml is {found['name']}=={found['version']}, but the catalog says "
            f"{expected['coordinate']}=={expected['version']}"
        )
    return project


def index_name(name: str) -> str:
    """The name PyPI uses in its URLs (PEP 503)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def already_published(name: str, version: str) -> bool:
    """Whether PyPI already has this version. Versions there are immutable, so it is never replaced."""
    try:
        with urllib.request.urlopen(f"{INDEX}/{index_name(name)}/{version}/json", timeout=30) as response:
            return json.load(response)["info"]["version"] == version
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise


def resource_files(project: dict) -> list[str]:
    """The binding resources the package must install next to its code, from its package-data."""
    data = project["tool"]["setuptools"]["package-data"]
    return sorted(
        f"{package}/{name}"
        for package, names in data.items()
        for name in names
        if name.startswith("runtimeconditions.")
    )


def check_wheel(wheel: Path, project: dict) -> None:
    """A wheel that lacks a binding resource installs a package the profiler would refuse."""
    with zipfile.ZipFile(wheel) as archive:
        present = set(archive.namelist())
    missing = [name for name in resource_files(project) if name not in present]
    if missing:
        raise ValueError(f"{wheel.name} does not contain {', '.join(missing)}")


def build_package(source: Path, destination: Path) -> list[Path]:
    """Build the sdist and the wheel from a copy of the committed tree, so nothing is left in the checkout."""
    with tempfile.TemporaryDirectory(prefix="pypi-bindings-") as name:
        copy = Path(name) / "package"
        shutil.copytree(source, copy, ignore=shutil.ignore_patterns("build", "dist", "*.egg-info", "__pycache__"))
        out = Path(name) / "out"
        subprocess.run([sys.executable, "-m", "build", "--outdir", str(out), str(copy)], check=True, text=True)
        built = sorted(out.iterdir())
        for path in built:
            shutil.copy2(path, destination / path.name)
    return [destination / path.name for path in built]


def publish(root: Path, targets: list[Target], dist: Path) -> int:
    """Build every selected package that is not on PyPI yet into dist, and return how many there are."""
    selected = of_language(targets, LANGUAGE)
    if not selected:
        summarize("No Python targets selected; nothing to publish.")
        return 0
    if dist.exists() and any(dist.iterdir()):
        raise ValueError(f"{dist} must be new or empty")
    dist.mkdir(parents=True, exist_ok=True)
    catalog = yaml.safe_load((root / TOOLING / "packages.yaml").read_text(encoding="utf-8"))
    new = []
    for target in selected:
        project = read_project(root, catalog, target)
        name, version = project["project"]["name"], project["project"]["version"]
        if already_published(name, version):
            summarize(f"{name}=={version} is already on PyPI; skipped.")
            continue
        files = build_package(root / target[1], dist)
        wheels = [path for path in files if path.suffix == ".whl"]
        if len(wheels) != 1 or len(files) != 2:
            raise ValueError(f"{name}: expected one wheel and one sdist, built {[path.name for path in files]}")
        check_wheel(wheels[0], project)
        new.append(f"{name}=={version}")
        summarize(f"Built and checked {name}=={version}.")
    summarize("To publish: " + (", ".join(new) if new else "nothing new."))
    return len(new)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--targets", default="")
    parser.add_argument("--dist", type=Path, required=True, help="new or empty directory for the files to upload")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        targets = select_targets(root / TOOLING / "packages.yaml", args.targets)
        count = publish(root, targets, args.dist.resolve())
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError, urllib.error.URLError) as error:
        parser.exit(1, f"PyPI publication failed: {error}\n")
    write_outputs(count=str(count))


if __name__ == "__main__":
    main()
