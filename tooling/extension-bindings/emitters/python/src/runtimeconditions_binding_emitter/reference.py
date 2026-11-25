"""The README's "Conditions" reference.

One collapsible entry per interface type and per condition field the package
defines, listing every field a user can set, its type, whether it is required,
what it means and its allowed values. It uses <details>, which PyPI and GitHub
render as a line that opens on click.
"""

from __future__ import annotations

import re
from typing import Any

from .emitter import EmissionPlan, TypeExpr, TypePlan
from .examples import root_type, split_documentation, summary


def _cell(text: str) -> str:
    """Prose of a description on one line, without examples, safe inside a table cell."""
    prose = " ".join(item.text for item in split_documentation(text or "") if item.condition is None)
    return " ".join(prose.split()).replace("|", "\\|")


def _headline(text: str) -> str:
    first = summary(text or "")
    match = re.search(r"\.(\s|$)", first)
    return first if match is None else first[: match.start() + 1]


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" and word else "a"


def _rows(plan: EmissionPlan, root: TypePlan) -> list[tuple[str, str, bool, str]]:
    types = {item.name: item for item in plan.types}

    def named(expression: TypeExpr) -> TypePlan | None:
        if not expression.symbol_key:
            return None
        return types.get(plan.names.get(expression.symbol_key, ""))

    def enum_of(expression: TypeExpr, through: frozenset[str] = frozenset()) -> TypePlan | None:
        if expression.kind == "array":
            return enum_of(expression.arguments[0], through)
        target = named(expression)
        if target is None or target.name in through:
            return None
        if target.kind == "enum":
            return target
        if target.kind == "alias" and target.expression is not None:
            return enum_of(target.expression, through | {target.name})
        return None

    def nested(expression: TypeExpr, suffix: str, through: frozenset[str] = frozenset()) -> list[tuple[TypePlan, str]]:
        if expression.kind == "array":
            return nested(expression.arguments[0], suffix + "[]", through)
        if expression.kind == "map":
            return nested(expression.arguments[0], suffix + ".*", through)
        if expression.kind == "union":
            return [item for argument in expression.arguments for item in nested(argument, suffix, through)]
        target = named(expression)
        if target is None or target.name in through:
            return []
        if target.kind == "object":
            return [(target, suffix)]
        if target.kind == "alias" and target.expression is not None:
            return nested(target.expression, suffix, through | {target.name})
        return []

    rows: list[tuple[str, str, bool, str]] = []
    seen: set[str] = set()

    def visit(current: TypePlan, prefix: str, stack: frozenset[str]) -> None:
        for field in current.fields:
            path = prefix + field.name
            if path in seen:
                continue
            seen.add(path)
            parts = [_cell(field.description)]
            values = enum_of(field.type_expr)
            if values is not None:
                parts.append("Values:")
                for index, (_, value) in enumerate(values.members):
                    meaning = _cell(values.member_descriptions[index] if index < len(values.member_descriptions) else "")
                    parts.append(f"`{value}`: {meaning}" if meaning else f"`{value}`")
            rows.append((path, field.annotation, field.required, "<br>".join(part for part in parts if part)))
            for child, suffix in nested(field.type_expr, ""):
                if child.name not in stack:
                    visit(child, f"{path}{suffix}.", stack | {child.name})

    visit(root, "", frozenset({root.name}))
    return rows


def _entry(plan: EmissionPlan, heading: str, sentence: str, root: TypePlan) -> list[str]:
    lines = ["<details>", f"<summary>{heading}</summary>", "", sentence, ""]
    rows = _rows(plan, root)
    if not rows:
        lines.extend(("It has no fields to set.", ""))
    else:
        lines.extend(("| Field | Type | Required | Description |", "| --- | --- | --- | --- |"))
        for path, annotation, required, description in rows:
            type_cell = "`" + annotation.replace("|", "\\|") + "`"
            lines.append(f"| `{path}` | {type_cell} | {'yes' if required else 'no'} | {description} |")
        lines.append("")
    lines.append("</details>")
    return lines


def conditions_reference(plan: EmissionPlan, model: dict[str, Any]) -> list[str]:
    """The README's collapsible reference of every interface and condition field this package defines."""
    root = model["rootExtension"]["id"]
    vocabulary = model.get("vocabulary", {})
    entries: list[list[str]] = []
    interfaces = [item for item in vocabulary.get("interfaces", []) if item.get("owner") == root]
    for item in sorted(interfaces, key=lambda value: (value["kind"], value["type"])):
        type_plan = root_type(plan, model, item["kind"], item["type"], item["type"])
        if type_plan is None:
            continue
        about = _headline(item.get("description", ""))
        heading = f"<b>{item['kind']}</b> · <code>{item['type']}</code>" + (f": {about}" if about else "")
        sentence = f"`{type_plan.name}` is the `interface` of {_article(item['kind'])} `{item['kind']}` Condition."
        entries.append(_entry(plan, heading, sentence, type_plan))
    fields = [item for item in vocabulary.get("conditionFields", []) if item.get("owner") == root]
    for item in sorted(fields, key=lambda value: (value["kind"], value.get("interfaceType", ""))):
        segments = item.get("segments") or []
        source_name = segments[-1]["name"] if segments else item["path"]
        interface_type = item.get("interfaceType", "")
        type_plan = root_type(plan, model, item["kind"], interface_type, source_name)
        if type_plan is None:
            continue
        scope = f" · <code>{interface_type}</code>" if interface_type else ""
        about = _headline(item.get("description", ""))
        heading = f"<code>{item['path']}</code> on <b>{item['kind']}</b>{scope}" + (f": {about}" if about else "")
        sentence = (
            f"`{type_plan.name}` sets the `{item['path']}` field of {_article(item['kind'])} `{item['kind']}` Condition."
        )
        entries.append(_entry(plan, heading, sentence, type_plan))
    if not entries:
        return []
    lines = [
        "## Conditions",
        "",
        "Every Condition part this package defines, with the fields you can set. Select a line to open it.",
    ]
    for entry in entries:
        lines.extend(("", *entry))
    return lines
