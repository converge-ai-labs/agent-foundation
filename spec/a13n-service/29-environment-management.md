# Environment Providers, Templates, and Runtime Environments

## Design Position

Service manages Organization- or Workspace-owned Environment Providers and versioned Environment Templates, plus Workspace-owned actual Environments. A template describes how to obtain an environment; an Environment records the logical working environment and its current backing target. Users normally start a Thread with a template selection rather than creating an Environment separately.

A Thread remembers a mutable default Environment. Each accepted Run fixes its own optional primary Environment reference independently of Agent configuration. Normal Runs can switch environments; recovery of an accepted Run cannot change its logical Environment selection. A stopped target resumes; a confirmed-deleted managed target is rebuilt from its frozen template revision. Rebuilding changes the backing generation, not the Service Environment ID, and does not restore lost files or unknown command outcomes.

The shared [Environment Provider contract](../a13n-environment/README.md) owns implementation selection, preparation, connections, operations, state codecs, stop, keepalive, and destruction. Service owns durable records, authorization, preparation timing, and retention. Harness consumes ready or transparently lazy operation objects and owns no target preparation policy.

## Boundaries

| Concern                                                                   | Owner                                               |
| ------------------------------------------------------------------------- | --------------------------------------------------- |
| Provider implementation, typed schemas, target operations and state codec | Shared Environment package                          |
| Configured Provider identity and encrypted credentials                    | Service scoped Provider resource                    |
| Reusable creation configuration and immutable versions                    | EnvironmentTemplate and EnvironmentTemplateRevision |
| Current target, generation, preparation and retention coordination        | Environment                                         |
| Default selection for later Runs                                          | Thread                                              |
| Accepted Environment and access ceiling                                   | Run                                                 |
| When to prepare, preserve, stop or delete                                 | Service                                             |
| How to create, resume, connect, retain, stop or delete                    | Provider implementation                             |
| File, shell and other Agent operation routing                             | Harness through the supplied Environment object     |

One Workspace owns an actual Environment; its Provider and frozen TemplateRevision can belong to that Workspace or its Organization. Allocation always records the consuming Workspace, independently from the template configuration owner. Threads in that Workspace may share the same Environment record. Cross-Workspace management of the same external target is unsupported; Service defines no deployment-global target resource or cross-organization target deduplication. IDs grant no authority.

## Configured Providers

