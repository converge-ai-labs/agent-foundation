# Scheduling, Workers, and Recovery

## Design Position

Every Foundation Worker runs the same periodic Run scan, claim, lease-renewal, and expired-lease takeover contract through its configured Plugin Runtime profile. An `on_demand` Worker preflights each candidate against its process-local loaded registry. A `runner` Supervisor discovers required Runtime locks and each matching Runner scans only that lock. PostgreSQL remains authoritative for Run eligibility, Attempt generations, leases, fences, recovery budget, and outcomes. Foundation has no separate Run Scheduler, recovery controller, or Redis dispatch queue.

Adding Worker processes or replicas adds competing consumers of the same relational contract. Row locking or equivalent compare-and-swap plus monotonic Attempt numbers and fences allows only one Worker to create the next generation. Redis remains available for domain-owned live data flow such as the Run stream and Thread control wakeups, but no Redis value discovers, creates, transfers, or completes a RunAttempt or consumes a Thread inbox entry.

Workers are container-role loops, not durable product owners. Under the [Plugin Runtime contract](26-harness-plugin-artifacts-and-runtime-loading.md), the default profile executes in the Worker interpreter and the optional runner profile uses a stable Supervisor plus lock-scoped children. A deployment scales the `worker` role by adding replicas whose selected execution loops compete through the same relational claim contract.

[Durable Thread Persistence](24-thread-persistence.md) owns current-Run and continuation-head selection; [Durable Run State](14-run-persistence.md) owns Run status, scheduling fields, and budget; [Durable Run Attempt Persistence](15-run-attempt-persistence.md) owns the exact claim, takeover, lease, fence, preparation-decision, and Attempt outcome transactions.

## Worker Scan and Claim Contract

Each non-draining on-demand Worker performs a bounded, deterministic scan across eligible candidates, then completes exact Plugin compatibility preflight before claim. Each runner Supervisor instead discovers the bounded set of referenced Runtime lock digests, ensures matching Runners exist, and lets each Runner scan only its exact lock. Discovery and preflight grant no Attempt ownership. The selected execution loop can consider:

- an initial `accepted` Run whose `available_at` has arrived and which has no current Attempt;
- a `running` Run whose highest-numbered prior Attempt is retryable `failed`, which has no current Attempt, and whose `available_at` has arrived; or
- a `running` Run whose highest-numbered prior Attempt is `yielded`, which has no current Attempt, and whose `available_at` has arrived; or
- a `running` Run whose selected `leased` or `running` Attempt has an expired lease.

The yielded candidate has reason-specific scheduling eligibility in addition to ordinary Runtime and state compatibility preflight. After `yield_reason="service_drain"`, a compatible claimant whose immutable `worker_build_id` differs from the yielded Attempt may claim immediately. A same-build claimant skips it until the finite positive `handoff_preference_window` has elapsed from `finished_at`, then may claim as a capacity fallback. The default window equals one RunAttempt lease duration. After `yield_reason="runner_rotation"`, any compatible non-draining execution loop whose Runtime lock matches exactly may claim immediately, regardless of `worker_build_id`. The build preference does not delay Runner rotation, initial claim, retryable-failure recovery, or expired-lease takeover. A draining Worker never claims any candidate.

Build difference is neither authority nor compatibility proof. Every claimant must still pass the existing exact Runtime, state-schema, Harness, Plugin, Skill, Environment, and artifact compatibility checks, and the claim transaction must still win the Run lease and fence. The preference chooses no Pod; for service drain it merely lets compatible new-build capacity win during ordinary rolling overlap while retaining bounded same-build fallback.

The scan is only candidate discovery. The execution loop must revalidate the exact Run, Thread selection, current Attempt, lease condition, and budget in the short claim transaction. For an expired lease, that transaction marks the old Attempt `failed`, disables its lease, charges known usage, and either creates and selects one new `leased` Attempt or seals the Run as `failed` when budget is exhausted. For an initial or backoff-ready Run, the same transaction either creates the next Attempt or fails the Run when its accepted recovery budget no longer permits one. For a yielded Run, it creates a successor with `recovery_reason="planned_handoff"` only after the reason-specific scheduling eligibility is revalidated; the yield already consumed handoff budget, and this successor does not consume recovery budget. Every candidate, including a planned-handoff successor, remains subject to the fixed recovery deadline and aggregate usage ceilings; exhaustion seals the Run instead of creating another Attempt. Every successful claim increments the complete `attempts_started` audit count. A direct budget failure also clears active selection and model snapshot, freezes the state key, retains the failed Run as the Thread's current Run, preserves the prior continuation head, increments the Thread version, and appends the applicable lifecycle facts.

