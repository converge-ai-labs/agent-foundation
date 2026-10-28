---
title: Operate the Service
description: Check deployment health, upgrade safely, preserve data, and diagnose stalled work.
---

Operate Service with the same configuration and credentials used by its running processes. The examples below assume `a13n-service` is installed in that environment and `service.toml` names the deployment's [configuration](configuration.md). For containers, run operator commands inside the configured container or migration Job.

A routine upgrade follows four steps: back up the deployment, migrate with the new build, replace its processes, then verify readiness and a real run. The sections below explain each step and the recovery signals to watch.

## Process roles

Choose `all` for a single process. Split `control` and `worker` when API traffic and agent execution need independent capacity:

| Role            | Responsibility                                                                                 |
| --------------- | ---------------------------------------------------------------------------------------------- |
| `all` (default) | API, Console, maintenance and agent execution.                                                 |
| `control`       | API, Console, session streams and maintenance.                                                 |
| `worker`        | Claims accepted runs and executes them through Harness; HTTP serves only health and readiness. |

Run these commands in separate processes, each with its own listener address or container:

```sh
a13n-service --config service.toml run --role control
a13n-service --config service.toml run --role worker
```

All replicas share PostgreSQL, Redis, object storage and the encryption key ring. Put control replicas behind the API load balancer. For split deployments, set `server.control_url` to an address workers can reach for internal platform calls; it defaults to `server.public_url`. Configure shared execution resources explicitly, because automatic workspace provisioning runs only in `all`.

Each worker has `worker.slots` concurrent attempts. On shutdown it stops claiming and gives attempts up to `worker.drain_seconds` to hand off through a checkpoint. Retained tools then receive their separate `tool_executions.shutdown_grace_seconds` and finalization budget. Give the process supervisor enough time for these stages before forcing termination.

After a crash or exhausted drain, lease expiry allows another worker to resume from the last checkpoint in a new attempt. External side effects can already have happened; recovery neither rolls them back nor makes repetition safe. A run fails when it exhausts `worker.max_attempts` charged attempts.

## Health and readiness

Check the address of each replica, not only the load balancer. For a process listening locally on port 8080:

```sh
curl --fail-with-body http://127.0.0.1:8080/healthz
curl --fail-with-body http://127.0.0.1:8080/readyz
```

| Result                                   | Interpretation and next action                                                                                                              |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `/healthz`: `200`, `status: ok`          | The process serves HTTP. Use readiness to check its runtime.                                                                                |
| `/readyz`: `200`, `status: ready`        | Startup completed, background services are running and the database schema matches. Check `role` is the one intended.                       |
| `/readyz`: `503`, `dependency: runtime`  | Startup is incomplete or a background service stopped. Inspect process logs.                                                                |
| `/readyz`: `503`, `dependency: database` | Schema validation failed or exceeded `server.readiness_timeout`. Check connectivity and the migration result.                               |
| `/readyz`: `200`, `degraded: [redis]`    | Restore Redis. Durable reads continue, but live output is lost and rate limits are not enforced. Worker claims fall back to periodic scans. |

After an upgrade, also open a historical run and submit one model-only prompt. Test any required tool or environment separately. Readiness does not call model providers or verify all saved object contents.

## Backups

Keep these three parts from the same recovery point:

| Part                                         | What it preserves                                                     |
| -------------------------------------------- | --------------------------------------------------------------------- |
| PostgreSQL                                   | Accounts, resources, run facts and references to stored objects.      |
| Object storage                               | Checkpoints, display history, assets and skill packages.              |
| Encryption key ring or `encryption.key_file` | The keys needed to decrypt saved provider and connection credentials. |

1. Before upgrading, take a consistent database and object-store backup. The single-host Compose procedure stops Service while backing up.
2. Save the corresponding keys and deployment configuration in private storage outside the deployment.
3. Test restoration into a separate deployment before relying on the backup. Open historical data and verify that saved credentials work.

