---
title: Operate the Service
description: Run control and worker roles at scale, verify readiness probes, execute zero-downtime database migrations, back up state, and tune outbox delivery.
---

Pass `--config service.toml` **before** every `a13n-service` subcommand using the deployment's shared stores and encryption keys.

## Process roles

| Role            | Responsibility                                                                         |
| --------------- | -------------------------------------------------------------------------------------- |
| `all` (default) | API, Console, background sweeps, auto-migration, and agent execution                   |
| `control`       | API, Console, SSE streams, and background sweeps                                       |
| `worker`        | Claims and runs agent attempts via Harness (`/healthz` and `/readyz` HTTP probes only) |

```sh
a13n-service --config service.toml run --role control
a13n-service --config service.toml run --role worker
```

Check `/healthz` (`200 ok`) and `/readyz` (`200 ready`; `"degraded": ["redis"]` indicates Redis is unreachable while durable PostgreSQL reads remain up) on every replica. Allow `worker.drain_seconds` + `tool_executions.shutdown_grace_seconds` + `finalization_seconds` in your supervisor's termination grace period.

## Backups

Back up all three together from a consistent recovery point before upgrades (see [upgrade notes](upgrade-notes.md)):

1. **PostgreSQL:** Accounts, resources, run records, and object pointers.
2. **Object storage:** Checkpoints, transcripts, assets, and skill bundles.
3. **Encryption keys (`encryption.key_file` or key ring):** Required to decrypt stored provider and connection secrets.

## Schema migrations

`all` and `control` auto-migrate on startup (`database.auto_migrate = true` under PostgreSQL advisory locking); `worker` never migrates. When using a dedicated migration Job, set `database.auto_migrate = false` on serving replicas and run:

```sh
a13n-service --config service.toml migrate
a13n-service --config service.toml migrate --check
```

### Roll out realtime in split deployments

1. Run `a13n-service --config service.toml migrate` with the target image and wait for the migration job to succeed.
2. Roll all `worker` replicas to the target image. Verify readiness and confirm that every old Worker has stopped claiming Runs before continuing.
3. Roll the `control` replicas, then open a test call and verify that it reaches `ready` and ends normally.

This order prevents new Control replicas from admitting realtime Runs that old Workers cannot execute. A call can end when its Worker drains; clients can reconnect to its thread after the previous run seals. See [realtime capacity and metrics](monitoring.md#realtime-capacity-and-health).

## Outbox retention and capacity

Webhooks, emails, and async cleanups deliver at-least-once through the durable outbox. Tune retention and worker parallelism per kind in `service.toml`:

```toml
[outbox.by_kind.webhook]
delivered_retention_seconds = 604800

[outbox.by_kind.checkpoint_cleanup]
delivered_retention_seconds = 3600
parallel = 2
```

Alert on `a13n_outbox_backlog_alert == 1` and `a13n_outbox_dead == 1` across `all` / `control` replicas. For vmagent/Alloy pull monitoring, run exactly one `writer = true` replica per collector (see [collector bootstrap](environment-collectors.md#bootstrap-an-optional-collector)).

## Operator CLI reference

| Command (`a13n-service --config service.toml ...`)            | Purpose                                                                                                   |
| ------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `bootstrap --email EMAIL [--password-stdin]`                  | Initialize first organization, workspace, and admin ([get started](get-started.md))                       |
| `user disable --email EMAIL` / `user enable --email EMAIL`    | Suspend or restore a user ([operator control](identity.md#operator-account-control))                      |
| `user reset-password --email EMAIL [--password-stdin]`        | Reset an active local-password user's password ([operator control](identity.md#operator-account-control)) |
| `migrate [--check]`                                           | Upgrade or verify the database schema                                                                     |
| `migrate-run-objects [--apply --backup-dir PATH] [--limit N]` | Convert legacy run objects while all replicas are stopped                                                 |
| `run [--role all\|control\|worker]`                           | Start a Service process                                                                                   |

<span id="convert-legacy-run-objects"></span>
