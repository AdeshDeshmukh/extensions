# Kafka service extension

This extension declares the Kafka topic and consumer-group access a workload
requires. It is separate from common integrations so future Kafka SDK mappings
can target the same service contract across languages.

The first release is [`0.1.0`](releases/0.1.0/runtimeconditions.extension.yaml).
Each Condition uses `kind: kafka.service` and `interface.type: service`. Its
`name` identifies the logical Kafka service requirement; resource identities
appear in `interface.operations`.

## Capabilities

| Resource | Action | Required identity | Requirement |
| --- | --- | --- | --- |
| `topic` | `write` | `topic` | Produce records to the named topic. |
| `topic` | `read` | `topic` | Read records from the named topic. |
| `group` | `read` | `groupId` | Coordinate consumption or commit and fetch offsets for the named consumer group. |

Every operation has exactly one of these forms. The operations array must be
nonempty and contain no duplicate entries. Declare separate entries when a
workload reads and writes the same topic, or uses several topics or groups.

A group consumer declares both topic read and group read requirements. A
consumer using explicit partition assignment without group coordination or
Kafka-managed offsets needs only topic read access. Group access alone grants
no topic access; topic access alone grants no group access.

See the [producer](examples/producer-profile.yaml),
[group consumer](examples/group-consumer-profile.yaml), and
[direct consumer](examples/direct-consumer-profile.yaml) examples. The producer
example describes the topic-write capability only; it does not claim that a
client's default idempotent producer configuration is fully covered.

## Fulfillment

An adapter can use these requirements to select a Kafka service and configure
access for the workload identity. Kafka's authorization model distinguishes
`Write` on a topic, `Read` on a topic, and `Read` on a group. Kafka's standard
ACL semantics also imply the corresponding `Describe` access from `Read` or
`Write`; the extension does not add a redundant describe capability.

Topic and group identities are literal and case-sensitive. The extension
does not authorize wildcard, prefix, or regular-expression matching. Topic
names follow Kafka's 249-character limit and allowed characters; `.` and `..`
are invalid. Group IDs are nonempty strings without `*`; they do not inherit
topic-name character or length restrictions.

These entries describe workload access requirements. They do not request topic
creation, configure a broker, or identify an authentication principal. The
platform supplies connectivity, authentication, and authorization policy.

## Scope and SDK support

Release `0.1.0` covers ordinary topic read/write access and consumer groups.
It does not represent transactions, additional idempotent-producer permissions,
administration, pattern subscriptions, Kafka Streams or Share Groups, AMQP,
schema registries, broker URLs, credentials, or client tuning.

Future SDK mappings can map proven application topic/group identities to these
capabilities. They must preserve additional demands such as transactional or
idempotent privileges through an extended contract, rather than claim that a
topic-write entry fully describes those workloads. A dynamic or unresolved
resource must not be replaced by wildcard access. This release includes no
SDK mapping or profiler integration.

The [design notes](docs/design.md) explain the source references and scope.
