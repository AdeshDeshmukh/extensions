"""Extension descriptions become docstrings without changing the generated API."""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from runtimeconditions_binding_emitter.emitter import EmissionPlan, build_plan
from runtimeconditions_binding_emitter.metadata import render_resources
from runtimeconditions_binding_emitter.package import load_target
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


if __name__ == "__main__":
    test_descriptions_become_docstrings()
    test_docstrings_do_not_change_the_api()
    test_readme_examples_run_against_the_generated_package()
    print("ok")
