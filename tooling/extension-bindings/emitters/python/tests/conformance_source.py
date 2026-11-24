"""A synthetic consumer of a generated API, used only by the tests.

It builds the source of a module that constructs every generated type and calls every declaration, so a test can check
the generated API without a real workload. It is test input, never emitter output, and so it lives with the tests and
not in the emitter.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from runtimeconditions_binding_emitter.emitter import EmissionPlan, TypeExpr, TypePlan
from runtimeconditions_binding_emitter.package import DiagnosticError, fail
from runtimeconditions_binding_emitter.source import _header


def _sample(
    expr: TypeExpr,
    plan: EmissionPlan,
    types: dict[str, TypePlan],
    active: tuple[str, ...] = (),
    selected_optional: str | None = None,
) -> str:
    if expr.kind == "string":
        return '"sample"'
    if expr.kind == "boolean":
        return "True"
    if expr.kind == "integer":
        return "1"
    if expr.kind == "number":
        return "1.0"
    if expr.kind == "null":
        return "None"
    if expr.kind == "any":
        return '"sample"'
    if expr.kind == "array":
        return f"({_sample(expr.arguments[0], plan, types, active)},)"
    if expr.kind == "map":
        return '{"sample": ' + _sample(expr.arguments[0], plan, types, active) + "}"
    if expr.kind == "union":
        # Use the first variant that has a finite sample. A recursive union such as
        # list[Self] | dict[str, Self] | str is sampled through its scalar variant.
        for index, argument in enumerate(expr.arguments):
            try:
                return _sample(argument, plan, types, active)
            except DiagnosticError:
                if index == len(expr.arguments) - 1:
                    raise
        raise AssertionError("union has no variants")
    name = plan.names[expr.symbol_key]
    type_plan = types[name]
    if type_plan.kind == "enum":
        return f"b.{name}.{type_plan.members[0][0]}"
    if type_plan.kind == "alias":
        if name in active:
            fail(
                "model",
                "RCP1012",
                type_plan.coordinate,
                "recursive alias has no finite conformance sample",
            )
        assert type_plan.expression is not None
        return _sample(type_plan.expression, plan, types, active + (name,))
    if type_plan.kind == "object":
        repeated = name in active
        optional_fields = [
            field.name for field in type_plan.fields if field.default_none
        ]
        if selected_optional is None and optional_fields:
            selected_optional = optional_fields[0]
        arguments: list[str] = []
        for field in type_plan.fields:
            if field.default_none and (repeated or field.name != selected_optional):
                value = "None"
            elif (
                repeated
                and not field.default_none
                and field.type_expr.kind in {"object", "ref", "alias"}
            ):
                fail(
                    "model",
                    "RCP1012",
                    field.coordinate,
                    "required recursive field has no finite conformance sample",
                )
            else:
                value = _sample(field.type_expr, plan, types, active + (name,))
            arguments.append(f"{field.name}={value}")
        return f"b.{name}({', '.join(arguments)})"
    raise AssertionError(f"unplanned type kind {type_plan.kind}")


def _field_groups(fields: list[TypePlan]) -> list[list[TypePlan]]:
    groups: dict[str, list[TypePlan]] = defaultdict(list)
    for field in fields:
        groups[field.schema_coordinate].append(field)
    return [
        sorted(groups[key], key=lambda item: item.name) for key in sorted(groups)
    ] or [[]]


def _conformance_source(plan: EmissionPlan, model: dict[str, Any]) -> str:
    lines = [
        _header(model).rstrip("\n"),
        "from __future__ import annotations",
        "",
        "from . import bindings as b",
        "",
        "",
        "def exercise() -> None:",
    ]
    if plan.uses_json_value:
        lines.extend(
            (
                '    _json_scalar: b.JSONValue = "sample"',
                '    _json_array: b.JSONValue = ("sample",)',
                '    _json_map: b.JSONValue = {"sample": "sample"}',
            )
        )
    types = {item.name: item for item in plan.types}
    object_variables: dict[str, str] = {}
    alternate_objects: dict[str, list[str]] = defaultdict(list)
    for index, type_plan in enumerate(sorted(plan.types, key=lambda item: item.name)):
        if type_plan.kind == "enum":
            for member_index, (member, _) in enumerate(type_plan.members):
                lines.append(
                    f"    _enum_{index}_{member_index}: b.{type_plan.name} = "
                    f"b.{type_plan.name}.{member}"
                )
        elif type_plan.kind == "alias":
            assert type_plan.expression is not None
            sample = _sample(type_plan.expression, plan, types)
            lines.append(f"    _alias_{index}: b.{type_plan.name} = {sample}")
            if type_plan.expression.kind == "union":
                for variant_index, variant in enumerate(type_plan.expression.arguments):
                    sample = _sample(variant, plan, types)
                    lines.append(
                        f"    _variant_{index}_{variant_index}: b.{type_plan.name} = {sample}"
                    )
        else:
            object_expr = TypeExpr(
                "object",
                symbol_key=next(
                    key
                    for key, value in plan.names.items()
                    if value == type_plan.name and key.startswith("type:")
                ),
            )
            sample = _sample(object_expr, plan, types)
            variable = f"_object_{index}"
            object_variables[type_plan.name] = variable
            lines.append(f"    {variable}: b.{type_plan.name} = {sample}")
            optional_fields = [
                field.name for field in type_plan.fields if field.default_none
            ]
            for optional_index, optional_name in enumerate(
                optional_fields[1:], start=1
            ):
                sample = _sample(
                    object_expr, plan, types, selected_optional=optional_name
                )
                alternate = f"_object_optional_{index}_{optional_index}"
                alternate_objects[type_plan.name].append(alternate)
                lines.append(f"    {alternate}: b.{type_plan.name} = {sample}")

    declaration_index = 0
    for declaration in sorted(plan.declarations, key=lambda item: item.function):
        fields = [
            item
            for item in plan.types
            if item.is_declaration_field
            and item.marker
            and item.marker.owner == plan.target.root_extension
            and item.marker.protocol == declaration.protocol
        ]
        if not fields:
            # A declaration-only package cannot invent an interface supplied
            # by a consumer's other installed dependency. Consumer fixtures
            # provide complete calls; retain native API coverage here.
            lines.append(
                f"    _deferred_declaration_{declaration.function} = "
                f"b.{declaration.function}"
            )
            continue
        for ordered in _field_groups(fields):
            arguments = ", ".join(object_variables[item.name] for item in ordered)
            lines.append(
                f"    _declaration_{declaration_index}: b.Declaration = "
                f"b.{declaration.function}({arguments})"
            )
            declaration_index += 1
            for item in ordered:
                for alternate in alternate_objects[item.name]:
                    alternative_arguments = ", ".join(
                        alternate
                        if field.name == item.name
                        else object_variables[field.name]
                        for field in ordered
                    )
                    lines.append(
                        f"    _declaration_{declaration_index}: b.Declaration = "
                        f"b.{declaration.function}({alternative_arguments})"
                    )
                    declaration_index += 1
    imported_index = 0
    for imported_declaration in sorted(
        plan.imported_declarations, key=lambda item: item.function
    ):
        fields = [
            item
            for item in plan.types
            if item.is_declaration_field
            and item.marker
            and item.marker.owner == imported_declaration.owner
            and item.marker.protocol == imported_declaration.protocol
        ]
        if not fields:
            lines.append(
                f"    _deferred_imported_{imported_index} = "
                f"b.{imported_declaration.function}"
            )
            imported_index += 1
            continue
        for ordered in _field_groups(fields):
            arguments = ", ".join(object_variables[item.name] for item in ordered)
            lines.append(
                f"    _imported_{imported_index} = "
                f"b.{imported_declaration.function}({arguments})"
            )
            imported_index += 1
    if len(lines) == 7:
        lines.append("    pass")
    lines.append("")
    return "\n".join(lines)