An Environment Provider resource follows the [Provider naming conventions](../data-conventions.md#public-and-internal-naming). Its `id` identifies configured access to one backend; `type` selects one trusted implementation in the shared catalog. Multiple Provider resources can use the same type with different accounts or endpoints.

The conceptual resource contains `id`, `organization_id`, `workspace_id`, `type`, `name`, typed non-secret `configuration`, `enabled`, `credential_configured`, `configuration_source`, and timestamps. Ownership, type and behavior-defining configuration are immutable. A null `workspace_id` denotes Organization ownership under [Organization-owned configuration](33-identity-and-access-management.md#organization-owned-configuration). Changing account namespace, host, endpoint or other target-defining configuration creates another Provider. Name, enabled status and credentials can change without retargeting existing Environments. Provider PATCH accepts these changes together under one `If-Match` precondition and commits them atomically; omitted credentials are preserved and explicit null removes them.

Each Provider owns its encrypted credential bundle using the [resource-owned credential protection contract](27-secret-management.md#protection-boundary), as Model and Connector Providers do. Credential input is separate and write-only; ciphertext, nonce, encryption-key identity and credential generation belong to the Provider record. Reads expose safe metadata only. Replacement advances the credential generation atomically. Templates, Environments, Run state and events contain neither credential values nor Secret-resource references. Runtime uses the current authorized Provider credential; it never selects credentials based on the invoking user's personal Secret.

One shared, explicit catalog maps registered types to installed implementations. It combines the built-in Environment implementations with the Environment registrations from deployment-selected `a13n.providers` entry points; the same selected Provider package can independently register Model, Connector, or Web types through their distinct contracts. Type metadata exposes backend and credential schemas, plus `template_configuration_schemas` keyed by configuration version plus `supports_managed` and supported lifecycle actions. Templates reject Providers with `supports_managed=False`; these Providers are selected through external Environment registration. All configuration parsing is deterministic. Package installation alone grants no authority; `provider_plugins.enabled` selects trusted metadata names before startup and every role loads the same immutable catalog. Provider code changes through deployment, while configuration and state compatibility use their explicit schema versions. Environment resources contain no Python distribution lock or import target. There is no Provider package upload API, managed package revision, Service-specific attachment/retention interface, or secondary implementation catalog.

HTTP and WebSocket Envd are connect-only external Providers under the [remote Provider contract](../a13n-environment/04-remote-envd.md). WebSocket Envd is opt-in and requires the [Service connection and relay integration](29a-websocket-environments-and-live-mounts.md); selecting its catalog key alone does not supply that runtime.

### Deployment-owned local Providers

For Docker Providers, `POST /environment-providers/{provider_id}/test-image` accepts a client-generated `request_id`, `workspace_id` (null for Organization templates), and the draft Docker `configuration`. Control requires template-management authority in the requested Organization or Workspace scope, confirms that the selected enabled Provider is visible there, and validates the draft template configuration. The internal request identity binds the Provider, authorized Organization and Workspace scope, authenticated principal, and client request ID; result, validity, and cancellation use that identity. A bounded transient request reaches Worker, which uses its configured Engine and runs the native Docker compatibility test. Expired or canceled queued requests are discarded before Engine work; live accepted requests are not silently evicted. The request reports the image ID actually used, whether it was local or pulled, completed checks, and a bounded failure. The Console aborts its request and sends `POST /environment-providers/{provider_id}/test-image/{request_id}/cancel` when the draft changes or the editor closes; the cancel route requires the same authority and stops queued or running Worker execution, including cancellation that arrives before enqueue for the same identity. Control also attempts to cancel on an observed HTTP disconnect. Timeout cancels Worker execution and removes the temporary container. A separate Worker loop periodically pings the Engine without pulling an image or starting a container, including while an image test runs. Enabled status remains deployment/user configuration; observed connectivity is reported separately and does not delete the Provider record during an outage.

The default Service catalog enables E2B and HTTP Envd. Direct Local and Docker require explicit OSS deployment configuration under `environments.local_providers`; they cannot be enabled through `provider_builtins`. This setting maps each selected local type to its typed backend configuration and is rejected with custom identity composition.

Control startup publishes these backends in the singleton OSS Organization. Every authorized Workspace can use them without creating a Provider. Provider reads expose `configuration_source=user|deployment`; deployed local Providers are read-only through the management API, including for administrators. Type metadata exposes `deployment_managed` so clients omit these types from manual creation.

Startup reuses the same Provider for an identical type and normalized backend configuration. Removing or changing a backend disables its old Provider without rewriting existing template references or retargeting Environments; a different configuration gets another Provider. Restoring a prior configuration re-enables that Provider. Configuration is operator authority and must agree across Control replicas. Workers still honor the configured host placement and backend access requirements.

### Deployment-Bundled Knowledge Mount

The [configuration assistant](43-agent-configuration-assistant.md#deployment-bundled-skill) receives a deployment-owned, read-only Host content mount through the shared Direct Local Provider. This is an internal knowledge directory, not a user-selectable Environment backend. It does not publish a configured Provider, Template or Environment resource, enable local Providers for ordinary Agents, or require `environments.local_providers` setup by the user. Its fixed logical root is `/environment/builtin-skills`.

Only the protected configuration purpose can request this mount. Service resolves the fixed physical root from the current Worker deployment and checks its availability and confinement before use; clients and Agent authoring cannot supply a host path. Root confinement, read-only file action ceilings and non-writable deployment files are all required. No shell profiles, executable allowlist, inherited process environment, ports or file mutation are enabled. No User/project/Thread directories are discovered or mounted implicitly. Direct Local is not an operating-system process sandbox; this use exposes only confined file reads.

The mount's adapter identity is not a Service Environment ID and must not be written to `Run.environment_id`. It grants no access to managed Environments; ordinary `environment.use` and resource lifecycle checks remain unchanged. Configuration entry explicitly selects no managed Environment and does not inherit the business Agent's or Thread's default. Replacement Attempts construct a fresh adapter for the current deployment's fixed Skill directory. Its file contents are not part of the accepted Run snapshot and may change across deployments; no per-Run bundle reference or old-file retention is required. This exception does not change managed Skill Revision locks or artifact retention. There is no remote provisioning, user-writable materialization, or managed stop/delete lifecycle for this static content.

## Templates and Revisions

An `EnvironmentTemplate` is an Organization- or Workspace-owned resource with stable `id`, `name`, description, `version`, `current_revision_id`, timestamps and archive state. Creation publishes revision 1. Behavior changes publish a higher immutable revision; metadata-only changes do not. Selecting current resolves to an exact revision when an Environment record is allocated. A referenced revision remains retained.

The conceptual revision is:

```python
type EnvironmentAccess = Literal["read_only", "read_write", "full"]


class EnvironmentTemplateRevision:
    id: EnvironmentTemplateRevisionId
    template_id: EnvironmentTemplateId
    workspace_id: WorkspaceId | None
    version: int
    provider_id: EnvironmentProviderId
    configuration_schema_version: str
    configuration: JsonObject
    access: EnvironmentAccess
    preparation: Literal["on_run", "on_use"] = "on_run"
    retention: EnvironmentRetentionPolicy
```

A revision retains its Template's ownership. Organization Templates reference only Organization Providers; Workspace Templates may reference local or parent Providers.

`configuration` is the implementation's desired environment template configuration: image or provider template, resource limits, initialization and supported workspace settings. It contains no current sandbox/container ID, credentials, live client or process handle. Required Provider runtime collaborators come from the selected Provider configuration and deployment. Initialization is provider-validated and runs for a new backing target, not on every reconnect. Restoring an earlier template configuration creates a new revision.

The revision stores its frozen template configuration as `template_config`, including preparation timing, retention and the access ceiling. New revisions affect newly allocated Environments only. Existing Environments, including later automatic rebuilds, use their original revision. Credential rotation does not publish a template revision. Archiving a template prevents new allocation but does not revoke existing Environments.

## Live Additional Mounts

[WebSocket Environments and Live Run Mounts](29a-websocket-environments-and-live-mounts.md) owns additional associations, connection availability and application observations. The preparation, authorization, usage, retention and release rules here apply to every acquired primary or additional mount; accounting based only on `Run.environment_id` is insufficient.

## Actual Environment Records

An Environment has one stable Service identity independent of its backing container or sandbox. The conceptual record contains:

| Field                                    | Meaning                                                                                                              |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `id`, `organization_id`, `workspace_id`  | Logical Environment identity and ownership                                                                           |
| `name`                                   | Mutable human-readable label, 1–128 characters; names need not be unique                                             |
| `provider_id`                            | Immutable configured backend identity                                                                                |
| `template_revision_id`                   | Immutable template configuration for a managed Environment; absent for externally managed registration               |
| `ownership`                              | `managed` or `external`                                                                                              |
| `state`                                  | Latest protected, validated shared `EnvironmentState`, if the Provider requires state                                |
| `generation`                             | Monotonic backing-target generation; advances on known creation/replacement, not reconnect or resume                 |
| `status`                                 | `unprepared`, `running`, `stopped`, `deleted` or `unavailable`; a bounded observation, not permanent health evidence |
| `retention_condition`, `condition_since` | Current aggregate `active` or `idle` condition and its entry time                                                    |
| `expires_at`, `next_maintenance_at`      | Observed running-target expiry and earliest periodic maintenance deadline; nullable                                  |
| lifecycle coordination                   | Bounded current operation identity, lease/fence, requested action, outcome and retry observation                     |
| timestamps and safe error                | Creation/update times and bounded actionable diagnostics                                                             |

`status=deleted` means backing-target absence is confirmed, through deletion or authoritative reconciliation, not that the Service record was removed. This includes an interrupted first allocation for which reconciliation finds no target. Publication clears current target state, identity and expiry while preserving generation history. A later authorized use of a managed Environment can rebuild it through fresh target-capacity admission. Template deletion, Thread archive, Run completion and local connection close do not imply backing deletion. Removing a Service record is a separate reference-aware management operation; retained Runs and Thread defaults prevent physical record deletion.

The Provider ID must equal the referenced template revision's Provider ID. State is protected non-secret data; only current authorized runtime and private management reads can inspect it. A stateless local backend can retain no provider state while still having one Service Environment record and a fixed host/root scope. Worker scheduling must honor that scope; the same path on a different host is not the same environment.

Only managed Environments carry a creation template configuration, preparation policy and retention policy; a missing managed revision fails explicitly. External configuration is a connection configuration and carries no synthetic template or disabled retention policy.

An externally managed registration supplies a validated existing-target reference and access ceiling for the selected Provider, without a template. The Environment stores that ceiling; a managed Environment derives its ceiling from its frozen template revision. Service connects to it but does not create, rebuild, stop or delete it automatically. It cannot claim managed retention guarantees. Same-Workspace registration of an already registered target must reuse its Environment or fail conflict, using the canonical backend-scoped target identity. The uniqueness check spans Provider resource IDs: configuring the same backend twice does not create two lifecycle owners for one target. Target metadata is validated through the same Provider implementation; this is not another target resource or registry. Managed targets carry sufficient ownership evidence to reject adoption under an unrelated Environment. Explicit cross-Workspace registration of the same target is outside this contract and never grants shared lifecycle ownership.

## Thread Defaults and Run Selection

Interactions owns input validation, source-Run inheritance, Run creation and Thread defaults. Environment selection applies one rule for current or explicit template revisions, archive state, existing references, Provider availability and access ceilings. Management, Thread/Run acceptance and read-only Gateway preflight share that rule. Acceptance reloads and authorizes the selection in the same short transaction as allocation and Run creation; preflight does not grant execution authority.

A root Thread can be created without accepting a Run. Ordinary creation requires the Workspace runner authority also used for root Run start, current access to the selected Session and Agent when supplied, and the Environment/template use permission for its selected choice. Creation selects an explicit environment choice or, when absent, the selected Agent default Revision's `AgentConfig.default_environment_template_id`. It resolves that template's current revision at allocation, creates a managed Environment record and records `Thread.default_environment_id`, but performs no external provisioning. An explicit no-environment choice is preserved. If no explicit choice or Revision default exists, the Thread has no Environment. Thread creation and Environment allocation are idempotent and atomic. A combined root Run using an exact historical AgentRevision takes its omitted choice from that selected Revision, not the Agent's live default. Accepted Run and existing Thread choices retain their own semantics.

Configuration Thread creation uses [configuration-purpose authority](33-identity-and-access-management.md#configuration-assistant-authority) and the fixed knowledge-mount policy above; it cannot allocate or inherit a user Environment implicitly.

Environment choices are independent invocation fields, not `AgentConfig`, `AgentRunOverride` or `EffectiveAgentConfig` content:

```python
class ExistingEnvironmentSelection:
    environment_id: EnvironmentId


class NewEnvironmentSelection:
    template_id: EnvironmentTemplateId
    version: int | None = None


type EnvironmentSelection = ExistingEnvironmentSelection | NewEnvironmentSelection
```

Exactly one variant is accepted. A new-environment choice always allocates a new record; matching the same template never implies reuse. `version=None` selects current at allocation. The public `environment` field distinguishes omission from null:

| Entry path                                      | Omitted                           | Explicit Environment or template choice | Explicit null  |
| ----------------------------------------------- | --------------------------------- | --------------------------------------- | -------------- |
| Root Thread creation / combined root Run start  | Use Agent default template if any | Select existing or allocate new         | No environment |
| Ordinary new Run in a Thread                    | Copy Thread default               | Select existing or allocate new         | No environment |
| Fork / Continue From a historical completed Run | Copy source Run selection         | Select existing or allocate new         | No environment |

At Run acceptance, one short transaction authorizes the final choice, fixes `Run.environment_id` and its access ceiling, and sets the Thread default to that choice. No Provider I/O occurs in this transaction. Acceptance failure changes neither; later execution failure does not roll back the selected default. The explicit request or resolved selection participates in the operation's idempotency evidence. Retrying the same acceptance cannot allocate a second Environment.

Queued input stores the explicit choice or its absence. Queueing alone allocates no Environment and changes no Thread default. Omitted selection and unpinned template versions resolve only when the queued intent is consumed into an accepted Run. Agent switching does not itself change the Thread default. Existing Thread configuration, including an explicit null, never falls back to a different Agent's default template.

## Run Binding and Recovery

Service supplies at most one primary Environment as the Harness `workspace` mount. The shared Harness multi-mount API remains available to other Hosts; it does not create another Service selection surface. Trusted local mount changes cannot substitute another logical Environment for the accepted Run; backing rebuilds follow generation reconciliation.

`Run.environment_id` and `Run.environment_access` are the canonical immutable binding. They are both absent for a Run without an Environment. The access ceiling is intersected with the template/registered environment ceiling and the [Attempt IAM snapshot](33-identity-and-access-management.md#attempt-iam-snapshot). Later operation checks, periodic IAM refresh, or a fresh Attempt's authorization can deny use but cannot silently broaden the accepted ceiling. No parallel Run-to-Environment binding resource or table duplicates these fields.

A Run also records whether environment use has begun. This is an execution fact, not part of Agent configuration. `on_use` Runs can finish without setting it. Once acquired, use remains active through model work, retry backoff and Attempt replacement until the Run leaves `running`; a between-tool pause is not environment idleness. Environment state and target generations belong to the Environment record, while existing lifecycle/audit evidence records which generation each Attempt used and when a rebuild occurred. Historical Runs must not be displayed as having executed on a newly rebuilt target.

| Operation                                | Selection                                                   |
| ---------------------------------------- | ----------------------------------------------------------- |
| Normal new input / continuation          | Explicit choice or Thread default                           |
| Same-Run Worker replacement              | Same accepted Environment ID and access                     |
| Retry of failed/cancelled intent         | Copy source Run's Environment and access                    |
| Feedback / waiting continuation          | Copy the waiting Run's Environment and access               |
| Automatic async-result continuation      | Copy the selected continuation Run's Environment and access |
| Fork or explicit historical continuation | Source selection unless explicitly overridden               |

Retry and feedback cannot use a later Thread default to redirect old work. Their acceptance updates the Thread default to the preserved choice. A caller wanting different execution inputs or environment submits an ordinary new operation under its eligibility rules.

Portable Harness environment state never overrides Service's current Environment state. Switching environments preserves compatible conversation history but does not copy files, processes or dependencies. Backing-generation changes invalidate old native handles, process references, readiness evidence and environment-dependent caches. The operation object refreshes safe Environment context before later Agent work. Pending approvals tied to files or other mutable target facts require revalidation after recovery/rebuild; mismatched conditions must fail or request fresh approval rather than treating the old decision as proof of unchanged state.

### Memory Storage Access

[Service Memory](42-memory.md#filesystem-configuration-and-storage-binding) defaults to the accepted Run's Environment and borrows only its file access. It creates no second execution Environment or shell route. A memory-specific root is provider-local and remains subject to current action/path ceilings. Explicit persistent roots and explicit existing Environment references use the same preparation, credential, use-authority, routing, and generation checks; they do not replace `Run.environment_id` or enable general tools on the additional target.

Memory acquires/reuses an Environment operation scope outside SQL and releases only what it owns. Another Worker reconnects through the existing target/state routing, never a local mirror. A configuration's file capability does not prove conditional commit, crash durability, or shared-filesystem coordination; the memory backend validates those guarantees before enabling writes.

Memory references do not automatically extend sandbox retention. Stop/destroy and target reconstruction retain their existing semantics. Interfaces exposing destructive lifecycle operations identify affected memory bindings; scheduled retention follows its configured policy and does not promise memory preservation. A confirmed rebuilt target advances generation, while memory separately verifies corpus identity. The same logical Environment ID is insufficient to restore lost files. Stale memory operations are fenced, and pending organization must not recreate an old corpus silently.

## Preparation Timing

`preparation=on_run` is the default. After claim and before Harness execution, the current Attempt acquires environment use and the Provider prepares a usable target and operation object. Queued or merely accepted Runs do not start environments. `preparation=on_use` supplies a transparently lazy object; the first actual operation acquires use and performs the same preparation. File acquisition, managed Skill materialization, explicit operation readiness and shell execution all count as use. Listing configured capabilities, projecting unprepared context, entering a Harness scope, state export and local cleanup do not.

```mermaid
sequenceDiagram
    participant Control
    participant DB
    participant Worker
    participant Provider
    participant Harness
    Control->>DB: Allocate Thread/Environment; accept Run with fixed Environment ID
    Worker->>DB: Claim Attempt
    alt on_run
        Worker->>DB: Acquire Environment use and preparation authority
        Worker->>Provider: Prepare current Environment
        Provider-->>Worker: Ready operation object and known state
        Worker->>DB: Publish observed state/generation
        Worker->>Harness: Ready Environment object
    else on_use
        Worker->>Harness: Lazy Environment object
        Harness->>Provider: First actual environment operation
        Provider->>Worker: Enter supplied Host preparation coordination
        Worker->>DB: Acquire use, validate Attempt and coordinate preparation
        Provider-->>Harness: Prepare, publish through Host, then perform operation
    end
```

Harness does not build provider connections or choose preparation timing. Binding a prepared object does not connect a second time. A lazy object's implementation owns the connection and invokes the supplied Host coordination before preparing. Concurrent first operations share one preparation result. Even if a target is already running, first use acquires protection before exposing operations, so retention cannot race first access.

The preparation path inspects current target evidence:

| Evidence                                                  | Behavior                                                                                   |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Never created managed target                              | Create from frozen template revision                                                       |
| Matching running target                                   | Reuse and establish required process-local operation clients                               |
| Matching stopped target                                   | Resume that target and connect                                                             |
| Managed target authoritatively deleted                    | Rebuild from frozen template configuration; advance generation and publish changed context |
| Timeout, denial, unreachable backend or uncertain outcome | Report/reconcile; never infer absence                                                      |
| Incompatible ownership or target metadata                 | Fail conflict without adoption or mutation                                                 |
| Missing externally managed target                         | Report unavailable; no implicit creation                                                   |

## Workspace Capacity

Workers configure `A13N_SERVICE_ENVIRONMENT_MAX_TARGETS_PER_WORKSPACE` (default 1,000) and `A13N_SERVICE_ENVIRONMENT_MAX_ACTIVE_PER_WORKSPACE` (default 100), both positive integers. Every worker serving a Workspace must use the same limits. Changing a limit affects subsequent admission; lowering it does not interrupt current users or delete retained targets.

Target capacity counts prepared managed Environments, including stopped targets and unresolved allocations. Stopping an unprepared or confirmed-deleted Environment leaves it absent and consumes no slot. Unprepared logical records consume no target slot; confirmed deletion releases the slot. External registrations consume no managed target slot. Active capacity counts distinct Environments with a running Run that acquired use, including external Environments. Shared Runs consume one active slot together. Waiting, completed, cancelled and never-used lazy Runs do not occupy active slots.

First use locks the consuming Workspace, checks capacity, records the pending preparation and records Run use in the same short transaction before Provider I/O. A pending preparation reserves one managed target slot even while the observed status remains unprepared or deleted; a target and its pending preparation count only once. Admission does not change the observed target status. Recovery and additional users reuse existing slots. A known undispatched first-preparation failure with no target releases the reservation; an unknown effect keeps it until resolved by Provider evidence. Operation lease expiry permits takeover but does not release capacity. Admission at capacity fails with `environment_capacity_exceeded` and a dependency-change retry hint. It never evicts another Environment or dispatches target work. Logical allocation and Thread creation remain available independently of target admission.

## Lifecycle Coordination and Failure

One Environment coordinates all same-Workspace users. Short database transactions grant bounded operation ownership and persist stable create/stop/delete/renew identities; all Provider I/O happens after the transaction closes. State publication is conditional on the Environment fence and target generation. An Attempt must still hold current Run authority before initiating preparation or Agent effects. Retention workers need Environment operation authority, not a fabricated Run Principal. Run-related transitions follow the [canonical lock order](19-agent-control-active-execution.md#completion-and-control-races), placing the Workspace capacity lock after Thread/Run/Attempt locks and before affected Environment and inbox/queue locks. Run preparation takes the Workspace capacity lock; maintenance never acquires it. Environment-only maintenance reads aggregate usage under the Environment lock and never acquires Thread or Run locks afterward.

Creation uses provider idempotency or ownership labels and bounded discovery sufficient to recover the same dispatched creation after a crash. A timeout preserves the operation identity and unknown outcome. If the Provider cannot safely distinguish an existing result from an undispatched create, it cannot claim support for managed automatic creation. Known target identity is retained immediately, including when readiness later fails; reconciliation repairs the dispatch-to-publication gap.

A database lease fences database writes, not an already dispatched external call. Conflicting prepare, stop and delete actions must remain serialized or reconciled until the previous action's external outcome is known. New use racing an in-flight stop/delete waits for resolution and then resumes/rebuilds; it cannot operate on a target still subject to a late destructive call. Stale results cannot repoint current state. Operations use exact validated target identities and supported upstream concurrency guarantees rather than claiming generic exactly-once effects.

If a target disappears before a new operation is dispatched, managed preparation may rebuild it. If a file mutation or command was already dispatched and its outcome is unknown, rebuilding does not authorize replay. Return a bounded outcome-unknown error to the Agent and refresh environment context; the Agent decides subsequent work. A successful rebuild is not restoration of the previous filesystem or process memory. Resume does not advance generation but invalidates connections or handles that the Provider cannot preserve.

## Retention Policy

A template contains this conceptual shape. Durations on the JSON boundary are non-negative integer seconds or null; the YAML below illustrates values, not defaults:

```yaml
retention:
  idle:
    stop_after: 600
    delete_after: 604800
```

`idle.stop_after` and `idle.delete_after` are explicitly supplied, with null disabling that action. No example duration is an implicit product default. The two-field policy is fixed by the template revision. If both actions are enabled, deletion must be later than stopping. Zero permits immediate action. Unsupported stop/delete capabilities fail configuration validation; no silent conversion of stop to delete is allowed.

Retention conditions are aggregate facts about the Environment, separate from whether its target is running or stopped:

| Condition | Meaning                                                                                       |
| --------- | --------------------------------------------------------------------------------------------- |
| `active`  | At least one running Run has acquired Environment use, including recovery/backoff of that Run |
| `idle`    | No running Run has acquired Environment use                                                   |

Waiting for approval or any other external result is idle unless another Run still has active use. Selection alone, queued input, a never-used lazy Run and Thread existence do not acquire use or start a target. An existing target continues its retention schedule while a lazy Run has not used it. Approval details and historical waiting Runs are not retention inputs; target-dependent approvals still require revalidation after recovery.

Both deadlines are measured from entry into idle. Stopping does not reset `condition_since` or begin another deletion countdown. With the example above, idle stops after 10 minutes and deletes on day 7, including approval waiting. A stopped target remains subject to deletion. If scanning first observes an expired delete deadline, it deletes directly without an intermediate stop.

One periodic maintenance loop drains all due managed Environments in bounded batches of 64 by default, with concurrency 4 and no inter-batch sleep. A bounded queue feeds fixed workers across page boundaries, so one slow target does not block the next batch on otherwise available workers. Each pass fixes its due-time cutoff and pages by Environment ID, visiting an Environment at most once per pass even when an individual operation fails. External Environments and confirmed-deleted targets without pending lifecycle work are excluded. The loop polls every 5 seconds by default. Future deadlines remain eligible for later passes; elapsed time can exceed one poll interval when due work exceeds worker throughput. Operators size concurrency and Provider timeouts for the deployed workload. The loop recovers pending lifecycle operations and rechecks usage before dispatch; it does not require per-Environment timers or event-driven deadline scheduling.

For each candidate, one short transaction locks a still-due Environment, skipping locked rows, and selects and claims its lifecycle action from current usage and operation evidence. An unexpired operation retains its owner; an expired operation retains its identity on takeover. A claimed operation schedules the next check no earlier than its lease expiry. Without an action, the transaction only refreshes retention and the next deadline. There is no separate scan reservation. Planning failures back off for 30 seconds without granting ownership; database failures roll back the decision. Provider operations remain fenced, run outside the transaction, and publish their outcomes separately with bounded failure backoff.

A condition change starts that condition's clock; repeated observations or changes among users that leave the aggregate condition unchanged do not reset it. Active use cancels the current inactivity countdown. When use later ends, a new condition begins. A policy/condition change never wakes a stopped target. All state transitions recheck aggregate users and current deadlines under Environment coordination before dispatch. Run status transitions and Environment-use release are atomic; recovery and periodic reconciliation repair observations without double-acquiring or releasing use.

Stop preserves the provider-defined state needed to resume the same target. It need not preserve process memory; the Provider declares that distinction. Delete releases the backing target and advances its status to deleted while preserving the Service record, frozen template configuration and generation history. A later use resumes a stopped target or rebuilds a deleted managed target. Automatically stopping/deleting an Environment never deletes caller-owned bind sources or unrelated resources.

Keepalive maintains a running target while active use or the current pre-stop/pre-delete retention interval requires it. The same Provider implementation performs renewal where needed and reports actual supported expiry evidence, persisted as `expires_at`. The next deadline is the earliest retention action or renewal, up to 60 seconds before expiry and no more than one fifth of the remaining observed lifetime early. Short configured lifetimes therefore do not cause an immediate renewal loop. Failed or unknown renewals preserve operation evidence and use bounded retry delay. No-op renewal is valid only for a backend that does not require it. Renewal must neither start a stopped target nor override a due stop/delete. Provider limits or failed renewal are explicit availability failures, not guaranteed unlimited lifetime. Once stopped, Service still schedules deletion even if the provider itself retains stopped environments indefinitely.

## Children and Forks

Every Run acceptance selects an explicit Environment source: the supplied selection (including explicit no Environment), the Agent default, the current Thread default, or an exact retained Run. Retry and state-preserving continuations name their retained source Run and preserve its access ceiling; they do not infer that source from mutable Thread pointers or Run kind. Authorization and allocation happen in the acceptance transaction. Managed Skills in any inline descendant require the owning Run to have a writable Environment, just as root Skills do.

Child policy is `none`, `shared` or `dedicated`, defaulting to `shared`. Direct Local rejects dedicated child selection. Shared children copy the spawning Run's Environment and narrow its access ceiling; they never consult a later root Thread default. Dedicated children allocate their own Environment from the exact template revision selected by the frozen child policy. Both use fresh process-local objects and the Environment's preparation/retention contract. Inline children borrow the parent Harness facade and do not acquire an independent durable use.

A Fork normally shares its source Run's Environment, but an explicit selection may choose another or allocate from a template. Neither Fork nor dedicated creation copies the parent's changed files. File transfer or snapshot cloning is not implicit. Parent switching, completion or cancellation cannot stop/delete an Environment still used by another Run or protected by its aggregate retention condition.

## Management API and Authorization

The public resource catalog is:

| Resource                        | Routes                                                                                                         |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Provider types                  | `GET /environment-provider-types`, `GET /environment-provider-types/{type}`                                    |
| Configured Providers            | `POST/GET /workspaces/{workspace}/environment-providers`, `GET/PATCH /environment-providers/{provider_id}`     |
| Provider credential replacement | `PUT /environment-providers/{provider_id}/credential`                                                          |
| Templates                       | `POST/GET /workspaces/{workspace}/environment-templates`, `GET/PATCH /environment-templates/{template_id}`     |
| Revisions                       | `POST/GET /environment-templates/{template_id}/revisions`, `GET /environment-template-revisions/{revision_id}` |
| Actual Environments             | `POST/GET /workspaces/{workspace}/environments`, `GET/PATCH /environments/{environment_id}`                    |
| Explicit lifecycle commands     | `POST /environments/{environment_id}/stop`, `POST /environments/{environment_id}/delete`                       |

Environment creation accepts a template choice or an explicitly externally managed registration. Both accept an optional name; omission generates a readable name containing an Environment ID suffix. Automatic Run/Thread allocations use the same naming rule. `PATCH /environments/{environment_id}` changes only the name under `environment.manage` and an exact `If-Match` precondition. Renaming preserves the target, generation, frozen template configuration and existing Run references. It creates the record without mandatory immediate target preparation, matching Thread allocation. Stop/delete target commands use the [durable operation contract](06-durable-operations-and-outbox.md), reauthorize at dispatch and reject active use; manual commands explicitly override inactivity grace but do not affect unrelated target ownership. Delete here removes the backing target, not retained Environment history. There is no standalone connection test or template trial API. Saving configuration performs deterministic validation; actual preparation verifies runtime availability and reports bounded failures.

Provider and Template collections also expose `POST/GET /organizations/{organization}/environment-providers` and `POST/GET /organizations/{organization}/environment-templates`. Organization collections contain only Organization-owned configuration; Workspace collections include parent configuration. Detail and revision routes retain their exact resource IDs and authorize reads through the consuming scope and mutations through the owning scope. Actual Environment routes remain Workspace-only.

[Thread and Run control](18-agent-control-input-and-continuation.md) owns invocation routes. `POST /workspaces/{workspace}/threads` creates an empty root Thread and optional Session; the combined root Run endpoint remains a convenience invoking the same allocation rules. Thread and Run reads expose safe Environment identity and availability/generation observations, not target credentials, private state or endpoint details.

Provider reads require `environment_provider.read`; configuration, credentials and enabled-state mutations require `environment_provider.manage`. Template reads, authoring and selection require `environment_template.read`, `environment_template.manage` and `environment_template.use`. Actual Environment reads, external registration/manual lifecycle and selection require `environment.read`, `environment.manage` and `environment.use`. An authorized template use can automatically allocate its Environment during Thread/Run creation without granting arbitrary Provider administration or external-target registration. Attempt preparation validates the accepted primary Environment selection and its Organization, Workspace, and Provider ownership once and retains that fixed binding in memory. Additional mounts repeat authorization at their model-boundary admission under the live-mount contract and publish fresh operation proxies. Ordinary runtime operations do not reload the Run or Environment binding.

Attempt preparation captures `environment.use`, the required Agent invocation authority, Workspace existence, and selected Environment Provider enablement in process-local authorization state, including for `preparation=on_use`. Provider enablement and Workspace existence refresh together with the [Attempt IAM snapshot](33-identity-and-access-management.md#attempt-iam-snapshot), after every ten Agent loops and before the next loop. A disabled or missing Provider denies subsequent Environment operations after refresh; a deleted Workspace or failed refresh invalidates Attempt authorization. A replacement Attempt reads fresh state. This cadence does not guarantee wall-clock propagation during a long model request or tool operation.

Each runtime operation checks its requested action, path and mount against the latest published authorization state, the accepted access ceiling, and the current adapter permissions and generation. It also checks the Attempt's locally confirmed lease deadline and invalidation state immediately before dispatch, including after readiness waits. These checks perform no PostgreSQL reads: this applies to shell execution, process polling, file operations, readiness prechecks, and managed Skill materialization. An earlier allow for one command or path does not authorize another. Background lease renewal and control reconciliation retain their independent PostgreSQL checks and invalidate local authority when it is lost. First preparation, target recovery, lifecycle coordination, and durable state mutations retain their owning database checks and fences.

Disabling a Provider denies new Attempt preparation and is observed by active Attempts at their next authorization refresh, without deleting records. Already selected retention cleanup remains separately authorized system maintenance and uses protected current credentials; it cannot execute Agent operations or bypass explicit credential revocation. Missing credentials or ownership evidence reports a lifecycle failure. Private authorized management reads may expose protected target references; safe lists, Agent context, events and traces omit them. Lifecycle evidence records Environment ID, generation, operation identity and safe outcome, never credentials or raw native errors.

## Relational Ownership and Invariants

The domain owns `environment_providers`, `environment_templates`, `environment_template_revisions` and `environments`. Provider credentials are columns of their owning Provider. Thread defaults and Run bindings are fields of their owning records; Environment coordination is bounded state on the Environment record. Existing durable operation and lifecycle/audit infrastructure carries command and generation-change evidence.

01. A template revision is a template configuration, not a running target.
02. Each Environment has one immutable Workspace, Provider and managed template configuration.
03. Creating a Thread does not provision a target; template selection allocates its Environment automatically.
04. Each Run freezes its primary Environment independently of Agent configuration and mutable Thread defaults.
05. Preparation defaults to on_run; on_use performs no target I/O before actual use.
06. Provider implementations own connections; Harness consumes operation objects.
07. Confirmed target loss permits managed rebuild, while uncertainty never proves absence.
08. Backing generation and operation evidence preserve historical truth across rebuilds.
09. One idle retention policy governs stop and delete, with both deadlines measured from entry into idle. Approval waiting counts as idle unless another Run has active use.
10. Shared users coordinate through one Workspace Environment; aggregate usage includes all active Runs across its Threads. No cross-Workspace target authority exists.
11. Credentials remain encrypted on Providers and are resolved freshly under current authority.
12. Local close is non-destructive; unknown Agent effects are never silently replayed.

## Lifecycle Publication and Recovery

`on_run`, first `on_use`, and in-Run connection recovery acquire the same fenced preparation operation. A ready connection serves normal readiness checks; target discovery and bootstrap run only when preparation or recovery is necessary. An accepted Run keeps its logical Environment and access selection across Attempt replacement, waiting resumption and retries.

Publication commits known state, canonical target identity, backing generation, bounded status, command outcome and lease release in one short transaction. Retrying a failed publication preserves completed reconciliation observations, including authoritative absence. The Run adapter exposes operations only after that commit. Only a different canonical backing target increments `generation`; refreshed credentials, connections and daemon sessions do not. Harness receives a stable backing identity derived from logical Environment ID and backing generation for recovery observation and Host policy. Standard Harness deferred approval does not automatically bind to it.

Lifecycle execution captures the operation's outcome and known target evidence before publication. A storage failure does not change a successful stop, delete, preparation or renewal into a failed Provider operation. Bounded publication retries save the same result without repeating Provider I/O; publication evidence committed with the state identifies an already completed write when its acknowledgement was lost, without duplicating generation changes, command completion or audit events. If publication remains unavailable, the pending operation remains recoverable. Known partial results, including a created target whose readiness failed or an insufficient but confirmed renewal deadline, remain part of the captured evidence. Local cleanup failure does not revise a committed target outcome.

A lifecycle command is `pending`, `completed` or `failed`. Known failure closes its receipt and releases the operation lease; unknown external effect retains the original operation identity for reconciliation. A maintenance worker reconciles an expired preparation by observation, without requiring a live originating Run and without starting a target. Once observation resolves the operation, normal retention can reclaim an unused target.

Construction or authorization failure before a new operation is dispatched is a known failure. For a previously claimed operation whose outcome remains unknown, an undispatched retry does not resolve the earlier effect: it retains the original operation identity until Provider evidence resolves that uncertainty.

The retention clock starts when the aggregate usage condition changes, in the transaction publishing that Run transition. The complete Run status change precedes aggregate usage evaluation; Environment coordination precedes inbox and queue locks. Completion, waiting, cancellation and terminal failure release use at this boundary, including budget exhaustion and successor handoff. Accepting a later Run does not repair or restart its predecessor's retention clock. It does not reuse the oldest waiting Run's seal time. Repeated readiness or maintenance observations do not restart an unchanged condition's clock.

`deployment.mode` is `single_host` or `distributed`, defaulting to `single_host`. Direct Local and Docker are available only in `single_host`; remote Providers work in either mode. Local Envd is not a Service backend. Single-host Workers share the configured filesystem and Docker backend. Startup rejects incompatible configuration rather than adding Provider-specific host predicates to Run scheduling or maintenance. Docker freezes `docker_host` as its backend endpoint; changing process environment does not retarget existing Providers.

Managed Direct Local templates specify a base in `root.path`. Service derives `<base>/environments/<env_id>/` for the concrete adapter, creates it on first preparation, and removes only that owned directory on destruction. Manual allocation needs no Session. External registrations and Harness UI roots remain exact caller-owned paths. Direct Local stop is a logical no-op preserving data; directory destruction belongs to Service and must fail if safe removal cannot be established. Different Environments have different targets; sharing reuses one Environment.

EnvironmentTemplate and Environment labels are mutable head metadata outside TemplateRevision and Environment generation. New managed Environments copy the Template's current head labels inside allocation even when selection names a historical TemplateRevision, then apply explicit overrides. The new-Environment selection accepts `labels` overrides in explicit creation and Thread/Run allocation alike; selecting an existing Environment accepts no labels mutation. External Environment registration without a Template uses only explicit labels. Backing replacement and existing-Environment selection retain labels.

Service input files and managed Skills are materialized beneath the Environment descriptor’s default working directory. For native Docker this is `/workspace` inside the container; explicit external mount targets remain independently accessible. Harness aggregate paths still include the selected mount prefix; relative file and shell paths use the declared working directory.