The first successful claim changes the Run from `accepted` to `running`. Replacement claims leave it `running`; a Run never returns to `accepted` after its first Attempt. A concurrent Worker that loses the row lock or compare-and-swap creates nothing and continues its scan. Claim transactions perform no object, artifact, policy-provider, model, Connector, Secret-store, or Environment I/O.

Operational admission control, queue names, priority, and fairness may order or delay scans. They never form another ownership authority.

## Claim, Preparation, and Run Sequence

```mermaid
sequenceDiagram
    participant Executor as Worker loop or lock-scoped Runner
    participant DB as PostgreSQL
    participant Objects as State and artifacts
    participant Harness

    loop bounded periodic scan
        Executor->>DB: find compatible accepted, yielded, backoff-ready, or lease-expired Run
        Executor->>DB: short claim or takeover transaction
        alt transaction wins and budget permits
            DB-->>Executor: new leased Attempt, fence, and exact Run metadata
        else candidate changed or another Worker won
            DB-->>Executor: no claim
        else budget exhausted
            DB-->>Executor: old Attempt failed when present, Run failed, and no new Attempt
        end
    end
    Executor->>Objects: outside transaction, read exact state and frozen artifacts
    Executor->>Executor: validate state, dependencies, authority, and unknown outcomes
    Executor->>DB: short fenced preparation-decision CAS
    alt continue
        DB-->>Executor: preparation accepted
        Executor->>Harness: enter one logical Run with fresh RunBindings and EnvironmentRuntime
        loop bounded heartbeat
            Executor->>DB: renew only while this Attempt still owns the lease
        end
        Executor->>DB: fenced state, usage, and Run outcome writes
    else retry later
        DB-->>Executor: Attempt failed, Run remains running with available_at
    else fail Run
        DB-->>Executor: Attempt failed, Run sealed failed
    end
```

Before claim, an on-demand Worker verifies that every required PluginVersion is either exactly loaded or additively compatible; a conflict leaves the Run unclaimed. In runner mode, the Supervisor ensures that a Runner exists for every lock it serves, and each Runner filters to an equal `runtime_lock_digest`. Missing compatible capacity or artifacts does not change durable eligibility and never causes another lock to be substituted. On-demand artifact reads and import preflight occur outside every relational session and transaction.

The winning execution loop closes the claim transaction before every state or artifact read. It renews the lease while preparing. If ownership changes before its preparation decision, the fenced compare-and-swap fails and all local values are discarded.

## Fencing and Lease Meaning

Every Worker-originated lifecycle mutation includes the Run ID, RunAttempt ID, fence, Worker generation, lease proof, and expected Run version. It succeeds only while the Run still selects that non-terminal Attempt, the lease is unexpired, and the requested transition remains legal.

Fencing applies to:

- Attempt preparation, state, heartbeat, and outcome;
- Run state-object conditional replacement and sealing;
- lifecycle events and retained Item publication;
- waiting-state sealing and feedback incorporation;
- child acceptance and result incorporation; and
- terminal Run outcomes and their atomic Thread current/head update.

Lease renewal proves only that the selected Worker generation is still alive and authorized to publish. It does not commit Agent progress, extend credential lifetime, or make process memory recoverable. A renewal that cannot confirm current ownership causes the Worker to stop model and tool work and suppress authoritative publication.

Readiness failure, drain, and Runner claim gating do not revoke an already selected Attempt. While an owner waits for a safe handoff boundary, publishes or reconciles checkpoint state, quiesces the local Run, and prepares the yield transaction, it continues the ordinary heartbeat and renewal cadence. Yield and renewal serialize through the same selected-Attempt CAS and fence. Only a committed Attempt terminal transition, or arrival of the drain deadline, lets that owner stop renewal; one failed yield CAS never does.

Usage ingestion has the narrow exception defined by [Events, Usage, and Delivery](20-events-usage-and-delivery.md): immutable usage evidence for already incurred work can arrive later under its original RunAttempt attribution, but it cannot restore an Attempt or advance Run lifecycle.

A stale Worker may publish bounded non-authoritative telemetry identifying its Attempt. It cannot publish a lifecycle event, retained Item, state object, or outcome that consumers could mistake for current product state.

## Recovery Trigger

Replacement becomes eligible only when one of these conditions holds:

