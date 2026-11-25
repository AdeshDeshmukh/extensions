"""Plan a direct dependency's package from a dependent package's binding model.

A package that adds fields to a dependency's kinds, such as env-configuration
adding configuration to common-integrations' cache, has examples that call the
dependency's declaration function with the dependency's interface types. The
binding model already carries the dependency's vocabulary and schemas, and
build_plan keeps only what the root extension owns, so planning the same model
with the dependency as root yields the dependency package's names.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .emitter import EmissionPlan, build_plan
from .package import DiagnosticError, PackageDependency


def dependency_model(model: dict[str, Any], extension: str) -> dict[str, Any] | None:
    """Narrow a binding model to the closure of one of its extensions, with it as root."""
    by_id = {item["id"]: item for item in model.get("extensions", [])}
    root = by_id.get(extension)
    if root is None:
        return None
    closure: set[str] = set()

    def visit(identifier: str) -> None:
        item = by_id.get(identifier)
        if item is None or identifier in closure:
            return
        closure.add(identifier)
        for dependency in item.get("dependencies", []):
            visit(dependency)

    visit(extension)
    vocabulary = model.get("vocabulary", {})

    def owned(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        return [item for item in items or [] if item.get("owner") in closure]

    declarations = [*vocabulary.get("ownedDeclarations", []), *vocabulary.get("importedDeclarations", [])]
    narrowed_vocabulary = {
        **vocabulary,
        "owners": owned(vocabulary.get("owners")),
        "ownedDeclarations": [item for item in declarations if item["owner"] == extension],
        "importedDeclarations": [
            item for item in declarations if item["owner"] != extension and item["owner"] in closure
        ],
        "interfaces": owned(vocabulary.get("interfaces")),
        "conditionFields": owned(vocabulary.get("conditionFields")),
        "interfaceFields": owned(vocabulary.get("interfaceFields")),
        "valueDomains": owned(vocabulary.get("valueDomains")),
    }
    identity = {key: root[key] for key in ("id", "version", "semanticSha256") if key in root}
    return {
        **model,
        "rootExtension": identity,
        "extensions": [item for item in model["extensions"] if item["id"] in closure],
        "vocabulary": narrowed_vocabulary,
        "schemas": owned(model.get("schemas")),
    }


def dependency_plan(plan: EmissionPlan, model: dict[str, Any], extension: str) -> EmissionPlan | None:
    """Plan a direct dependency's package, or None when it cannot be planned."""
    dependency = next((item for item in plan.target.dependencies if item.extension == extension), None)
    narrowed = dependency_model(model, extension)
    if dependency is None or narrowed is None:
        return None
    direct = next(item for item in narrowed["extensions"] if item["id"] == extension).get("dependencies", [])
    target = replace(
        plan.target,
        package_key=dependency.distribution_name,
        root_extension=extension,
        distribution_name=dependency.distribution_name,
        import_package=dependency.import_package,
        version=dependency.version,
        # The dependency's own providers only name its imports, which examples never use.
        dependencies=tuple(
            PackageDependency(item, f"unresolved-{index}", f"unresolved_{index}", "0.0.0")
            for index, item in enumerate(direct)
        ),
    )
    try:
        return build_plan(narrowed, target)
    except DiagnosticError:
        return None
