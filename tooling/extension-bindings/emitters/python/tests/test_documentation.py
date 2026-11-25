"""Extension descriptions become docstrings without changing the generated API."""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from runtimeconditions_binding_emitter.dependencies import dependency_plan
from runtimeconditions_binding_emitter.emitter import EmissionPlan, build_plan
from runtimeconditions_binding_emitter.metadata import render_resources
from runtimeconditions_binding_emitter.package import PackageDependency, load_target
from runtimeconditions_binding_emitter.examples import render_examples
from runtimeconditions_binding_emitter.source import (
    _CURRENT_EXAMPLES,
    _bindings_source,
    _init_source,
    readme_source,
)
from runtimeconditions_binding_emitter.verification import api_surface, verify_package

TOOLING = Path(__file__).resolve().parents[3]
MODEL = TOOLING / "model/conformance/expected/14-documented-extension/runtimeconditions.binding-model.yaml"
DEPENDENT_MODEL = TOOLING / "model/conformance/expected/15-documented-dependent/runtimeconditions.binding-model.yaml"
TARGET = TOOLING / "emitters/python/testdata/package-target.yaml"


def _model_and_plan() -> tuple[dict, EmissionPlan]:
    model = yaml.safe_load(MODEL.read_text(encoding="utf-8"))
    target = replace(
        load_target(TARGET),
        root_extension=model["rootExtension"]["id"],
        emitter_sha256="0" * 64,
        dependencies=(),
    )
    return model, build_plan(model, target)


def _write(plan: EmissionPlan, model: dict, directory: Path) -> str:
    # Unformatted source: the docstrings and the API do not depend on Ruff.
    directory.mkdir(parents=True)
    bindings = _bindings_source(plan, model)
    (directory / "__init__.py").write_text(_init_source(plan, model), encoding="utf-8")
    (directory / "bindings.py").write_text(bindings, encoding="utf-8")
    key = f"src/{plan.target.import_package}/runtimeconditions.bindings.yaml"
    (directory / "runtimeconditions.bindings.yaml").write_text(
        render_resources(plan, model)[key], encoding="utf-8"
    )
    return bindings


def _without_docs(plan: EmissionPlan) -> EmissionPlan:
    return replace(
        plan,
        package_description="",
        declarations=tuple(replace(item, description="") for item in plan.declarations),
        types=tuple(
            replace(
                item,
                description="",
                member_descriptions=tuple("" for _ in item.member_descriptions),
                fields=tuple(replace(field, description="") for field in item.fields),
            )
            for item in plan.types
        ),
    )


def test_descriptions_become_docstrings() -> None:
    model, plan = _model_and_plan()
    with TemporaryDirectory() as temporary:
        source = _write(plan, model, Path(temporary) / "pkg")
        ast.parse(source, feature_version=(3, 11))
        verify_package(plan, Path(temporary) / "pkg", model)
    for expected in (
        # Extension summary as the module docstring.
        "Common integrations a workload depends on",
        # Kind description on the declaration function.
        "The workload calls another service through a network API",
        # Interface type description on the interface class.
        "A key/value store, such as Redis or Memcached.",
        # Interface field meaning, then the JSON Schema rule, after the field.
        '    engine: KeyValueEngine | None = None\n    """The cache engine the workload needs.',
        "Optional. When present, must be one of the defined engine values.",
        # Field value description after the enum member.
        'REDIS = "redis"\n    """Redis, an in-memory key/value store."""',
    ):
        assert expected in source, expected


def test_docstrings_do_not_change_the_api() -> None:
    model, plan = _model_and_plan()
    with TemporaryDirectory() as temporary:
        _write(plan, model, Path(temporary) / "documented")
        _write(_without_docs(plan), model, Path(temporary) / "plain")
        assert api_surface(Path(temporary) / "documented") == api_surface(
            Path(temporary) / "plain"
        )


def test_readme_examples_run_against_the_generated_package() -> None:
    model, plan = _model_and_plan()
    examples = render_examples(plan, model)
    readme = readme_source(plan, model, examples)
    assert readme is not None
    blocks = re.findall(r"```python\n(.*?)```", readme, flags=re.S)
    assert len(blocks) >= 5, readme
    # A collapsible reference lists every interface with its fields, types and allowed values.
    for expected in (
        "## Conditions",
        "<summary><b>cache</b> · <code>key_value</code>: A key/value store, such as Redis or Memcached.</summary>",
        "`KeyValue` is the `interface` of a `cache` Condition.",
        "| `engine` | `KeyValueEngine \\| None` | no | The cache engine the workload needs.",
        "<br>Values:<br>`memcached`: Memcached, a simple in-memory cache.<br>`redis`: Redis, an in-memory key/value store. |",
        "| `operations[].method` | `Method` | yes |",
    ):
        assert expected in readme, f"{expected}\n{readme}"
    _CURRENT_EXAMPLES.update(examples)
    try:
        bindings = _bindings_source(plan, model)
    finally:
        _CURRENT_EXAMPLES.clear()
    # The same code is in the declaration function's docstring.
    assert "cache(KeyValue(engine=KeyValueEngine.REDIS))" in bindings
    with TemporaryDirectory() as temporary:
        package = Path(temporary) / plan.target.import_package
        package.mkdir()
        (package / "__init__.py").write_text(_init_source(plan, model), encoding="utf-8")
        (package / "bindings.py").write_text(bindings, encoding="utf-8")
        sys.path.insert(0, temporary)
        try:
            for block in blocks:
                # Running the example constructs every dataclass and enum it names.
                exec(compile(block, "README example", "exec"), {})
        finally:
            sys.path.remove(temporary)
            for name in [key for key in sys.modules if key.startswith(plan.target.import_package)]:
                del sys.modules[name]