- the selected Attempt lease expires, so another Worker's takeover transaction can replace it;
- the owning Worker commits that its Attempt can no longer produce an authoritative Run result, terminalizes that Attempt as `failed`, clears the current selection, and schedules an in-budget retry through `available_at`;
- the owning Worker commits `yielded` from a complete safe checkpoint, clears the current selection, and makes the still-running Run immediately available for a planned-handoff successor.

The second case is the precise meaning of losing outcome certainty for an Attempt. It concerns the whole Attempt's ability to finish authoritatively. A single Agent tool call with `unknown_outcome` does not fail the Attempt and does not trigger replacement; it is recovery context only if replacement is already needed for one of the reasons above.

Foundation defines no `lost` RunAttempt status. An expired old Attempt becomes `failed` only in the transaction that replaces it or fails the Run, so there is no separate isolation phase or controller-owned intermediate state.

## Recovery Checks and Decision

The newly created Attempt already owns the lease before recovery checks begin. Its Worker performs all object, artifact, and authority reads outside database transactions and then commits one short fenced preparation decision.

| Check                       | What must be true                                                                                                                                                                                                                                                                     |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| State                       | The deterministic `state.json` exists and its key metadata, tenant, Run, Thread, digest, size, checkpoint sequence, outer schema, AgentPresetVersion, Runtime lock, and required Harness, Host, Capability, and Environment-state codecs validate exactly.                            |
| Unknown Agent tool outcomes | Every durable prior Agent tool dispatch absent from that exact complete state can be represented as bounded `unknown_outcome` context. The check reads no provider business state and never automatically replays a call.                                                             |
| Budget                      | The already-created Attempt remains within the applicable `max_recovery_attempts` or `max_handoffs` count, the fixed `recovery_deadline_at`, and every configured aggregate usage ceiling after all durable known usage charges. Unknown required usage cannot be assumed to be zero. |
| Frozen compatibility        | The exact AgentPresetVersion, Runtime lock, model execution snapshot, managed Skill artifacts, Connector contracts, Environment connector locks, state-owned Environment configuration, and all other frozen dependencies are present, digest-valid, and compatible.                  |
| Current authority           | Current Workspace and principal policy, RoleBindings, Connection eligibility, Environment provider selection, required Secret metadata, and intended credential uses still authorize reconstruction. Persisted references grant no authority by themselves.                           |

The decision is complete:

- if every check passes, the fenced transaction stores the bounded `unknown_outcome` projection on the new Attempt and permits reconstruction and Harness entry;
- if a failed check is explicitly transient and retryable and budget remains, the transaction marks this Attempt `failed`, clears it as current, leaves the Run `running`, and moves `available_at` to a bounded future instant; or
- if a check is permanent, non-retryable, unrepresentable, or out of budget, the transaction marks this Attempt `failed`, clears it as current, and seals the Run as `failed` while preserving the Thread's prior continuation head.

A confirmed missing or corrupt state, incompatible schema or lock, digest mismatch, or current authorization denial fails the Run. Temporary object-store or immutable-artifact unavailability can retry only under explicit policy and remaining budget. If the Worker itself disappears during these checks, no decision commits; lease expiry makes the same Run eligible for another ordinary takeover.

The same preparation contract applies to a planned-handoff successor. It reads the latest successfully committed complete value at the Run's unchanged `state.json` key and does not require a handoff-specific checkpoint marker or infer `yielded` from object contents. It preserves the exact pinned Runtime lock and accepts a different Foundation Service build only when that build can read the state schemas and serve every frozen dependency named by the Run.

Recovery does not probe model reachability, inspect external tool or provider business state, validate or reconcile a previous Sandbox, or resume a previous Environment resource. Model reachability and fresh Environment connection are ordinary outcomes of the newly owned Attempt.

## Worker Run Boundary

After preparation succeeds, the owning Worker execution loop reconstructs safe process-local Agent values from the exact AgentPresetVersion, Run-pinned Runtime lock, fresh authorized credentials, and Run-owned model execution snapshot. It never re-resolves current ModelConfig or the active Runtime lock. Managed plugin factories create fresh Agent-specific instances. Managed Skill packages named by the effective selection frozen in `state.json` are verified and materialized through a fresh `SkillManager` and fresh Environment before model exposure.

