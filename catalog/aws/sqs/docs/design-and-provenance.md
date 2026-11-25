# SQS design and provenance

## Authoritative source

The API authority is AWS's public Smithy JSON model, not an SDK-specific API file.

| Source coordinate | Value |
| --- | --- |
| Repository | https://github.com/aws/api-models-aws |
| Commit | `87480db7ee4bec827c971dbf0294446591d1b229` |
| Model commit date | 2026-06-02 |
| Model path | `models/sqs/service/2012-11-05/sqs-2012-11-05.json` |
| Smithy service shape | `com.amazonaws.sqs#AmazonSQS` |
| Smithy version | `2.0` |
| SQS API version | `2012-11-05` |
| Source bytes SHA-256 | `f34a7d2560fb9776206fb35958b8d0fc5f17d33f377bb7512d55a34a8c0c78f0` |
| Inspection date | 2026-11-23 |

The [pinned model](https://github.com/aws/api-models-aws/blob/87480db7ee4bec827c971dbf0294446591d1b229/models/sqs/service/2012-11-05/sqs-2012-11-05.json)
contains 23 service operations. Its service operation references, request member
requiredness, and documentation establish the SQS API semantics used here.
The source hash identifies the downloaded JSON bytes, not extension semantics.
The model is referenced rather than copied into this extension.

AWS's [API permission reference](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-api-permissions-reference.html)
and [redrive permission guidance](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-configure-dead-letter-queue-redrive.html)
inform platform-supplied authorization mapping. They do not define an additional
IAM action selection in this extension. These are living documents consulted on
the inspection date, not immutable snapshots.

AWS's [service authorization reference](https://docs.aws.amazon.com/service-authorization/latest/reference/list_sqs.html)
identifies operation-to-action relationships and resource scope. The
[SQS policy guide](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-using-identity-based-policies.html)
distinguishes queue resource policies from regional ListQueues authorization.
The [queue tag restrictions](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/quotas-queues.html)
supply the key/value length limits and reserved-prefix rules used below.

## Fields and adapter decisions

| Vocabulary | Adapter decision | Why a coarser requirement is insufficient |
| --- | --- | --- |
| `aws.sqs` | Select an implementation supporting Amazon SQS semantics | Generic messaging does not establish SQS compatibility |
| `interface.type` | Distinguish queue access, queue creation/discovery, and redrive task access | These domains have different resource context and authorization requirements |
| `operations[].name` | Verify API support and derive authorization on resolved resources | Broad read/write/admin access loses service operation intent |
| `queueType` | Select or verify a Standard or FIFO queue | Their ordering and deduplication semantics differ and the type cannot be changed in place |

The Smithy service lists operations without these Runtime Conditions interface
groups. The three groups are extension-owner decisions based on the API domains
and resource context documented by AWS:

- `queue` covers the 18 ordinary queue operations, including message operations,
  queue attributes, policy changes, tags, and dead-letter source discovery.
  `GetQueueUrl` belongs here because it resolves a particular existing queue.
- `service` covers `CreateQueue` and `ListQueues`. Creation permission does not
  itself request a queue provisioned before the workload runs. The adapter still
  scopes creation authorization to permitted queue resources.
- `message_move_task` covers `StartMessageMoveTask`, `ListMessageMoveTasks`, and
  `CancelMessageMoveTask`. Its resource context is the source dead-letter queue;
  the extension does not ask an adapter to provision a task or serialize its handle.

The extension retains all 23 canonical Smithy operations: 18 queue operations,
two service operations, and three task operations. Operation objects contain only
`name`. The three batch variants remain distinct from their individual-message
counterparts even where AWS uses a shared permission action. Adapters can check
that a selected service implementation supports the required API surface; an
IAM permission alone does not establish individual-versus-batch API compatibility.
Future SDK mappings can reference the canonical service names directly.

There is one operation vocabulary and no `permissions` field or IAM action enum.
Platform-supplied mapping owns the translation into applicable actions, resource
scope, conditions, and dependent permissions. The
[machine-readable AWS authorization reference](https://servicereference.us-east-1.amazonaws.com/v1/sqs/sqs.json)
can supply mapping evidence alongside the Smithy API authority. No such mapping
is generated or packaged by this extension.

`queueType` comes from the model's `FifoQueue` attribute and its documented
Standard/FIFO semantics. It is optional: an ordinary SQS call does not prove the
required delivery type. `MessageGroupId` alone must not imply FIFO, because AWS
also supports it for fair queues on Standard queues. No default is applied when
the field is absent, even though the CreateQueue API defaults to Standard.

## Configuration and permission fields

All configuration fields are optional requirements with no implicit defaults.
Queue Conditions may omit `operations` when they describe configuration alone.
This permits a dead-letter queue requirement without inventing workload access.
Configuration does not imply that the workload itself calls SetQueueAttributes.

Supplied scalar configuration values are exact requirements. This includes
numeric queue defaults, `maximumMessageSizeBytes`, `deadLetterQueue.maxReceiveCount`,
and `encryption.dataKeyReusePeriodSeconds`, as well as booleans and enumerations.
For example, seven-day retention does not satisfy an explicit four-day setting:
longer retention may violate deletion policy or budget constraints. A configured
message-size limit is an exact limit, not a minimum supported message capacity.
Omission leaves the setting to platform policy; it never imports an AWS default.

`maxMessagesPerSecond` is the exception: it is a ceiling on redrive execution,
and a lower positive rate satisfies it. These comparisons are extension-owner
semantics for fulfillment. Local JSON Schema constrains declared values; the
adapter compares requirements with selected resources and governed execution.

The [CreateQueue](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/APIReference/API_CreateQueue.html)
and [SetQueueAttributes](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/APIReference/API_SetQueueAttributes.html)
documentation establish these queue configuration sources and adapter decisions:

| Fields | AWS source | Adapter decision |
| --- | --- | --- |
| `visibilityTimeoutSeconds` | `VisibilityTimeout` | Set the queue's redelivery visibility default |
| `messageRetentionPeriodSeconds` | `MessageRetentionPeriod` | Retain messages for the required period |
| `delaySeconds` | `DelaySeconds` | Set the initial delivery-delay default |
| `maximumMessageSizeBytes` | `MaximumMessageSize` | Set the accepted message-size limit |
| `receiveMessageWaitTimeSeconds` | `ReceiveMessageWaitTimeSeconds` | Set the queue's long-poll default |
| `contentBasedDeduplication` | `ContentBasedDeduplication` | Configure FIFO body-based deduplication |
| `deduplicationScope` | `DeduplicationScope` | Select queue-wide or per-group deduplication |
| `fifoThroughputLimit` | `FifoThroughputLimit` | Select queue-wide or per-group throughput quotas |
| `encryption` | `SqsManagedSseEnabled`, `KmsMasterKeyId`, `KmsDataKeyReusePeriodSeconds` | Select encryption mode, key binding, and key reuse |
| `deadLetterQueue` | `RedrivePolicy` | Configure a target queue and receive threshold |
| `redriveAllowPolicy` | `RedriveAllowPolicy` | Control which source queues may select this dead-letter queue |
| `accessPolicy` | `Policy` | Configure queue resource-policy grants and denials |
| `tags` | CreateQueue `tags` and TagQueue `Tags` | Apply workload-required governance labels |

These persistent settings change fulfillment even when static SDK analysis cannot
infer them. Application declarations can provide requirements that calls do not
prove. Per-message overrides and payload data remain outside this vocabulary.

FIFO-only fields require `queueType: fifo`. Per-message-group throughput also
requires `deduplicationScope: messageGroup`. Numeric limits follow the cited API
documentation. Encryption modes are extension-owned values: `none` disables
server-side encryption, `sqs_managed` selects SQS-owned keys, and `kms` requires
KMS encryption. KMS key and reuse settings are valid only for `kms`; absent
`keyRef`, the adapter selects a compatible key rather than assuming a literal ID.

## Platform decisions and composition

Platform engineers select resources and apply security, compliance, business
process, and budget policy. They may choose unspecified values and add compatible
controls. An explicit requirement conflicting with platform policy must be
reported as unfulfilled; the adapter must not silently substitute another value
and claim success. This includes selecting encryption when no mode is declared,
or rejecting `mode: none` when encryption is mandatory.

`tags` is a required subset of resource tags. Platforms may add ownership, cost,
or compliance labels. An existing required key must have the declared value;
conflicting platform values require a reported conflict, not silent replacement.
Tag requirements must not remove unrelated tags.

`accessPolicy.statements` adds required authorization constraints rather than
authorizing wholesale replacement of a queue's policy. Preserve applicable
platform controls when composing statements. Evaluate effective Allow/Deny
behavior: adding an Allow statement is insufficient when a platform Deny blocks
the access required by the Condition. Report that conflict rather than removing
the Deny or claiming the workload permission is fulfilled.

Tag keys have one to 128 Unicode characters; values have zero to 256. Neither
may begin with the reserved `aws:` prefix. Local schemas enforce these limits;
adapters also check AWS's documented character restrictions. The recommendation
against more than 50 tags is not encoded as a hard extension limit. Organization
tag requirements and applicable platform policies remain platform decisions.

## Logical references and resource policies

`deadLetterQueue.queueRef`, `redriveAllowPolicy.sourceQueueRefs[]`, and task
`sourceQueueRef` / `destinationQueueRef` each name an `aws.sqs` / `queue` Condition
in the same profile. Referenced Conditions must have a core `name`; the adapter
must reject missing or wrongly typed references and incompatible queue types.
It must also reject self-referential or cyclic dead-letter routing. These checks
need profile context and are not claimed by a Condition's local JSON Schema.

`encryption.keyRef`, `accessPolicy.statements[].principalRefs[]`, and
`accessPolicy.statements[].conditionRefs[]` are names in adapter-supplied binding
maps, not Condition references or concrete environment values. Key bindings
resolve KMS keys; principal bindings resolve AWS principals; condition bindings
resolve named IAM policy constraints, such as a requirement for TLS transport.
Binding names are exact and case-sensitive. Missing bindings prevent fulfillment.

Platform engineers own these bindings and may implement organization-specific
security, compliance, or workflow rules. Binding names are not standardized
security guarantees. The example's `approved-producer-context` must resolve to
platform-defined conditions; no particular condition follows from its spelling.
An IAM condition on an Allow statement restricts that statement, not every other
grant. A global restriction needs suitable Deny statements or platform controls.

Each access-policy statement applies to the current queue and requires `effect`,
`principalRefs`, and `operations`. Policy operation objects reuse the canonical
queue and task names plus CreateQueue: 22 operations, excluding regional
ListQueues and wildcards from queue policy scope. The schema reuses the same
canonical name sets; no independent IAM vocabulary is maintained. A policy
selector constrains its principals but does not declare workload invocation.
All named condition constraints apply together. The adapter
must preserve IAM Allow/Deny semantics, respect SQS cross-account and resource-policy
restrictions, and reconcile the statements with platform policy. This logical
representation is an extension-owner decision, not a raw IAM document schema.
Platform mapping must evaluate Allow and Deny separately. Expanding all
authorization dependencies into Deny statements can unintentionally block other
required operations. For example, denying task creation must not automatically
deny ordinary message receipt just because starting redrive needs receive rights.

Some operation selections cannot be distinguished by IAM alone. For the same
principal and applicable conditions, denying SendMessage may also block a required
SendMessageBatch because AWS uses a shared authorization action. The platform
must provide suitable additional controls or report the requirements unfulfilled;
it must not weaken the Deny or silently claim independent IAM enforcement.
This mapping conflict is different from offering two selectable vocabularies:
every selector here names a canonical SQS operation.
Setting `redriveAllowPolicy.mode: byQueue` requires one to ten source queue
references; other modes forbid that list. No AWS defaults are imported.

## Why service has fewer fields

`service` represents the required creation or discovery operations. The selected
Smithy model exposes no independently configurable SQS service resource. The
smaller field set is a scope decision rather than an incomplete operation list.

| Service operation | Inputs omitted from service fields | Reason |
| --- | --- | --- |
| `CreateQueue` | `QueueName` | Concrete resource naming remains an adapter binding |
| `CreateQueue` | `Attributes`, `tags` | These configure the created queue; queue Conditions own that vocabulary |
| `ListQueues` | `QueueNamePrefix` | A result filter does not impose a service configuration or permission boundary |
| `ListQueues` | `MaxResults`, `NextToken` | Pagination controls request execution rather than fulfillment |

See the authoritative [ListQueues inputs](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/APIReference/API_ListQueues.html).
Region, endpoint, credentials, and retry behavior are SDK client or environment
configuration, not service-wide SQS attributes. This extension defines no fields
for those concerns. A service operation does not assert the values a workload
will pass when it creates queues; queue configuration fields express separate
fulfillment requirements rather than constraints on every future CreateQueue call.

## Redrive authorization

A `message_move_task` Condition describes access to one source dead-letter queue.
With `sourceQueueRef`, that source is the referenced queue Condition. Without it,
the platform binds the task Condition itself to one source dead-letter queue
under its normal resource-selection policy; omission does not imply account-wide
access. All task operations in that Condition apply to that same bound source.
Platform mapping derives the complete authorization for each declared operation,
using source and destination references and any proven request-specific facts.
StartMessageMoveTask requires authorization on both queues. Its dependent rights
do not become additional workload operation entries: the service moving messages
does not prove that application code independently calls SendMessage, ReceiveMessage,
DeleteMessage, or GetQueueAttributes.

The referenced destination can therefore be a configuration-only queue Condition,
as shown in the example. A future SDK mapping emits SendMessage there only when
application evidence actually proves that separate operation requirement.

The [StartMessageMoveTask inputs](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/APIReference/API_StartMessageMoveTask.html)
supply the semantics for `sourceQueueRef`, `destinationQueueRef`, and
`maxMessagesPerSecond`. Queue references retain relationships without embedding
ARNs. A rate ceiling must be between 1 and 500 messages per second; destination
and rate fields require `{name: StartMessageMoveTask}` in `operations`. They constrain task execution,
not a queue's throughput quota. Source references also support list/cancel access.
Platforms may fulfill the rate requirement through supplied configuration,
governed workflows, or other controls suited to their execution environment.
Governance-only consumers may assess and report compliance without provisioning
a task. A consumer unable to establish compliance must not report fulfillment;
declaring the ceiling alone does not alter a workload's future API requests.

The model requires `SourceArn` for start/list and accepts an optional
`DestinationArn` for start. An API request omitting DestinationArn sends messages
back to their respective original source queues. Omitting the profile's
`destinationQueueRef` does not require that API default: destination selection
remains with the platform and governed execution. An adapter must resolve and authorize
those destinations before claiming fulfillment; it must not invent one queue or
grant wildcard access as a fallback. Source and destination queue types must match.

Cancellation accepts a `TaskHandle`, not a queue identifier. Future analysis must
prove its source queue through task provenance or an explicit declaration before
emitting a resource-specific Condition. Handles are runtime values, not fields.
Encrypted redrive can also need KMS permissions; this extension does not define
KMS operations or permission vocabulary. An encryption key binding does not grant
KMS access, and these Conditions alone do not establish KMS authorization.

## Boundaries and maintenance

Only configuration fields and canonical operation requirements are introduced. Message bodies,
message attributes, group IDs, receipt handles, task IDs, pagination, SDK retry
settings, per-call polling and visibility overrides, SDK symbols, and protocol
shapes are not profile fields. Queue-level polling and visibility defaults are
configuration fields. Queue names, URLs, ARNs, accounts, Regions, endpoints,
credentials, and environment-variable conventions are not embedded as target values.

The extension is manually authored from the sources above. No generated service
mapping, semantic bridge, SDK mapping, binding, or generator is part of this work.
Future semantic changes should receive a new versioned extension definition after
reviewing upstream model and permission changes. No validation or end-to-end
checks were run for this candidate.
