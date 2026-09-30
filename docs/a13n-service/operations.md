---
title: Run and maintain
description: Process roles, migrations, health checks, background work, operator commands, and backups.
---

One `a13n-service` executable runs every part of the Service. All processes of a deployment share one PostgreSQL database, one Redis endpoint, one object store and one encryption key ring, [configured](configuration.md) identically.

## Process roles

```sh
a13n-service --config service.toml run --role control
a13n-service --config service.toml run --role worker
```

| Role            | Serves                                                                                                        | Background work                                          |
| --------------- | ------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| `control`       | Console, the HTTP API, thread streams, `/api/v1/openapi.json` and the interactive API page at `/api/v1/docs`. | All maintenance sweeps and deliveries.                   |
| `worker`        | Only `/healthz` and `/readyz`.                                                                                | Claims accepted runs and executes them with the Harness. |
| `all` (default) | Everything `control` serves.                                                                                  | Everything `control` and `worker` do.                    |

Use `all` for one process. Scale `control` replicas behind a load balancer and `worker` replicas separately; workers accept no API traffic. Control replicas coordinate maintenance work through database claims.

Each worker executes up to `worker.slots` attempts. If it crashes, another worker continues the run from its last checkpoint in a new attempt after the lease expires; a run fails after `worker.max_attempts` charged attempts. During shutdown, the worker stops claiming and allows up to `worker.drain_seconds` for a checkpointed handoff. External side effects are not automatically rolled back or guaranteed safe to repeat.

## Schema migrations

```sh
a13n-service --config service.toml migrate          # upgrade to this build's schema
a13n-service --config service.toml migrate --check  # verify the schema matches without changing it
```

With `database.auto_migrate = true` (the default), `all` and `control` processes migrate at startup. Every migration holds a PostgreSQL advisory lock, so concurrent starts migrate once; `database.migration_advisory_lock_timeout` bounds the wait for it. Workers never migrate. Every process checks at startup that the database schema is exactly the one its build expects, and refuses to start otherwise ("run a13n-service migrate").

For a dedicated migration step, such as the Helm chart's migration Job, set `database.auto_migrate = false` on replicas and run `migrate` before starting them. During rolling upgrades, older replicas keep serving against the migrated schema, so review each release's schema changes for compatibility with the previous one.

## Health and readiness

| Endpoint       | Response                                                                                                                           |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `GET /healthz` | Always `200 {"status": "ok", "role": "<role>"}` while the process serves HTTP.                                                     |
| `GET /readyz`  | `200 {"status": "ready", "role": "<role>"}` once startup finished, background tasks are running and the database schema is usable. |

Readiness returns `503 {"status": "unavailable", "dependency": "runtime"}` before startup completes or after a background task stopped, and `"dependency": "database"` when the schema cannot be checked within `server.readiness_timeout`. Losing Redis keeps the process ready and adds `"degraded": ["redis"]`, because Redis only speeds the Service up.

## Background work

Control processes maintain queued work and deliveries in bounded sweeps. Failed passes are logged and retried at the next interval.

| Sweep                           | Interval                           | Work                                                                                                                       |
| ------------------------------- | ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `advance_threads`               | `control.scan_seconds`             | Starts the next queued input of idle threads.                                                                              |
| `expire_leases`                 | `worker.authority_seconds`         | Ends attempts whose lease expired, so the run continues on another worker or fails.                                        |
| `expire_credentials`            | `auth.expiry_scan_seconds`         | Deletes expired or revoked login sessions, mail links and unaccepted invitations.                                          |
| `maintain_environments`         | `environments.scan_seconds`        | Stops or deletes managed environments idle past their template's thresholds, and continues unfinished provider operations. |
| `renew_environments`            | `environments.scan_seconds`        | Renews ready hosted sandboxes that would otherwise end, each call bounded by `environments.renewal_seconds`.               |
| `recover_connection_operations` | `providers.operation_scan_seconds` | Settles connection authorization operations whose owner disappeared past their deadline.                                   |
| `deliver_outbox`                | `control.scan_seconds`             | Delivers webhooks, identity mail and subagent results.                                                                     |
| `purge_outbox`                  | every 60 seconds by default        | Removes settled deliveries past the kind’s settlement-based retention.                                                     |