The execution loop constructs fresh Environment connector scopes from the exact desired mount configuration in `state.json`. Each connector attaches the configured already-running resource and supplies one fresh process-local attachment. The Worker adapts those attachments into runtime mounts, constructs and retains one `EnvironmentRuntime`, and keeps each resource alive only while its attachment scope, the Harness run, and the Attempt lease remain active. Foundation does not create, resume, pause, destroy, lease, validate, or reconcile the external resource. A fresh connection failure is an Attempt execution failure, classified under the ordinary retry and budget rules.

The execution loop calls the public process-local Harness Python API from its verified Runtime. It is the sole consumer of the `HarnessRunStream` and owning `HarnessAguiObserver`. Database sessions and locks never span reconstruction I/O, provider calls, Harness work, waits, sleeps, event streaming, or cleanup.

As soon as Harness supplies its Run identity and before the first live observation, the execution loop binds that identity immutably to the current Attempt under its fence and changes the Attempt from `leased` to `running`. A replacement creates a fresh Attempt, Harness Run, connector scopes, attachments, runtime mounts, `EnvironmentRuntime`, clients, credentials, and `RunBindings`. It never restores another process's task, session, socket, attachment, runtime, Sandbox, or stream subscriber.

## Thread Inbox and Control Reconciliation

The execution loop owns one process-local control dispatcher for every active RunAttempt. It registers the current `(run_attempt_id, fence)` with the live `HarnessRunStream` and joins the owning Thread's Redis control Stream consumer group under its Worker identity and generation. Claim, takeover, Redis wakeup, and every mandatory execution boundary invoke the complete [steer-consumption and reconciliation contract](35-agent-control-active-execution.md#steer-consumption-and-state-commitment); this scheduling contract does not define another inbox ordering, delivery, or completion flow.

A claim or takeover reconciles PostgreSQL before relying on the Redis group and can then reclaim deliveries from a prior Worker generation. Every signal means only that the dispatcher must re-read the Thread's durable state. Missing, trimmed, expired, duplicated, stale, or already acknowledged signals never change the decision made from PostgreSQL.

Worker shutdown unregisters process-local run controls and stops group consumption. Redis acknowledgement, consumer replacement, and dispatcher cleanup do not advance an inbox or Run domain status.

## Retry Semantics

Retries remain owned by the layer that knows the failed boundary:

