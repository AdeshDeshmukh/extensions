# RabbitMQ service extension

This extension describes a workload that publishes to RabbitMQ exchanges or
consumes from RabbitMQ queues through AMQP 0-9-1. Each Condition uses
`kind: rabbitmq.service` and `interface.type: amqp_0_9_1`.

`interface.virtualHost` identifies the virtual host containing every resource
in the Condition. It is required; `/` explicitly names the default virtual host.
The Condition's `name` identifies the logical service requirement. Workloads
accessing several virtual hosts declare a separate Condition for each one.

## Capabilities

| Resource | Action | Required fields | Requirement |
| --- | --- | --- | --- |
| `exchange` | `publish` | `exchange`, `routingKey` | Publish messages to the exchange using this routing key. |
| `queue` | `consume` | `queue` | Receive messages from the named queue. |

The operations array is nonempty and contains no duplicate entries. A workload
that publishes and consumes declares both operations. Exchange and queue access
remain independent, even when the resource names are identical.

The default exchange is represented by `exchange: ""`. RabbitMQ automatically
routes from it to an existing queue whose name equals the routing key. Its
permission-management alias, `amq.default`, is not the publishing target name.
An empty routing key is also a valid explicit publishing value. Missing
`exchange` and `routingKey` fields are rejected; an empty queue name is rejected
because consumption requires an identified queue.

See the [publisher](examples/publisher-profile.yaml),
[consumer](examples/consumer-profile.yaml), and
[default-exchange publisher](examples/default-exchange-publisher-profile.yaml)
profiles.

## Fulfillment

Publishing corresponds to RabbitMQ's write permission on an exchange, while
consumption corresponds to read permission on a queue. The routing key lets an
adapter account for topic-exchange authorization where it is enabled. Required
exchanges, queues and routing must already be available when these operations
run. These capabilities describe access, rather than requesting declarations
or queue bindings.

Resource names and routing keys are literal strings. Adapters translating names
into permission expressions must escape regular-expression characters and scope
permissions to the declared virtual host. RabbitMQ permissions can authorize
several protocol operations; an adapter must apply the broker's actual permission
model when fulfilling each requirement.

The schema rejects NUL characters and bounds these strings to 255 characters.
AMQP short strings are limited to 255 UTF-8 bytes, so adapters or clients must
also check the encoded length before sending multibyte names and routing keys.

Generated bindings represent the operation alternatives through the existing
emitters' shared types. Validate profiles against the extension schema to reject
invalid action/resource combinations and missing identities.

The contract follows RabbitMQ's [authorization table](https://www.rabbitmq.com/docs/access-control),
[virtual-host model](https://www.rabbitmq.com/docs/vhosts), and
[exchange semantics](https://www.rabbitmq.com/docs/exchanges). Short-string
encoding is defined in section 4.2.5.3 of the
[AMQP 0-9-1 specification](https://www.rabbitmq.com/resources/specs/amqp0-9-1.pdf).
