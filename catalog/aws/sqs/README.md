# AWS SQS extension candidate

This standalone extension describes Amazon Simple Queue Service configuration and
canonical operation requirements. It is a repository candidate, not an AWS-published
extension. Its versioned identifier follows the catalog convention and does not
claim that the artifact has been published at that URL.

## Core files

- [Extension definition](releases/0.2.0/runtimeconditions.extension.yaml): vocabulary,
  descriptions, canonical operation values, and scoped JSON Schemas.
- [Design and provenance](docs/design-and-provenance.md): authoritative source,
  interface boundaries, permission translation, and exclusions.
- [Example profile](examples/queue-profile.yaml): queue access, service access,
  and dead-letter queue message redrive requirements.

## Condition shape

```yaml
kind: aws.sqs
interface:
  type: queue
  queueType: fifo
  visibilityTimeoutSeconds: 60
  receiveMessageWaitTimeSeconds: 20
  operations:
    - name: SendMessageBatch
    - name: ReceiveMessage
    - name: DeleteMessageBatch
```

Configuration and queue operations are optional. Omitted fields impose no
requirement and do not import AWS API defaults. `operations[].name` identifies
required canonical service operations; there is no IAM permission selection.
`accessPolicy.statements[].operations` uses the same canonical operation names
to select authorization constraints for resolved principals.

Queue fields cover delivery delay, visibility, retention, message size, long
polling, FIFO deduplication and throughput, encryption, dead-letter routing,
redrive admission, access policy, and tags. Task fields identify logical source
and destination queues and constrain the maximum redrive rate.

Queue references name `aws.sqs` / `queue` Conditions in the same profile.
Principal, KMS key, and policy-condition bindings are logical names resolved by
the adapter. No concrete queue ARN, principal identity, or key ID is embedded.

| Interface type | Requirement |
| --- | --- |
| `queue` | Access to one logical queue, including messaging, attributes, policies, and tags |
| `service` | Permission to create queues or list queues within an account and Region |
| `message_move_task` | Permission to manage redrive tasks for one source dead-letter queue |

Canonical operations express workload needs. Platform-supplied mapping translates
them into resource-scoped authorization and any required dependent permissions.
For redrive, StartMessageMoveTask and the destination reference determine
destination authorization without claiming a separate SendMessage call.
Configuration requirements describe desired fulfillment; they do not grant the
workload permission to change those settings.

Supplied scalar settings require exact values. The task field
`maxMessagesPerSecond` instead sets an upper bound, allowing lower positive rates.
Omitted settings remain platform choices. Policy-condition references retain
their platform-defined meanings; unresolved bindings prevent fulfillment.

`tags` and `accessPolicy.statements` express requirements to compose with platform
controls. They do not replace all existing labels or policy statements. Platforms
may add tags and controls, but conflicts must be reported without silently
changing explicit requirements. Policy selectors reuse the operation vocabulary;
Allow/Deny mapping must account for shared permissions and report conflicts.

The `service` interface declares creation and listing operations. CreateQueue
attributes and tags describe the queue being created, not service-wide settings.
Literal queue names, listing filters, pagination, and SDK client configuration
remain outside that interface; the design document lists the excluded inputs.

AWS's [SQS Smithy model](https://github.com/aws/api-models-aws/blob/87480db7ee4bec827c971dbf0294446591d1b229/models/sqs/service/2012-11-05/sqs-2012-11-05.json)
is the operation authority. AWS's [authorization reference](https://docs.aws.amazon.com/service-authorization/latest/reference/list_sqs.html)
can inform platform-owned operation-to-permission mapping; IAM action names are
not extension vocabulary.

This folder contains no SDK mappings, language bindings, generators, maintenance
automation, or integration with another extension. The definition and example
were authored without running validation or end-to-end checks.
