---
name: coordinate-parallel
description: Run independent investigations or changes across multiple workers. Isolate concurrent writes and verify the integrated result.
---

# Coordinate Parallel Work

Use this workflow when multiple tracks can produce independently useful results and concurrent execution saves more time than coordination and integration cost.

## Decide What Can Run Together

Name what each track can deliver on its own and what would still be usable if another track stopped. Keep work that is still defining a shared interface, migration, or release order on one mainline, or settle that dependency before splitting it.

Read-only tracks can share stable inputs. Treat validation that writes generated files, caches, or shared fixture state as writing for isolation purposes.

## Assign Ownership Before Launch

Give concurrent writers in the same repository separate, accessible worktrees and branches or equivalent isolated checkouts. A worker Thread does not provide checkout isolation. Isolate shared test resources such as databases and ports, or schedule those checks serially. If isolation is unavailable, serialize writes and parallelize only read-only tracks.

Give each worker a short brief:

- Track name, objective, base revision, and assigned checkout or stable input.
- Owned write scope, boundaries and interfaces to preserve, and any authorized publication.
- Acceptance checks and handoff evidence: findings with references, or a commit or patch with validation results.

Assign a single integration owner. If a worker needs to cross its boundary, have it report the dependency before editing outside its scope.

## Reconcile Reports and Notices

Keep one compact roster with each track's worker, checkout and base, required or optional status, last verified result, and next action. On a report, Host notice, or user update, inspect relevant saved outcomes and reconcile once. A report and a later notice can refer to the same operation. When only waiting remains, record the next check and end the turn without promising a wake-up.

If a steer is rejected while a worker is settling, it will not automatically start a new Run. Use `get_thread` once to check what landed and whether the worker is available before resending.

When the user starts work directly, reconcile the admitted task instead of redispatching it. If the user changes a track's scope or base, review the impact before assigning more work. For failed, cancelled, or suspended tracks, inspect saved results and actual side effects before retrying or reassigning. The Coordinator has no force-cancel tool for workers; if a worker must stop, direct the user to that worker's stop control and verify the outcome before reassigning its checkout.

## Integrate Once

Integrate verified outputs in dependency order. Keep one writer on the integration checkout and assign any cross-track conflict to a named owner. Check contracts and generated artifacts even when Git reports no textual conflict. Run cross-track validation on the combined revision.

Report the integrated result, retained independent work, and any missing tracks or failed checks. Make clear whether a missing track blocks the requested outcome.
