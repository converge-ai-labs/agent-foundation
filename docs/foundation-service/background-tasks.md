# Operate background tasks

Foundation Service runs periodic delivery, recovery, and cleanup in the `control` role. The `all` role includes these tasks once. Worker-only and connectivity-only processes do not run control maintenance. A2A contributes its publisher only when enabled, and Plugin Runtime command coordination runs in the `runner` profile.

Scans start immediately and continue without request traffic. Multiple control replicas are supported: short database transactions recheck authority, and leased operations reject expired claims. Workers continue to own Run execution, Environment maintenance, and lifecycle projection.

## Tune bounded work

Configure these fields through the same Settings inputs as the rest of the service. Environment variables use the `FOUNDATION_` prefix and uppercase field names.

| Settings field                             | Default | Meaning                                                     |
| ------------------------------------------ | ------- | ----------------------------------------------------------- |
| `control_recovery_poll_interval_seconds`   | 1       | Delay after each queue or async-result recovery scan        |
| `control_recovery_batch_limit`             | 64      | Candidate count per recovery scan                           |
| `control_recovery_item_timeout_seconds`    | 30      | Maximum time for each recovered item                        |
| `control_collection_poll_interval_seconds` | 300     | Delay after collection scans                                |
| `control_collection_batch_limit`           | 64      | Candidate count per collection class                        |
| `control_collection_timeout_seconds`       | 30      | Object operation timeout; relational batches remain bounded |
| `hook_history_minimum_retention_days`      | 30      | Minimum age before eligible Hook history is collected       |
| `asset_tombstone_minimum_retention_days`   | 30      | Minimum age before eligible Asset metadata is collected     |
| `object_orphan_minimum_age_hours`          | 24      | Minimum age before orphan discovery can reclaim bytes       |
| `object_publication_timeout_seconds`       | 120     | Maximum time and lease for immutable object publication     |

Webhook, A2A, Asset cleanup, lifecycle retention, Connector setup, and Plugin Runtime commands retain their own configured cadence and delivery bounds. Increasing a scan interval delays attempts; it never extends credential validity or restores deleted resources. A growing backlog can take many iterations to drain.

## Retention behavior

Expired Skill upload receipts can disappear while published packages remain available. Retained Skill Revisions and Plugin Runtime locks protect their content. Completed object deletion does not itself release command replay or audit requirements. Asset cleanup dead letters remain available for recovery; successful cleanup receipts can expire under the published-delivery retention horizon.

Hook collection preserves inline configuration needed for waiting Continue or Retry, outstanding delivery/redrive, command replay, and required audit. Audit records have no automatic expiry here and can therefore keep otherwise old Hook history or Asset tombstones retained indefinitely.

Retained Runs protect their state, replay, and payload objects. No new TTL is imposed on Runs, finalized queue/inbox history, Secret metadata, IAM tombstones, or successful Plugin artifacts. Unknown object namespaces are preserved. Workspace deletion resumes Secret erasure, Service Account tombstoning, live grant removal, and Asset deletion; disabling a User is reversible and does not trigger erasure.

## Observe recovery

The `a13n_service.background` logger reports a stable task name, owning role, duration, outcome, examined candidates, committed progress, deferred/failed counts, and observed lag. Empty iterations log at debug level; work logs at info, and retryable dependency failures log at warning with the exception type. Correlation IDs remain log fields rather than metric labels. Bytes reclaimed are not estimated from attempted deletes.

Temporary dependency failures retry after the configured delay. A programming failure follows critical component supervision and makes the service terminate after bounded cleanup. Shutdown cancels scans and leaves unfinished durable claims for the next process. A deferred collection result commonly means a retained reference or audit dependency, while repeated failures and increasing lag require operator investigation.

## Upgrade object-writing processes together

Apply the service migrations and upgrade every process that writes service objects before allowing the new control collector to scan. Older writers do not participate in the per-object publication fence. For the first upgrade, stop old control and Worker processes, migrate, and start the new deployment together. Subsequent replicas using this boundary can overlap normally.

The publication migration adds an empty table and its index, with no object backfill. Hook ownership constraints become deferred so a head and its Revisions can be collected atomically. PostgreSQL changes constraints under DDL locks; SQLite rebuilds those tables and restores the existing integrity triggers. Schedule the first upgrade during a maintenance window. Migrations run transactionally; retry an interrupted migration through the ordinary migration command. Rollback requires stopping the new collectors and writers before removing the publication table or restoring constraints.

Object stores must support atomic conditional deletion and fresh object versions, including when identical bytes are republished. Startup probes reject incompatible S3 endpoints. A grace period does not compensate for an endpoint that lacks these guarantees.