Use the deployment-specific commands in the [Compose backup guide](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose#backups-and-upgrades) or [Helm upgrade guide](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes#upgrades-and-backups). Changing an image or running `helm rollback` does not restore a migrated database or convert saved objects.

## Schema migrations

Choose one migration owner:

- **Automatic:** `all` and `control` migrate at startup when `database.auto_migrate = true`, the default.
- **Dedicated Job:** set `database.auto_migrate = false` on replicas and run the new build's migration command before starting them.

Workers never migrate. Concurrent migrators coordinate through a PostgreSQL advisory lock; `database.migration_advisory_lock_timeout` bounds their wait.

For a dedicated migration step, after the backup and release compatibility review:

```sh
a13n-service --config service.toml migrate
a13n-service --config service.toml migrate --check
```

A successful check confirms the schema expected by this build and the absence of legacy unpaged run pointers. If the command reports legacy objects, follow [object conversion](#convert-legacy-run-objects) before starting replicas.

Every process requires its build's exact schema at startup. During a rolling upgrade, an already-running old process can continue working against the changed schema, but its readiness becomes unavailable when the schema head differs. Review migration compatibility and load-balancer behavior before choosing a rolling rollout. Replace old processes, check each new replica, then verify historical reads and execution.

## Logs and metrics

Start troubleshooting with the failing request's `X-Request-Id`. Find its log record, then follow the run and attempt IDs to the failed operation. Request logs use route templates rather than raw URLs or query strings. Use [Monitor and troubleshoot](monitoring.md) to enable metrics, inspect run failures and configure tracing.

## Background work

Control replicas coordinate maintenance through database claims. A failed sweep logs its error and retries at the next interval. When work stalls, identify the affected operation before adding worker capacity:

| Symptom                                                        | Check                                                                       |
| -------------------------------------------------------------- | --------------------------------------------------------------------------- |
| Accepted runs stay queued                                      | Worker availability, free slots, checkpoint compatibility and claim errors. |
| A crashed attempt remains active                               | Lease expiry and the control-side recovery sweep.                           |
| Idle environments remain allocated                             | Template idle policy and provider maintenance errors.                       |
| Hosted environments expire                                     | Renewal failures and provider connectivity.                                 |
| Memory organization or background compaction remains unsettled | The operation's saved status and control reconciliation logs.               |
| Webhooks or email stop arriving                                | Outbox errors, destination availability and dead-delivery alerts.           |

Outbox delivery is at least once. Failed deliveries retry with exponential backoff, up to one hour between attempts, and become dead after the kind's `max_attempts`. Workspace administrators can inspect and redeliver [webhooks](files-and-webhooks.md#webhooks) after fixing the destination.

## Outbox retention and capacity

Retention controls completed delivery history; capacity controls how quickly pending deliveries progress. By default, successful rows remain for one day and dead rows for fourteen days. The purge sweep runs every minute, deleting batches of up to 1000 rows for at most five seconds. Pending rows stay queued regardless of age or queue depth.

For example, keep webhook history for seven days and reduce checkpoint-cleanup concurrency while retaining the shared defaults for other fields:

```toml
[outbox.by_kind.webhook]
delivered_retention_seconds = 604800

[outbox.by_kind.checkpoint_cleanup]
delivered_retention_seconds = 3600
parallel = 2
```

Use `outbox.defaults` for shared policy and `outbox.by_kind` for overrides. Each kind inherits unspecified fields. Environment overrides `A13N_OUTBOX__DEFAULTS` and `A13N_OUTBOX__BY_KIND` take whole JSON objects; they replace the corresponding TOML field. Restart processes after changing configuration. The [configuration reference](configuration-reference.md) lists limits and defaults.

Alert on `a13n_outbox_backlog_alert == 1` and `a13n_outbox_dead == 1`, taking the maximum across `all` and `control` replicas. Restore destination availability first, then adjust delivery capacity if producers still outpace consumers. Shorter retention cannot clear pending work.

## Environment monitoring collectors

For optional vmagent or Grafana Alloy integration, follow [collector bootstrap](environment-monitoring.md#bootstrap-an-optional-collector). Run exactly one `writer = true` process per collector in an `all` or `control` role; other API replicas use `writer = false`. Workers do not reconcile collector configuration.

Use this sequence when changing or retiring a collector:

1. Preserve the protected configuration directory shared by the writer and collector. Back up operator-owned forwarding configuration and credentials; Service regenerates its own fragment from environment bindings.
2. After a change, check the environment's collector status, the collector's active targets and fresh backend samples. `reload_requested` confirms acceptance of the request, not ingestion.
3. In external mode, remove the managed fragment and reload the collector before stopping the writer or disabling monitoring. Stopping Service leaves loaded scrape jobs active.

The Helm-managed vmagent uses a dedicated writer/collector Recreate Pod and a retained queue PVC. Before disabling it, unbind or disable sources, confirm target removal, and drain pending writes. Preserve the queue until delivery completes or you deliberately discard it. Fence a lost node before forced replacement; RWO storage and file locks do not fence another node. See the [Helm collector lifecycle](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes#environment-monitoring-collectors).

After rotating a deployment preset token, restart ordinary control Pods and explicitly replace credentials on saved sources. Removing a preset or allowlist entry affects future confirmation, not credentials already saved or accepted by the backend.

## Operator commands

Pass `--config service.toml` before the command name. Use the deployment's environment and storage access, including when running inside a container.

| Command after `a13n-service --config service.toml`            | Use                                                                                                     |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `bootstrap --email EMAIL [--password-stdin]`                  | Create the first organization, workspace and administrator; see [initial setup](get-started.md).        |
| `user disable --email EMAIL` / `user enable --email EMAIL`    | Suspend or restore an account through [operator account control](identity.md#operator-account-control). |
| `migrate [--check]`                                           | Upgrade or verify this build's schema.                                                                  |
| `migrate-run-objects [--apply --backup-dir PATH] [--limit N]` | Inspect or convert legacy objects while all replicas are stopped.                                       |
| `run [--role ROLE]`                                           | Start a Service process.                                                                                |

`a13n-service --version` prints the installed version. Bootstrap prompts for a password or reads the first line of standard input; it prints new IDs as JSON and exits `3` without changes if already initialized. `bootstrap`, `user` and `run` require the build's schema.

## Run search rollout

For upgrades that introduce run search, the migration adds an empty-default search column and builds its GIN index concurrently. It does not backfill historical content. Column and trigger changes still need brief table locks; configured migration timeouts bound the work. A failed index build can be rerun: invalid indexes are removed and completed valid indexes are retained.

Upgrade all control processes and readers before enabling new workers. Current workers write checkpoint format 4 and read formats 1–4; older workers skip formats they cannot continue. After new-format writes, keep compatible readers, Console and workers during rollback. Database downgrade drops derived search data but does not convert saved objects.

## Convert legacy run objects

Use this procedure only when upgrading from the original unpaged run display and the migration command reports legacy pointers. The schema migration renames the database field; the separate conversion supplies message ordinals and compressed objects.

1. Drain accepted and running work, then stop **all** control and worker replicas.
2. Complete the schema migration. A subsequent legacy-object error identifies the remaining conversion step.
3. Use database-owner privileges and a durable private backup directory outside the repository. The backups contain conversation data.
4. Inspect a batch, then apply the conversion:

```sh
a13n-service --config service.toml migrate-run-objects
a13n-service --config service.toml migrate-run-objects --apply --backup-dir /private/backups/a13n-run-objects
```

The command defaults to 100 runs; `--limit` accepts 1–1000. It verifies and backs up original objects and database pointers before replacing each run's pointers, preserving message IDs, order, content, stream coverage and checkpoint payload. Already-converted runs are skipped, so an interrupted batch can resume.

5. Repeat the apply command until it reports `"converted": 0`. Investigate any refusal: unfinished checkpoint cleanup, existing display pages, missing or damaged objects, and already-truncated history require resolution before proceeding.
6. Check the completed deployment migration:

```sh
a13n-service --config service.toml migrate --check
```

7. Resume replicas, open a historical run and its message list, then continue the conversation.

To roll back to the old unpaged reader, stop replicas and restore the backed-up database pointers and objects together. Later cleanup can remove original objects, so retain the private backup independently.
