"""Render example Conditions from extension descriptions as Python code.

Descriptions may contain example Conditions in condition fences, a first-party
documentation convention checked by the normalizer. The Python emitter renders
each example as the declaration call that produces it, in docstrings and in the
package README. An example for a dependency's kind, such as an env-configuration
field on a common-integrations cache, calls the dependency's declaration
function (see dependencies.py). An example that cannot be rendered is shown as
Profile YAML instead.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import yaml

from .emitter import EmissionPlan, TypeExpr, TypePlan

CONDITION_FENCE = "condition"
WIDTH = 88


@dataclass(frozen=True)
class DocSegment:
    text: str = ""
    source: str = ""
    condition: dict[str, Any] | None = None


@dataclass(frozen=True)
class RenderedExample:
    code: str
    # Names to import, per import package, in package order.
    imports: tuple[tuple[str, tuple[str, ...]], ...]


def dedent(text: str) -> str:
    lines = text.split("\n")
    indents = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
    common = min(indents) if indents else 0
    return "\n".join(line[min(common, len(line) - len(line.lstrip())) :] for line in lines)


def split_documentation(text: str) -> list[DocSegment]:
    """Split a description into prose and example Conditions."""
    segments: list[DocSegment] = []
    prose: list[str] = []
    example: list[str] | None = None

    def flush() -> None:
        joined = "\n".join(prose).strip()
        if joined:
            segments.append(DocSegment(text=joined))
        prose.clear()

    for line in text.split("\n"):
        stripped = line.strip()
        if example is None and stripped == "```" + CONDITION_FENCE:
            flush()
            example = []
        elif example is not None and stripped == "```":
            source = "\n".join(example)
            try:
                condition = yaml.safe_load(dedent(source))
            except yaml.YAMLError:
                condition = None
            if isinstance(condition, dict):
                segments.append(DocSegment(source=source, condition=condition))
            else:
                prose.extend(("```yaml", source, "```"))
            example = None
        elif example is not None:
            example.append(line)
        else:
            prose.append(line)
    if example is not None:
        prose.extend(("```" + CONDITION_FENCE, *example))
    flush()
    return segments


def summary(text: str) -> str:
    """The first paragraph of a description, on one line, for registry summaries."""
    prose = next((item.text for item in split_documentation(text) if item.condition is None and item.text), "")
    return " ".join(re.split(r"\n\s*\n", prose)[0].split())


class _ExampleError(Exception):
    pass


# A small syntax tree, laid out the way Ruff formats calls and literals.
@dataclass(frozen=True)
class _Atom:
    text: str


@dataclass(frozen=True)
class _Call:
    name: str
    arguments: tuple[tuple[str, Any], ...]  # (keyword or "", node)


@dataclass(frozen=True)
class _Collection:
    opening: str
    closing: str
    items: tuple[tuple[str, Any], ...]  # (key prefix or "", node)


def _flat(node: Any) -> str:
    if isinstance(node, _Atom):
        return node.text
    if isinstance(node, _Call):
        inner = ", ".join(f"{key}={_flat(value)}" if key else _flat(value) for key, value in node.arguments)
        return f"{node.name}({inner})"
    inner = ", ".join(f"{key}{_flat(value)}" for key, value in node.items)
    return f"{node.opening}{inner}{node.closing}"


def _layout(node: Any, indent: int) -> str:
    flat = _flat(node)
    if indent + len(flat) <= WIDTH or isinstance(node, _Atom):
        return flat
    pad = " " * (indent + 4)
    if isinstance(node, _Call):
        parts = [
            f"{pad}{key + '=' if key else ''}{_layout(value, indent + 4 + len(key) + (1 if key else 0))},"
            for key, value in node.arguments
        ]
        return f"{node.name}(\n" + "\n".join(parts) + "\n" + " " * indent + ")"
    collection: _Collection = node
    parts = [f"{pad}{key}{_layout(value, indent + 4 + len(key))}," for key, value in collection.items]
    return f"{collection.opening}\n" + "\n".join(parts) + "\n" + " " * indent + collection.closing


class _Renderer:
    def __init__(self, plan: EmissionPlan, imports: dict[str, set[str]]) -> None:
        self.plan = plan
        self.types = {item.name: item for item in plan.types}
        self.imports = imports

    def use(self, name: str) -> None:
        self.imports.setdefault(self.plan.target.import_package, set()).add(name)

    def value(self, expression: TypeExpr, value: Any) -> Any:
        kind = expression.kind
        if kind == "string" and isinstance(value, str):
            return _Atom(json.dumps(value, ensure_ascii=False))
        if kind == "boolean" and isinstance(value, bool):
            return _Atom("True" if value else "False")
        if kind == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return _Atom(str(value))
        if kind == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return _Atom(repr(value))
        if kind == "null" and value is None:
            return _Atom("None")
        if kind == "any":
            return _Atom(repr(value))
        if kind == "array" and isinstance(value, list):
            return _Collection("[", "]", tuple(("", self.value(expression.arguments[0], item)) for item in value))
        if kind == "map" and isinstance(value, dict):
            return _Collection(
                "{",
                "}",
                tuple(
                    (f"{json.dumps(key, ensure_ascii=False)}: ", self.value(expression.arguments[0], value[key]))
                    for key in sorted(value)
                ),
            )
        if kind == "union":
            for variant in expression.arguments:
                try:
                    return self.value(variant, value)
                except _ExampleError:
                    continue
            raise _ExampleError("no union variant matches")
        if expression.symbol_key:
            return self.named(self.plan.names[expression.symbol_key], value)
        raise _ExampleError(f"value {value!r} does not fit {kind}")

    def named(self, name: str, value: Any) -> Any:
        type_plan = self.types.get(name)
        if type_plan is None:
            raise _ExampleError(f"unknown type {name}")
        if type_plan.kind == "enum":
            for member, member_value in type_plan.members:
                if member_value == value:
                    self.use(name)
                    return _Atom(f"{name}.{member}")
            raise _ExampleError(f"{value!r} is not a {name}")
        if type_plan.kind == "alias":
            assert type_plan.expression is not None
            return self.value(type_plan.expression, value)
        return self.object(type_plan, value)

    def object(self, type_plan: TypePlan, value: Any) -> Any:
        if not isinstance(value, dict):
            raise _ExampleError(f"{type_plan.name} expects an object")
        known = {"type", *(field.source_name for field in type_plan.fields)}
        unknown = sorted(set(value) - known)
        if unknown:
            raise _ExampleError(f"{type_plan.name} has no field {unknown[0]}")
        self.use(type_plan.name)
        return _Call(
            type_plan.name,
            tuple(
                (field.name, self.value(field.type_expr, value[field.source_name]))
                for field in type_plan.fields
                if field.source_name in value
            ),
        )

    def root(self, model: dict[str, Any], kind: str, interface_type: str, source_name: str) -> TypePlan | None:
        schemas = {item["coordinate"]: item for item in model.get("schemas", [])}
        candidates = []
        for item in self.plan.types:
            schema = schemas.get(item.schema_coordinate)
            if (
                item.is_declaration_field
                and item.source_name == source_name
                and schema is not None
                and schema.get("kind", schema.get("id")) == kind
                and schema.get("interfaceType") in (None, interface_type)
            ):
                candidates.append((schema.get("interfaceType") != interface_type, item.name, item))
        return sorted(candidates, key=lambda entry: entry[:2])[0][2] if candidates else None

    def condition(
        self, model: dict[str, Any], condition: dict[str, Any], dependency: _Renderer | None = None
    ) -> Any:
        """Render a Condition as a declaration call.

        Values come from this package first, then from the dependency package
        that declares the kind, when one is given.
        """
        kind = condition.get("kind")
        declarer: _Renderer = self
        declaration = next((item for item in self.plan.declarations if item.kind == kind), None)
        if declaration is None and dependency is not None:
            declarer = dependency
            declaration = next((item for item in dependency.plan.declarations if item.kind == kind), None)
        if declaration is None:
            raise _ExampleError(f"kind {kind!r} is not declared here")
        sources = [self] if declarer is self else [self, declarer]

        def render(interface_type: str, key: str, value: Any, label: str) -> Any:
            for source in sources:
                type_plan = source.root(model, str(kind), interface_type, key)
                if type_plan is None:
                    continue
                single = len(type_plan.fields) == 1 and type_plan.fields[0].source_name == "value"
                return source.object(type_plan, {"value": value} if single and not isinstance(value, dict) else value)
            raise _ExampleError(f"{label} {key!r} is not defined here")

        arguments: list[tuple[str, Any]] = []
        interface = condition.get("interface")
        interface_type = interface.get("type", "") if isinstance(interface, dict) else ""
        if interface is not None:
            arguments.append(("", render(interface_type, interface_type, interface, "interface")))
        for key in sorted(condition):
            if key in {"kind", "interface", "name"}:
                continue
            arguments.append(("", render(interface_type, key, condition[key], "condition field")))
        declarer.use(declaration.function)
        return _Call(declaration.function, tuple(arguments))


def root_type(
    plan: EmissionPlan, model: dict[str, Any], kind: str, interface_type: str, source_name: str
) -> TypePlan | None:
    """The declaration-field type of an interface or condition field root, if this package defines one."""
    return _Renderer(plan, {}).root(model, kind, interface_type, source_name)


@dataclass(frozen=True)
class ConditionFieldDoc:
    name: str
    kind: str
    interface_type: str
    path: str
    description: str


def condition_field_docs(plan: EmissionPlan, model: dict[str, Any]) -> list[ConditionFieldDoc]:
    """The root extension's documented condition fields, in type-name order."""
    root = model["rootExtension"]["id"]
    renderer = _Renderer(plan, {})
    docs: list[ConditionFieldDoc] = []
    for field in model.get("vocabulary", {}).get("conditionFields", []):
        if field.get("owner") != root or not field.get("description"):
            continue
        segments = field.get("segments") or []
        source_name = segments[-1]["name"] if segments else field["path"]
        type_plan = renderer.root(model, field["kind"], field.get("interfaceType", ""), source_name)
        if type_plan is None:
            continue
        docs.append(
            ConditionFieldDoc(
                type_plan.name, field["kind"], field.get("interfaceType", ""), field["path"], field["description"]
            )
        )
    return sorted(docs, key=lambda item: item.name)


