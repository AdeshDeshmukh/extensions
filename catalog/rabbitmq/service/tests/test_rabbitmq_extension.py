"""Check RabbitMQ access requirements against the published schema."""

from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parents[2]
EXTENSION_ID = (
    "https://runtimeconditions.io/extensions/rabbitmq/service/0.1.0/"
    "runtimeconditions.extension.yaml"
)
PUBLISH = {
    "resource": "exchange",
    "action": "publish",
    "exchange": "order-events",
    "routingKey": "orders.created",
}
CONSUME = {"resource": "queue", "action": "consume", "queue": "order-workers"}


def definition():
    path = ROOT / "releases/0.1.0/runtimeconditions.extension.yaml"
    assert path.is_file(), "RabbitMQ release is missing"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def validator():
    schema = definition()["spec"]["schemas"][0]["schema"]
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def condition(operations, virtual_host="/orders"):
    return {
        "kind": "rabbitmq.service",
        "interface": {
            "type": "amqp_0_9_1",
            "virtualHost": virtual_host,
            "operations": operations,
        },
    }


def test_release_and_binding_registration():
    release = definition()
    assert release["metadata"]["id"] == EXTENSION_ID
    assert release["metadata"]["version"] == "0.1.0"
    packages = yaml.safe_load(
        (REPOSITORY / "tooling/extension-bindings/packages.yaml").read_text(
            encoding="utf-8"
        )
    )["packages"]
    assert packages["rabbitmq-service"]["rootExtension"] == EXTENSION_ID
    assert set(packages["rabbitmq-service"]["languages"]) == {
        "go",
        "python",
        "typescript",
        "java",
    }


@pytest.mark.parametrize("operations", [[PUBLISH], [CONSUME], [PUBLISH, CONSUME]])
def test_independent_and_combined_access(operations):
    validator().validate(condition(operations))


@pytest.mark.parametrize(
    "exchange,routing_key", [("", "orders"), ("", ""), ("events", "")]
)
def test_empty_exchange_and_routing_key_are_explicit_values(exchange, routing_key):
    validator().validate(
        condition([{**PUBLISH, "exchange": exchange, "routingKey": routing_key}])
    )


def test_permission_alias_is_not_a_publishing_target():
    value = condition([{**PUBLISH, "exchange": "amq.default"}])
    assert list(validator().iter_errors(value))


@pytest.mark.parametrize("field", ["virtualHost", "exchange", "routingKey", "queue"])
def test_short_strings_reject_binary_zero(field):
    value = condition([dict(CONSUME if field == "queue" else PUBLISH)])
    if field == "virtualHost":
        value["interface"][field] = "/orders\x00"
    else:
        value["interface"]["operations"][0][field] = "orders\x00"
    assert list(validator().iter_errors(value))


@pytest.mark.parametrize(
    "operation",
    [
        {},
        {"resource": "exchange", "action": "publish", "exchange": "events"},
        {"resource": "exchange", "action": "publish", "routingKey": "orders"},
        {"resource": "queue", "action": "consume"},
        {**PUBLISH, "resource": "queue"},
        {**CONSUME, "action": "publish"},
        {**PUBLISH, "action": "declare"},
        {**CONSUME, "queue": ""},
        {**CONSUME, "queue": None},
        {**PUBLISH, "routingKey": None},
        {**PUBLISH, "exchange": ["events"]},
        {**PUBLISH, "queue": "orders"},
        {**CONSUME, "exchange": "events"},
        {**CONSUME, "typo": "orders"},
    ],
)
def test_rejects_incomplete_or_mismatched_operations(operation):
    assert list(validator().iter_errors(condition([operation])))


@pytest.mark.parametrize("operations", [[], [PUBLISH, PUBLISH], PUBLISH, None])
def test_rejects_empty_duplicate_or_nonarray_operations(operations):
    assert list(validator().iter_errors(condition(operations)))


@pytest.mark.parametrize("field", ["type", "virtualHost", "operations"])
def test_interface_requires_explicit_connection_scope(field):
    value = condition([PUBLISH])
    del value["interface"][field]
    assert list(validator().iter_errors(value))


@pytest.mark.parametrize("virtual_host", ["", None, 42])
def test_rejects_invalid_virtual_hosts(virtual_host):
    assert list(validator().iter_errors(condition([CONSUME], virtual_host)))


def test_root_and_protocol_identity_are_enforced():
    value = condition([PUBLISH])
    value["kind"] = "kafka.service"
    assert list(validator().iter_errors(value))
    value = condition([PUBLISH])
    value["interface"]["type"] = "amqp_1_0"
    assert list(validator().iter_errors(value))


@pytest.mark.parametrize("name", ["orders.*", "a+b", "a/b", "équipe", "amq.direct"])
def test_names_and_routing_keys_are_literal_unicode_strings(name):
    validator().validate(
        condition(
            [
                {**PUBLISH, "exchange": name, "routingKey": name},
                {**CONSUME, "queue": name},
            ],
            virtual_host="/équipe",
        )
    )


def test_character_bounds_do_not_claim_to_enforce_utf8_byte_limits():
    validator().validate(condition([{**CONSUME, "queue": "a" * 255}]))
    assert list(validator().iter_errors(condition([{**CONSUME, "queue": "a" * 256}])))
    # Adapters or clients check encoded byte limits; JSON Schema counts characters.
    validator().validate(condition([{**CONSUME, "queue": "é" * 128}]))


def test_outer_objects_remain_open_for_composition():
    value = condition([CONSUME])
    value["configuration"] = {"env": [{"property": "url", "name": "AMQP_URL"}]}
    value["interface"]["otherExtensionField"] = "value"
    validator().validate(value)


def test_shared_operation_descriptions_survive_branch_merging():
    release = definition()
    schema = release["spec"]["schemas"][0]["schema"]
    branches = schema["properties"]["interface"]["properties"]["operations"]["items"][
        "oneOf"
    ]
    for field in ("resource", "action"):
        assert (
            len({branch["properties"][field]["description"] for branch in branches})
            == 1
        )


@pytest.mark.parametrize(
    "filename,operations",
    [
        ("publisher-profile.yaml", [PUBLISH]),
        ("consumer-profile.yaml", [CONSUME]),
        (
            "default-exchange-publisher-profile.yaml",
            [{**PUBLISH, "exchange": "", "routingKey": "order-workers"}],
        ),
    ],
)
def test_examples_preserve_resource_requirements(filename, operations):
    profile = yaml.safe_load((ROOT / "examples" / filename).read_text(encoding="utf-8"))
    assert profile["extensions"] == [f"{EXTENSION_ID}:0.1.0"]
    assert len(profile["conditions"]) == 1
    value = profile["conditions"][0]
    assert value["interface"]["virtualHost"] == "/orders"
    assert value["interface"]["operations"] == operations
    validator().validate(value)
