# Background tasks and retention

Background work is owned by process roles, not by HTTP request traffic. The `all` role installs each role's contributions once. Run the appropriate role when relying on its reconciliation; a Worker alone does not perform control maintenance.

## Who runs what?

| Role                   | Work                                                                                                                                                                                      |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `control` / `all`      | Hook dispatch and delivery, Run recovery/collection, hosted-child result and successor reconciliation, Asset cleanup, Hook history/retention, Connector setup and OAuth-state maintenance |
| `worker` / `all`       | Run execution, Environment lifecycle maintenance, lifecycle projection, optional model-price updates                                                                                      |
| `connectivity` / `all` | Provider event admission, bounded pending input, deduplication and retry                                                                                                                  |

A2A's publisher is conditional on that gateway being enabled. There is no `runner` process profile or Plugin Runtime artifact/command subsystem in this executable. Installed Harness Plugin code is selected through the artifact's plugin catalog and settings, not a background wheel-installation API.

Scans start without incoming requests. Multiple replicas use short transactions, durable leases/fences, and idempotency to reject stale claims. A scan beginning or an HTTP acknowledgement does not prove its downstream work completed.

## Configure cadence and bounds

Use nested TOML settings or their exact **`A13N_SERVICE_*`** names. Old `FOUNDATION_*` names and former flat Settings fields are not consumed by the current loader.

```toml
[control]
recovery_poll_interval_seconds = 1
recovery_batch_limit = 64
recovery_item_timeout_seconds = 30
collection_poll_interval_seconds = 300
collection_batch_limit = 64
collection_timeout_seconds = 30

[subagents]
reconcile_poll_interval_seconds = 1
reconcile_drain_seconds = 30
```

For example, `control.recovery_poll_interval_seconds` maps to `A13N_SERVICE_CONTROL_RECOVERY_POLL_INTERVAL_SECONDS`; `subagents.reconcile_poll_interval_seconds` maps to the singular `A13N_SERVICE_SUBAGENT_RECONCILE_POLL_INTERVAL_SECONDS`.

| Setting                                    | Default     | Purpose                               |
| ------------------------------------------ | ----------- | ------------------------------------- |
| `control.recovery_poll_interval_seconds`   | 1 second    | Delay between recovery passes         |
| `control.recovery_batch_limit`             | 64          | Recovery candidates per batch         |
| `control.recovery_item_timeout_seconds`    | 30 seconds  | Per-item recovery deadline            |
| `control.collection_poll_interval_seconds` | 300 seconds | Collection cadence                    |
| `control.collection_batch_limit`           | 64          | Candidates per collection class       |
| `control.collection_timeout_seconds`       | 30 seconds  | Object-operation deadline             |
| `hooks.history_minimum_retention_days`     | 30 days     | Minimum eligible Hook history age     |
| `assets.tombstone_minimum_retention_days`  | 30 days     | Minimum eligible Asset metadata age   |
| `objects.orphan_minimum_age_hours`         | 24 hours    | Minimum orphan age before reclamation |
| `objects.publication_timeout_seconds`      | 120 seconds | Immutable publication deadline/lease  |

Webhook delivery, Asset cleanup, lifecycle projection/retention, Connectivity, Environment maintenance, and gateway waiting have their own settings. The [configuration reference](configuration-reference.md) lists all fields, environment spellings, bounds, and defaults rather than treating one poll interval as a universal timeout.

Increasing a scan interval delays attempts; it does not extend credentials, revive deleted resources, or make work retry-safe. A backlog can require many bounded passes. Observe actual lag and throughput before changing batch size or concurrency.

## Hook dispatch and recovery

Run transitions commit their lifecycle events before Webhook subscriptions are matched. The `hook_dispatch` task claims pending events with PostgreSQL `SKIP LOCKED`, then commits all matching Outbox rows and event completion together. Replicas can process different events concurrently; dispatch and HTTP delivery are unordered. An Outbox failure delays notifications without undoing the Run transition.

Matching observes subscriptions at dispatch time. A new subscription can receive an older pending event; a paused or deleted subscription is excluded. Completed events are never rematched. Automatic inline expiry at Run sealing still permits the owning Run's pending notifications.

The `hooks.dispatch_*` settings control polling, batch size, and bounded exponential retries independently of the `webhooks.*` HTTP delivery settings. Lifecycle reads expose `hook_dispatch_state`, attempt count, timing, and safe failure diagnostics. `failed` dispatch remains retained and requires an explicit operator retry after the cause is resolved:

```bash
a13n-service --config service.toml hooks retry-dispatch lev_EVENT_ID --organization-id org_ORGANIZATION_ID
```

Run this command from a protected terminal with the deployment's database configuration. It resets the failed event's attempt budget and makes it immediately eligible; pending or completed events are unchanged. Outbox delivery redrive remains a separate operation for destinations already selected by completed dispatch.

## Environment capacity

Logical Environment allocation does not start a target or consume target capacity. First use reserves capacity atomically before Provider I/O. Exhaustion returns `environment_capacity_exceeded`; limits do not evict existing targets.

Defaults are 1,000 prepared targets and 100 active Environments per Workspace. Set `A13N_SERVICE_ENVIRONMENT_MAX_TARGETS_PER_WORKSPACE` and `A13N_SERVICE_ENVIRONMENT_MAX_ACTIVE_PER_WORKSPACE` consistently on every worker. Stopped/unresolved managed targets still count toward target capacity. Confirmed deletion or reconciliation confirming target absence releases capacity, including reservations for interrupted creation when no target exists. Release capacity before retrying an exhausted allocation.

Active capacity counts distinct acquired Environments, not every Run sharing one Environment. A lazy Run acquires its slot only on first use. The last active Run ending or entering a waiting state starts the idle retention clock in the same transaction.

Worker maintenance defaults to batches of 64, concurrency 4, a 5-second interval, and a 60-second operation timeout. Due batches drain before the next interval. Provider latency and due volume can extend observed delay; failure backoff is distinct from the poll interval. Configure workers consistently.

[Environment management](resources.md#environment-providers-templates-and-targets) distinguishes Provider configuration, Template revisions, and actual Workspace targets.

## Retention is reference-aware

- Expired Skill upload receipts can disappear while published packages remain referenced. Retained Skill revisions protect their content.
- Asset deletion tombstones immediately; asynchronous object cleanup does not restore logical access if deletion fails. Dead letters and replay/audit evidence have separate retention requirements.
- Hook collection preserves configuration required by unfinished inline dispatch, waiting continuation, delivery/redrive, command replay, and audit. Pending or failed Hook dispatch also pins its lifecycle event. Audit dependencies can keep old history/tombstones beyond a minimum age.
- Retained Runs protect state, replay, and payload objects. A collection interval does not impose a new Run TTL.
- Unknown object namespaces are preserved. Workspace deletion continues its owned Secret erasure, Service Account tombstoning, grant removal, and Asset deletion. Reversible User disable is not equivalent to erasure.

A minimum age is eligibility, not a promise that every object is deleted at that instant. References, audit, outstanding operations, and failed cleanup can retain data longer.

Initial Run state uses a fresh key owned by that Run alone. Its publication and acceptance are expected to finish within `objects.orphan_minimum_age_hours`; resuming an uncommitted acceptance after a suspension longer than that window is not supported. Once the Run commits, its retained reference protects the state independently of age.

## Observe failures and shutdown

The `a13n_service.background` logger records task/role, duration, outcome, examined candidates, committed progress, deferred/failed counts, and lag. Empty passes are debug-level, actual work is info-level, and retryable dependency failures are warnings. Correlation IDs stay in log fields rather than unbounded metric labels. Attempted deletion is not reported as confirmed reclaimed bytes.

Temporary dependency failures retry under the task's bounded policy. Programming failures follow critical-component supervision and terminate the process after bounded cleanup rather than silently disabling an essential loop. Shutdown stops admission and cancels/finishes work within its owned budgets; unfinished durable claims remain available to later processes.

Repeated deferral often means a retained reference or audit dependency. Repeated failures and increasing lag need operator investigation, not blind deletion of state.

## Upgrade writers and collectors coherently

Use the same compatible schema and namespace-specific publication contract across object-writing processes and collectors. For objects that use publication fencing, do not enable a new collector while older writers can publish without that fence. Fresh Run state follows the orphan-age boundary described above. Apply reviewed migrations before admitting the new deployment, and follow the release's rollout instructions rather than manually creating tables.

Object stores must support atomic conditional deletion and fresh versions, including republishing identical bytes. Startup rejects incompatible storage. Retention grace alone is not a substitute for those capabilities.

[Service configuration](configuration.md#roles-and-migration-authority) explains migration ownership; repository migration authoring belongs in the contributor workflow, not a background task command.
