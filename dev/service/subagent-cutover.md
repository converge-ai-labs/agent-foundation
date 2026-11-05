# Unified subagents cutover

Service and Console switch together to `subagents: {mode, named, dynamic}`. Old map-only policies, `subagent_mode`, and per-edge Run patches are rejected. Harness's declared-subagent behavior and saved wire defaults remain supported.

## Prepare

1. Back up PostgreSQL and the corresponding object-store namespace together. Record the current application build and migration revision.
2. Stop new submissions and Schedule dispatch. Drain accepted/running Runs with the old workers, then stop Control, workers and external writers. Waiting Runs may remain if their saved data is compatible. The migration refuses accepted or running Runs.
3. Inventory Agent revisions, Run/Inbox options, Schedule invocations and occurrence snapshots. An old per-edge override requires its actual base revision. A Run supplies that revision; an assigned message can use its accepted Run. An unassigned default-revision message or Schedule does not identify a historical base. Pin a verified revision before the cutover, or explicitly clear and recreate that scoped input or Schedule. Never resolve it from whichever revision is default at migration time.
4. Inventory retained MCP App bindings. Their public and private references now name `agent_definition_id`. The migration refuses old bindings in Run checkpoints, tails or immutable page dependencies. Export the affected Sessions, then either perform a reviewed offline rewrite of their object graph and all hashes/references, or reset that scoped history before upgrade. Do not patch only PostgreSQL pointers or discard individual originals: those objects participate in immutable history. A disposable preview can instead be fully reseeded.

## Convert and deploy

Apply revision `03610e577d35` through the ordinary migration owner. Do not run it from a worker. The generated schema migration also performs one transactional data conversion:

- Convert each Agent revision to the unified policy, retaining its old execution mode and disabling dynamic creation. Recompute its configuration digest.
- Materialize each legacy edge patch against its actual base. Retain pins, context, environment, permissions and limits. Convert all retained Run/Inbox and Schedule/occurrence option owners.
- Recompute Run options digests from submitted Inbox options or the inherited Run owner. Frozen Run options omit nulls and contain resolved pins, so they are not the submitted digest source. Unreconstructable historical ownership fails the migration; explicitly reset the affected obsolete history instead of inventing a digest.
- Temporarily disable only application triggers on the converted tables. Keep foreign-key validation enabled and immediate, restore triggers before completion, and roll back all DDL/data changes on failure.

Original full request bodies are not retained for every idempotency owner. Existing create, fork, inbox, resume and Schedule request digests remain historical evidence; the conversion does not claim that a new-shaped request is equivalent. Clients must use fresh idempotency keys after cutover and must not retry pre-cutover commands. Exported Agent bundles must also use the new policy before import.

Start matching Control/readers and Console, then workers. Verify the migration head, metadata/rule parity, retained resource reads, options digests, zero disabled application triggers, and a named delegation. Enable dynamic spawning on a test Agent and verify spawn, result delivery, deferred continuation, archive refusal for new acceptance, and retained-snapshot fork. Then reopen submissions and Schedule dispatch.

For a disposable local checkout, the existing reset workflow replaces only that checkout's stores:

```sh
make dev-stop
make dev-reset STATE=seeded
make dev
```

Do not apply that reset to shared or hosted data. This change does not automatically reset any deployment.

## Recovery

A failed migration is transactional; correct the preflight condition and retry while writers remain stopped. Schema downgrade is available only before any dynamic policy or Thread snapshot exists, and only when replacement policies can be represented by old edge patches. Once new snapshots or MCP App objects have been written, prefer a forward repair. Reverting application binaries alone is unsafe; a full rollback restores the matching database, object-store backup and application build together.