Deliveries are at least once. A sender claims a batch of `outbox.defaults.batch` rows for `outbox.defaults.lease_seconds`; a failed delivery is retried with exponential backoff (up to one hour between tries) until it has used `outbox.defaults.max_attempts` attempts, and is then marked dead. Webhook subscribers can inspect and redeliver deliveries through the API; see [webhooks](files-and-webhooks.md#webhooks).

## Operator commands

| Command                                                   | Purpose                                                                                                                                                                                                                                                                            |
| --------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `a13n-service bootstrap --email EMAIL [--password-stdin]` | Creates the first organization, workspace and administrator. Prompts for a password of at least 8 characters, or reads the first line of standard input. Prints the new IDs as JSON; exits `3` without changes when the Service is already initialized, and `1` for invalid input. |
| `a13n-service user disable --email EMAIL`                 | Disables a user's account; see [operator account control](identity.md#operator-account-control).                                                                                                                                                                                   |
| `a13n-service user enable --email EMAIL`                  | Re-enables it with the grants and keys it had.                                                                                                                                                                                                                                     |
| `a13n-service migrate [--check]`                          | Migrates or checks the database schema.                                                                                                                                                                                                                                            |
| `a13n-service run [--role ROLE]`                          | Runs a process.                                                                                                                                                                                                                                                                    |
| `a13n-service --version`                                  | Prints the installed version.                                                                                                                                                                                                                                                      |

Pass `--config` before the command name. `bootstrap`, `user` and `run` refuse a database whose schema does not match the build.

## Logs and metrics

Every process logs each request, run and delivery with the IDs that lead from one to the other, and can serve Prometheus metrics for dashboards and alerts. Request URLs and query strings are never logged. Every response carries an `X-Request-Id` that also appears in error bodies and in the request's log records. See [Monitor and troubleshoot](monitoring.md).

## Backups

Back up PostgreSQL, the object store and the encryption key ring (or `encryption.key_file`) together. Stored credentials cannot be decrypted without the key that wrote them, and run checkpoints, displays, assets and skill packages live in the object store. The [single-host Compose stack](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose#backups-and-upgrades) and the [Helm chart](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes#upgrades-and-backups) describe backing up, restoring and upgrading each deployment.

### Outbox retention and capacity

The same policy applies to webhooks, email, child results, memory purges and checkpoint reclamation. Defaults retain successful deliveries for one day after completion and dead deliveries for fourteen days after failure. Every minute, the purge sweep deletes up to 1000 expired rows per short transaction and repeats for at most five seconds. Pending work is never discarded to shrink the table.

Configure shared defaults and override only the fields that differ for a kind:

```toml
[outbox]
purge_interval_seconds = 60
purge_batch = 1000
purge_budget_seconds = 5

[outbox.defaults]
delivered_retention_seconds = 86400
dead_retention_seconds = 1209600
max_attempts = 12
batch = 32
parallel = 8
lease_seconds = 60
backlog_count = 10000
backlog_age_seconds = 300
backlog_alert_seconds = 300

[outbox.by_kind.webhook]
delivered_retention_seconds = 604800

[outbox.by_kind.checkpoint_cleanup]
delivered_retention_seconds = 3600
parallel = 2
```

Environment overrides follow the existing section convention: `A13N_OUTBOX__DEFAULTS` and `A13N_OUTBOX__BY_KIND` carry JSON objects. The environment replaces the corresponding TOML field, then each kind inherits unspecified policy fields from defaults. Unknown fields and kinds fail startup. Configuration is resolved once per process; restart to apply a change.

Alert on `a13n_outbox_backlog_alert == 1` and `a13n_outbox_dead == 1`, taking the maximum across control replicas. Check the failing destination and restore delivery capacity; retention does not discard pending work.