- bounded connection and keep-alive transport retries remain in the Environment connector;
- Harness semantic recovery creates another ModelAttempt inside one Harness Run;
- a pending steer that can no longer enter the current native Run follows the [active-control recovery rule](35-agent-control-active-execution.md#steer-consumption-and-state-commitment);
- Foundation creates another RunAttempt only after the prior Attempt has failed, yielded, or had its lease expire, and only under the applicable Run budget;
- retrying sealed terminal intent creates a successor Run rather than reopening the original;
- a recovered Agent decision is an ordinary new tool call, not a replay command; and
- Foundation does not guarantee reuse of a prior invocation or idempotency key across RunAttempts.

Backoff uses the Run's exact durable `available_at`; Worker or process restart does not reset it. `attempts_started` counts all RunAttempts separately from Harness ModelAttempts and Connector retries. The recovery and planned-handoff limits are independent within the same Run-owned deadline and usage ceilings.

## Shutdown and Drain

A control process stops accepting product mutations and streaming connections before stopping its publishers and other domain-owned control work. An on-demand Worker stops its scan; a runner Supervisor gates every child scan. The selected execution loop sets a process-local handoff request on every active Attempt and continues ordinary execution, heartbeat, and lease renewal until a safe boundary. There it confirms complete state at the existing key, fences and closes local Run resources while continuing renewal, and attempts the short `yielded` transaction. A normal outcome, cancellation, or failure that commits first remains authoritative. Successful yield is the only voluntary release boundary and stops renewal only after commit.

If handoff budget is exhausted, an active Attempt continues toward an ordinary outcome until the drain deadline. If the deadline arrives before any terminal decision commits, the execution loop fences local work, stops renewal, and exits; no other Worker may take over until the recorded lease actually expires. Plugin activation itself adds no shorter deadline. Complete role behavior is owned by [Runtime Configuration and Deployment](01-runtime-configuration-and-deployment.md#drain-and-shutdown).

Shutdown never extends a lease indefinitely or marks unfinished work successful. Worker-only processes never migrate. Role overlap during rollout remains safe through leases, fencing, and transactional claims.

## Failure Semantics

| Failure                                                            | Durable outcome                                                                                                     |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------- |
| Concurrent Workers scan the same Run                               | Exactly one claim or takeover transaction creates the next Attempt; losers create nothing.                          |
| No compatible execution loop can serve the Run's Runtime lock      | Run remains eligible or the claimed Attempt follows bounded preparation failure; no Runtime is substituted.         |
| Worker crashes before claim commit                                 | The transaction commits no partial Attempt ownership.                                                               |
| Worker crashes after claim or during preparation                   | Its lease expires; a later Worker's takeover marks it `failed` and creates at most one successor within budget.     |
| Worker crashes after uncheckpointed Agent tool dispatch            | The successor compares durable dispatch records with exact state and projects unmatched calls as `unknown_outcome`. |
| Worker drains while a model, tool batch, or inline child is active | It keeps renewing and waits for a complete safe boundary; no second Worker may execute the Run.                     |
| Checkpoint publication or object reconciliation spans heartbeats   | The selected Attempt keeps renewing throughout; the checkpoint operation does not release authority.                |
| Yield races with heartbeat renewal                                 | The shared Attempt CAS serializes them; after yield commits, late renewal is rejected.                              |
| Yield transaction fails while the old owner remains authoritative  | The owner keeps renewing and retries or commits another legal result; the failure alone causes no takeover.         |
| Drain deadline arrives before yield                                | The owner stops renewal and exits; takeover remains forbidden until lease expiry.                                   |
| Compatible new-build and old-build Workers see a yielded Run       | New build may claim immediately; same build skips until the preference window elapses.                              |
| Heartbeat arrives after lease expiry or takeover                   | The old Worker is stale even if it reconnects; every authoritative write is rejected.                               |
| Preparation finds a retryable dependency outage                    | The claimed Attempt fails; the Run remains `running` with bounded `available_at` while budget remains.              |
| Preparation finds permanent incompatibility or revoked authority   | The claimed Attempt and Run fail; no Harness model or tool work starts.                                             |
| Managed Skill materialization stops                                | No partial catalog reaches Harness; the Attempt follows ordinary retry and budget rules.                            |
| Redis live data flow is unavailable                                | Relational ownership and Thread inbox remain intact; safe-point reconciliation replaces no claim or domain fact.    |

## Invariants

01. Every Worker runs the same bounded periodic Run scan and transactional claim/takeover contract through its configured Runtime profile; Foundation has no separate Run Scheduler or recovery controller.
02. PostgreSQL owns Run eligibility and RunAttempt state; Redis never creates, completes, or transfers a RunAttempt and never consumes a Thread inbox entry.
03. At most one selected Attempt exists for a Run, and only its unexpired matching lease authorizes Worker mutation.
04. Expired-lease takeover atomically fails the old Attempt and creates at most one new generation or fails the Run.
05. `accepted` is pre-first-attempt only. Replacement and backoff keep the same Run `running`; a `failed` Run never returns to `accepted`.
06. Attempt `failed` is generation-terminal and does not by itself imply Run `failed`; Foundation defines no Attempt `lost` state.
07. Object, artifact, authorization, Environment, model, tool, and other external I/O never occurs inside a claim, takeover, or decision transaction.
08. Recovery checks occur only after the new Attempt owns a lease and commit through one short fenced preparation decision.
09. Unknown Agent tool outcomes are shown to the next Agent and never trigger replacement or automatic replay by themselves.
10. Replacement Attempts use fresh process-local values and only complete, conditionally committed Run state.
11. Foundation persists no generic dispatch phase, Environment connection lease, or Environment resource reconciliation state.
12. Waiting and terminal Runs are sealed; feedback or retry creates another Run.
13. Terminal Run sealing atomically updates the owning Thread under the same fence, and late immutable usage cannot mutate Run lifecycle.
14. An on-demand Worker claims only after exact additive compatibility preflight, and a Runner claims only an equal lock digest; no preparation or recovery path substitutes another Runtime.
15. Every Attempt owner follows the active-control reconciliation contract; Redis consumer-group progress is only a wakeup optimization.
16. Drain gates claim and takeover scans but preserves every active Attempt's heartbeat and lease authority until a terminal commit or drain deadline.
17. A committed `yielded` transition releases the current selection without sealing the Run; its successor receives a fresh Attempt and Harness Run from the same latest complete state key.
18. Yield and heartbeat use one CAS/fence authority, so a CAS conflict cannot create two valid owners or silently release the old lease.
19. Build preference after a service-drain handoff is bounded and advisory: compatibility and the transactional lease claim remain mandatory, while a same-build Worker becomes eligible after `handoff_preference_window`. Runner-rotation successors have no build-preference delay and still require an exact Runtime lock match.
