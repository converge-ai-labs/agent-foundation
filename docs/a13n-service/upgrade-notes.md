---
title: Service upgrade notes
description: Check version-specific cutovers and convert retained data before reopening admission.
---

Use this page when upgrading an existing Service deployment, not for a new installation. Select the cutovers that apply to the source and target releases, follow the [backup and migration procedure](operations.md), and keep admission closed until data, workers and clients are compatible. Preserve unknown external-operation outcomes for reconciliation.

## Composable memory guidance rollout

Default and preset-following file guidance omit the optional `modules` key in stored configuration, and frozen memory mounts retain their previous shape. Existing memory-mounted Runs remain readable during a rolling upgrade. Upgrade every control and worker replica before saving an explicit module selection or launching the new organization workflows. Older processes cannot interpret explicit module configuration or execute newly introduced tools.

Before rolling back to a build without composable guidance, reset explicit selections to `modules: null` through the current API and drain the new organization tasks. Keep compatible readers for already accepted Runs; schema rollback does not convert accepted execution history. Automatic organization requires the policy owner's current and captured `read`, `write` and `run` authority; a revoked grant is recorded as an admission failure and follows the configured retry cooldown.

## Automatic memory policy rollout

The migration adds standalone policy and successful history-consumption tables. It preserves existing Space scheduler rows, Memory and Space IDs, files, revisions, Session bindings and accepted Runs. There is no data backfill, historical scan or model execution during migration. New foreign keys require brief locks on referenced tables; migration timeouts bound lock acquisition, and a rolled-back migration can be retried.

Upgrade every control and worker before configuring policy `instructions` or `history` or using standalone automation. Unset new policy fields are omitted from storage so default policies retain their previous shape. Old processes reject explicitly configured new fields. Before rollback, disable standalone policies, clear Space `instructions` and `history`, and drain accepted organization Runs. Retain the additive tables and compatible readers for accepted history; dropping the tables discards standalone policy and consumption progress.

History automation requires an explicit Session allowlist and starting seal time. Existing file-only policies retain their saved-change behavior; installation alone never opts them into conversation processing.

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

## Internal platform tools

Before Service 0.1, Service may introduce breaking changes and developers are responsible for migrating their own existing data. This internal-tool replacement is one such change: all previously normalized Agents may need complete reauthoring, including Agents whose removed tool entries were disabled. No compatibility layer, mixed-version Control/Worker operation or recovery of old Runs is promised. Before deployment, stop new admission and scheduled/manual dispatch, drain active Runs, and resolve pending approvals, waiting Runs and uncertain external-operation outcomes. Shut down the old deployment, update Control and Workers together, migrate or reauthor data, then resume admission.

## Tool-discovery selectors

**Service breaking change:** replace any `environment` entry in a discovery group's `capabilities` with `files`, `shell` and `computer` (or the subset you need). Operators must repair affected stored Agent revisions and frozen Run, Inbox and Schedule configurations themselves. Service does not migrate or translate them. Individual reads and reuse of incompatible records return `409 conflict` with `reason: configuration_unavailable` and the affected resource ID. Agent revision, Inbox and Schedule lists keep valid items and identify incompatible records in `unavailable`; follow `next_cursor` even when `items` is empty. Session thread frames report incompatible runs and open entries in the same way so other threads continue to refresh. This selector change does not change standalone Harness or Harness UI configuration.

## Webhook signatures

For subscriptions created before Standard Webhooks signing, replace the secret with a valid `whsec_…` value through an admin `PATCH`, or recreate the subscription. Old queued deliveries retain the old secret and fail with `signing_secret_unavailable`; redelivery cannot repair them. New deliveries use the replacement secret. Receivers use the [`webhook-*` headers](webhooks.md#payload-and-signature); the former `X-A13n-*` signature format is no longer sent.

## Desktop-control cutover

This change replaces Run-scoped assistance and pause/resume APIs with Environment-scoped Computer control. Use a coordinated cutover:

1. Stop new task and takeover admission on the old release. Drain its active workers and retained tool executions.
2. Using the old release, revoke and reconcile every legacy desktop grant and reservation. Complete and resume legacy desktop waits, or cancel their current thread head. Keep completed historical runs unchanged. Resolve unknown cleanup before proceeding.
3. Stop all old control and worker replicas. Back up the database, then run the schema migration once through the supported migration process. The migration refuses remaining live reservations, unresolved assistance or resumable desktop waits.
4. Upgrade control, workers and clients together. Prepare desktop guests with protocol v2 before enabling human access. Existing v1 guests require an explicit compatible replacement or operator-managed upgrade; attachment does not upgrade them.
5. Verify viewing, takeover and handback on a prepared target before reopening admission.

Mixed old/new replicas are unsupported for this cutover. Database downgrade refuses new control or handback history that the old release cannot represent. Repair forward after new history exists.
