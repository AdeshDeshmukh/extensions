# Kafka service contract

The `kafka.service` contract describes topic read/write access and consumer-group
access through the Kafka client protocol.

## Approaches

Adding Kafka to common integrations would couple protocol-specific vocabulary
to the shared extension. A combined Kafka/AMQP extension would also couple two
different protocols' releases and SDK mappings. A separate `kafka.service`
contract follows the NATS service precedent while keeping Kafka's own identities
and capabilities independently versioned. This is the selected approach.

An end-to-end compiler and SDK mappings would be a larger contribution. The
first release instead authors a small contract grounded in Kafka sources.
Future generation should project the upstream protocol schemas directly and
add a semantic supplement only when a specific required fact is absent.
An inventory or a copy of NATS's bridge is not required by this design.

## Adapter decisions

| Field or distinction | Decision enabled | Why it is retained |
| --- | --- | --- |
| `kafka.service` / `service` | Select a service speaking the Kafka client protocol. | A generic message broker need not implement Kafka. |
| `operations[].resource` | Select topic or consumer-group authorization. | The same identifier on the two resource types has different meaning. |
| `operations[].action` | Distinguish reading from writing. | A producer need not receive topic read access. |
| `operations[].topic` | Scope access to an exact topic. | Service-wide access would broaden the requirement. |
| `operations[].groupId` | Scope group coordination and offset access. | Reading a topic does not prove use of a group. |

Fixed schema branches accept only topic/read, topic/write and group/read.
Operation entries are closed to catch misspelled identities and unsupported
options. The outer Condition and interface remain open for composition.
Topic names use Kafka's own validation rules. The literal-only restriction
on group IDs is an extension scope decision, not a claim that Kafka generally
forbids every name containing `*`.

## Authoritative references

- [Kafka 4.1 authorization table](https://kafka.apache.org/41/security/authorization-and-acls/)
  relates Produce to topic Write, consumer Fetch to topic Read, and group
  coordination/offset operations to group Read. Read and Write imply Describe
  in Kafka's standard ACL model. Transactional and idempotent production can
  introduce additional resources or privileges and are outside this release.
- [Produce request](https://github.com/apache/kafka/blob/4.1.0/clients/src/main/resources/common/message/ProduceRequest.json)
  and [Fetch request](https://github.com/apache/kafka/blob/4.1.0/clients/src/main/resources/common/message/FetchRequest.json)
  supply protocol operation and topic identity facts.
- [JoinGroup request](https://github.com/apache/kafka/blob/4.1.0/clients/src/main/resources/common/message/JoinGroupRequest.json)
  supplies the consumer-group identity. Future mappings must also handle the
  protocol version and group API used by their SDK.
- [Kafka topic validation](https://github.com/apache/kafka/blob/4.1.0/clients/src/main/java/org/apache/kafka/common/internals/Topic.java)
  defines the 249-character limit, ASCII character set, and reserved `.`/`..`
  names used by the schema.

These references explain the first release's semantics; they do not declare
complete Kafka 4.1 API coverage or restrict fulfillment to that server version.
Newer protocol messages can contain topic IDs instead of names. A future SDK
mapping must establish the literal topic identity from source-proven state;
it must not invent a name from an unresolved UUID.

## Release and verification

The definition uses the currently implemented `metadata.id` catalog format
and an immutable `0.1.0` release path. Migrating the catalog to `metadata.uri`
is outside this change. The package catalog registers the four existing
language emitters. Generated packages remain owned by the merge workflow.

Tests cover the fixed operation forms, literal identities, topic boundaries,
duplicates, composition and all three examples. Generated bindings merge the
operation forms into one type with optional `topic` and `groupId` fields; the
schema rejects invalid combinations when the profile is validated. Compiling
bindings and validating profiles do not prove live broker access or SDK inference.