def _exported(plan: EmissionPlan) -> list[str]:
    return sorted([f"type {item.name}" for item in plan.types] + [f"def {item.function}" for item in plan.declarations])


def test_dependent_readme_examples_call_the_dependency_and_run_against_both_packages() -> None:
    # The dependency is case 14's extension, so its own package is case 14's.
    dependency_model_value, dependency = _model_and_plan()
    dependency = replace(
        dependency,
        target=replace(dependency.target, import_package="rc_documented", distribution_name="rc-documented"),
    )
    dependency = build_plan(dependency_model_value, dependency.target)
    model = yaml.safe_load(DEPENDENT_MODEL.read_text(encoding="utf-8"))
    target = replace(
        load_target(TARGET),
        package_key="rc-documented-dependent",
        root_extension=model["rootExtension"]["id"],
        distribution_name="rc-documented-dependent",
        import_package="rc_documented_dependent",
        emitter_sha256="0" * 64,
        dependencies=(
            PackageDependency(dependency.target.root_extension, "rc-documented", "rc_documented", "1.0.0"),
        ),
    )
    plan = build_plan(model, target)
    # Names planned from the dependent model match the dependency package's own.
    derived = dependency_plan(plan, model, dependency.target.root_extension)
    assert derived is not None
    assert _exported(derived) == _exported(dependency)

    examples = render_examples(plan, model)
    readme = readme_source(plan, model, examples)
    assert readme is not None
    for expected in (
        "## KeyValueConfiguration",
        "`KeyValueConfiguration` sets the `configuration` field of `cache` Conditions with interface type `key_value`.",
        "from rc_documented import KeyValue, KeyValueEngine, cache",
        "from rc_documented_dependent import KeyValueConfiguration, KeyValueConfigurationEnvItem",
        "In a Profile, this declares:",
        "<summary><code>configuration</code> on <b>cache</b> · <code>key_value</code>: How the platform passes the cache address to the workload, as environment variables.</summary>",
        "| `env[].name` | `str` | yes |",
    ):
        assert expected in readme, f"{expected}\n{readme}"
    _assert_ruff_formatted(readme)
    blocks = re.findall(r"```python\n(.*?)```", readme, flags=re.S)
    assert len(blocks) >= 2, readme
    _CURRENT_EXAMPLES.update(examples)
    try:
        bindings = _bindings_source(plan, model)
    finally:
        _CURRENT_EXAMPLES.clear()
    # The condition field's docstring shows the same code.
    assert "cache(\n" in bindings or "cache(KeyValue(" in bindings, bindings
    with TemporaryDirectory() as temporary:
        for package_plan, package_model, source in (
            (dependency, dependency_model_value, _bindings_source(dependency, dependency_model_value)),
            (plan, model, bindings),
        ):
            package = Path(temporary) / package_plan.target.import_package
            package.mkdir()
            (package / "__init__.py").write_text(_init_source(package_plan, package_model), encoding="utf-8")
            (package / "bindings.py").write_text(source, encoding="utf-8")
        sys.path.insert(0, temporary)
        try:
            for block in blocks:
                exec(compile(block, "README example", "exec"), {})
        finally:
            sys.path.remove(temporary)
            for name in [key for key in sys.modules if key.startswith("rc_documented")]:
                del sys.modules[name]


def _assert_ruff_formatted(readme: str) -> None:
    """Ruff formats the Python code blocks of a package's README, so each must already be formatted."""
    import subprocess

    with TemporaryDirectory() as temporary:
        (Path(temporary) / "README.md").write_text(readme, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "format", "--no-cache", "--check", "--diff", "README.md"],
            cwd=temporary,
            capture_output=True,
            text=True,
        )
    assert result.returncode == 0, result.stdout + result.stderr


def test_readme_examples_are_ruff_formatted() -> None:
    model, plan = _model_and_plan()
    readme = readme_source(plan, model, render_examples(plan, model))
    assert readme is not None
    _assert_ruff_formatted(readme)


def test_nested_calls_and_long_imports_are_laid_out_like_ruff() -> None:
    from runtimeconditions_binding_emitter.examples import _Atom, _Call, _layout, import_lines

    spec = _Call(
        "Spec",
        (
            ("format", _Atom("Format.OPENAPI")),
            ("uri", _Atom('"catalog://api/default/todos-api"')),
            ("version", _Atom('"1.0.0"')),
        ),
    )
    node = _Call("api", (("", _Call("Http", (("spec", spec), ("operations", _Atom("None"))))),))
    # Nested arguments are indented four spaces past their line, not aligned after "spec=".
    assert "\n        spec=Spec(\n            format=Format.OPENAPI,\n" in _layout(node, 0)
    names = ("Format", "Http", "HttpOperationsItem", "Method", "Spec", "api")
    assert import_lines("runtimeconditions_common_integrations", names) == [
        "from runtimeconditions_common_integrations import (",
        *(f"    {name}," for name in names),
        ")",
    ]
    assert import_lines("pkg", ("KeyValue", "cache")) == ["from pkg import KeyValue, cache"]


if __name__ == "__main__":
    test_descriptions_become_docstrings()
    test_docstrings_do_not_change_the_api()
    test_readme_examples_run_against_the_generated_package()
    test_dependent_readme_examples_call_the_dependency_and_run_against_both_packages()
    test_readme_examples_are_ruff_formatted()
    test_nested_calls_and_long_imports_are_laid_out_like_ruff()
    print("ok")