def render_examples(plan: EmissionPlan, model: dict[str, Any]) -> dict[str, RenderedExample]:
    """Render every example Condition in the model's descriptions, keyed by source."""
    from .dependencies import dependency_plan

    root = model["rootExtension"]["id"]
    vocabulary = model.get("vocabulary", {})
    texts = [item.get("description", "") for item in model.get("extensions", []) if item["id"] == root]
    texts += [item.get("description", "") for item in vocabulary.get("ownedDeclarations", [])]
    texts += [
        item.get("description", "") for item in vocabulary.get("conditionFields", []) if item.get("owner") == root
    ]
    dependencies: dict[str, EmissionPlan | None] = {}

    def dependency_for(kind: Any) -> EmissionPlan | None:
        imported = next((item for item in plan.imported_declarations if item.kind == kind), None)
        if imported is None:
            return None
        if imported.owner not in dependencies:
            dependencies[imported.owner] = dependency_plan(plan, model, imported.owner)
        return dependencies[imported.owner]

    rendered: dict[str, RenderedExample] = {}
    for text in texts:
        for segment in split_documentation(text or ""):
            if segment.condition is None or segment.source in rendered:
                continue
            imports: dict[str, set[str]] = {}
            renderer = _Renderer(plan, imports)
            provider = dependency_for(segment.condition.get("kind"))
            try:
                node = renderer.condition(
                    model, segment.condition, None if provider is None else _Renderer(provider, imports)
                )
            except _ExampleError:
                continue
            rendered[segment.source] = RenderedExample(
                _layout(node, 0),
                tuple((package, tuple(sorted(names))) for package, names in sorted(imports.items())),
            )
    return rendered


def is_identifier(text: str) -> bool:
    return re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text) is not None
