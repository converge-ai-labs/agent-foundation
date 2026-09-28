# Environments: mounts and external lifecycle

## Design position

A thread chooses environments; a run uses the mount set frozen for it at acceptance. Several threads may share one instance, so lifecycle coordination is a property of the **instance and all its active users**, never of one thread or worker. Every external lifecycle call belongs to a durable, fenced operation, and an instance is never silently replaced: a lost instance refuses new use as `environment_lost` until a caller deletes it or mounts another in its place.

## Boundaries

| Concern                                                                | Owner                                                                                                            |
| ---------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Environment provider resources and templates as configuration          | [04: provider resources](04-resources.md#provider-resources), [templates](04-resources.md#environment-templates) |
| Registered environment types, capability flags and the endpoint policy | [08](08-providers.md#environment-providers)                                                                      |
| When a run freezes mounts, and how a failed preparation ends a run     | [05](05-runs.md#source-selection)                                                                                |
| Instances, thread mounts, operations, idle policy and execution mounts | This chapter                                                                                                     |

## Tables

```
environments   (env_)
  id  organization_id  workspace_id  provider_id NULL  provider_identity NULL  template_id NULL
  device_id NULL  endpoint NULL  token NULL  owner_principal_id NULL  name  status  handle NULL  generation
  operation_id NULL  operation_started_at NULL  operation_deadline NULL
  lease_owner NULL  lease_token_hash NULL  lease_expires_at NULL
  failure NULL  last_used_at NULL  renew_at NULL  expires_at NULL  created_by_id  version  created_at  updated_at
  status IN ('creating', 'starting', 'ready', 'stopping', 'stopped', 'deleting', 'deleted')
  CHECK (template_id IS NULL OR status NOT IN ('ready', 'starting', 'stopping', 'stopped')
         OR handle IS NOT NULL)
  CHECK ((status IN ('creating', 'starting', 'stopping', 'deleting')) = (operation_id IS NOT NULL))
  CHECK ((operation_id IS NULL) = (operation_started_at IS NULL))
  CHECK (operation_deadline IS NULL OR operation_id IS NOT NULL)
  CHECK (lease_owner, lease_token_hash, lease_expires_at all set or all NULL,
         and set only with operation_id)
  CHECK (template_id IS NOT NULL OR status IN ('ready', 'deleted'))
  CHECK ((template_id IS NULL) = (provider_id IS NULL))
  CHECK ((template_id IS NULL) = (device_id IS NOT NULL)
         AND (template_id IS NULL) = (endpoint IS NOT NULL)
         AND (template_id IS NOT NULL OR (provider_identity IS NULL AND handle IS NULL)))
  CHECK ((token IS NOT NULL) = (template_id IS NULL AND status <> 'deleted'))
  CHECK ((renew_at IS NULL AND expires_at IS NULL) OR (status = 'ready' AND template_id IS NOT NULL))
  UNIQUE (operation_id) WHERE operation_id IS NOT NULL
  INDEX (renew_at) WHERE renew_at IS NOT NULL

thread_environments
  thread_id  environment_id  organization_id  workspace_id  name  working_directory NULL  created_at
  PRIMARY KEY (thread_id, name)
  UNIQUE (thread_id, environment_id)
```

A **managed** instance has a template and its provider; an **external target** has neither, nor a handle or provider identity, and holds only the `device_id`, `endpoint` and `token` of the envd daemon it reaches ([external targets](#external-targets)). A target belongs to the principal that registered it (`owner_principal_id`); managed instances have no owner. The instance references its provider by `(workspace_id, provider_id)`, so the database refuses a provider of another workspace ([04](04-resources.md#provider-resources)), and identity columns never change.

- `handle` is `{recipe, state, credential_version}`: the recipe a managed instance was built from, the provider's portable state for reaching it, and the version of the provider credential that last reached it, which is its encryption envelope's nonce and changes whenever the credential is written.
- `provider_identity` is `{type, backend}`, the provider type and non-secret backend locator the handle is meaningful in. It is frozen when a managed instance's create operation is first claimed; NULL means the create was never claimed.
- `token` is a target's credential, encrypted for its own row and column; the tombstone drops it.
- `failure` is `{code, message, certainty, permanent, operation_id, at}`: the last error of the outstanding operation or, on a ready instance, of its last [renewal](#renewal), whose `operation_id` is NULL. `certainty` is `not_dispatched`, `known` or `unknown` (the call may have taken effect); `permanent` failures refuse new use until the cause is fixed or the instance is deleted. An unknown outcome is never permanent.
- `renew_at` is when the next [renewal](#renewal) of a ready sandbox whose type ends sandboxes unless renewed is due, and `expires_at` the expiry its provider last reported. Changes confined to them keep the version, so renewing leaves the ETag unchanged.
- The view shows everything except `handle`, `provider_identity`, `token`, `generation`, `operation_deadline`, `renew_at`, `expires_at` and the lease columns.

Mount names match `^[a-z][a-z0-9-]{0,62}$`; `workspace` is the primary mount. A `working_directory` is a canonical absolute path of at most 1024 characters, without empty, `.` or `..` segments. Inserting, updating or deleting a thread's mounts bumps the thread version.

## Templates and instances

Operator-selected Local and Docker defaults are prepared by [workspace provisioning](09-runtime.md#workspace-provisioning). A Docker recipe accepts `pull_policy: never | if_missing` (default `if_missing`). `never` uses only an image already in the configured Engine; a missing image fails with `environment_image_missing` and build/load guidance. Image acquisition policy is not part of the native container identity.

A template is live configuration ([04](04-resources.md#environment-templates)). Its effect on instances:

- **The recipe is frozen per instance.** The first claim of an instance's create operation validates the template's current recipe, materializes its effective provider defaults into `handle.recipe`, and freezes `provider_identity` before any external call. Later template edits and changes to provider defaults apply only to instances created afterwards; an existing instance never changes its image, resources or storage. Explicit template values remain explicit even when equal to the current default. A legacy Docker handle without `image` retains `ghcr.io/converge-ai-labs/a13n-docker-environment:dev`, including a create whose response was lost before portable state was saved; reconnect, retry, stop and delete never reinterpret that omission as the current release.
- **The idle policy is read live.** Maintenance applies each template's current `stop_after_seconds` and `delete_after_seconds` to all its instances ([idle policy](#idle-policy)).
- **Disabling refuses new instances.** A disabled template refuses reservation, and a reserved instance whose create was never claimed fails its first claim with the permanent failure `environment_template_disabled`. Instances already created keep working.

## Mounts

A thread's **desired mounts** are the environments later runs will use. `POST …/threads/{thread}/environments` adds one (`{name, environment_id, working_directory?}`) and `DELETE …/threads/{thread}/environments/{name}` removes one; both need `run` and the thread `If-Match`, answer with the new thread ETag, and are audited as `thread_environment.create` and `.delete`. Adding needs an open thread. Replacing a mount is explicit: remove the name, then add it again. A duplicate name is `already_exists` (kind `mount`); an environment already mounted on the thread is `conflict` (`already_mounted`). A thread holds at most 32 desired mounts: a path that would add more is `conflict` (`mount_limit`, details `limit`), and a request naming more than 32 is `invalid_argument`. Acceptance's primary reservation below may add one beyond them, and a `shared` child adopts all of its parent run's frozen mounts. `GET` lists the mounts with the thread ETag.

Every new use checks the instance under its row lock:

- an external target of another principal is `forbidden`;
- a `deleting` or `deleted` instance is `conflict` (`environment_{status}`);
- an instance with a permanent failure is `conflict` with the failure's code.

Mounts share-lock their instances in ID order: new use excludes a concurrent stop, delete or lifecycle step, never other new use. The same checks apply to every path that adds mounts:

- **New threads and forks** may name up to 32 initial `environments`, added in the transaction that accepts the first run. A fork also shares the origin thread's desired mounts unless `fresh_environments` is set, and its shared and named mounts together count against the 32; shared and named mounts are checked and locked in one ID-ordered pass, so an unusable shared mount refuses the fork (`forbidden` or `conflict`).
- **Child threads** take their mounts from the delegating edge ([05](05-runs.md#child-runs)): `shared` adopts the parent run's frozen mounts, `dedicated` reserves an instance from the edge's template, `none` mounts nothing.
- **Acceptance** freezes the thread's desired mounts into `runs.environment_mounts` ([05](05-runs.md#source-selection)), checking each instance for the run's principal. If the agent has a `default_environment_template_id` and the thread has no `workspace` mount, acceptance first reserves a managed instance from that template and mounts it as `workspace`. A child thread never uses its agent's default template.

While a run is accepted or running, its frozen `environment_mounts` is the durable active-use evidence, across worker loss, handoff and backoff; a GIN index answers "which active runs use this instance". Removing a desired mount never hides an active run's use.

**Reservation** creates a managed instance in `creating` with its first operation, from an enabled template of an enabled provider the principal may `run`. A workspace holds at most `environments.managed_count` managed instances that are not deleted; reservations are serialized by the workspace's reservation lock, and one beyond the limit, whether explicit, at acceptance or for a dedicated child edge, is `conflict` (`environment_limit`, details `limit`). Only reservations take that lock, and no transaction that locks an instance exclusively reserves, so acceptance may reserve before or after share-locking the instances it mounts ([05](05-runs.md#lock-discipline)). Nothing external exists until maintenance or a waiting attempt dispatches the create. `POST …/environments {template_id, name?}` reserves one directly (`run`; 201; audited as `environment.create`); the name defaults to the template's name.

## One outstanding external operation

The status names the instance's outstanding operation: `creating`, `starting`, `stopping` or `deleting`. No other status carries one, and an error stays on the phase with the same operation.

1. **Begin.** Under the environment row lock, set the phase, increment `generation`, allocate a new `envoper_` `operation_id`, clear any claim and failure, and commit. A different operation replaces an outstanding one only when the outstanding one is **settled**: nobody holds or silently lost its claim, and its last call did not end with an unknown outcome.
2. **Claim.** A dispatcher locks the row, finds the claim free or expired, records a fresh token, a call deadline of `environments.operation_seconds` and a claim that outlives the deadline by 10 seconds, and commits. The claim first refuses, as permanent failures without dispatching: a provider whose backend identity cannot be resolved (`environment_provider_unavailable`), a disabled provider for `creating` or `starting` (`environment_provider_disabled`), a disabled template at the create's first claim (`environment_template_disabled`), a template whose provider changed since the reservation, also at that claim (`environment_template_moved`), and a provider that now points at another account or endpoint (`provider_identity_changed`). Stop and delete still proceed on a disabled provider.
3. **Perform.** With no database session held, one bounded call: `creating` prepares the instance; `starting` reconciles it, fails with the permanent, known `environment_lost` if it no longer exists (or with `provider_credential_changed`, which is not permanent, when the provider's credential changed since it last reached the instance, as for [renewal](#renewal)), and otherwise prepares it; `stopping` stops it; `deleting` destroys it.
4. **Publish.** Lock the row and record the outcome only if generation, operation ID and claim token still match; a superseded dispatcher changes nothing. Success clears the operation and failure, reaches `ready`, `stopped` or `deleted`, stores the new handle state and the version of the credential that reached it (a deleted instance keeps no handle), sets `last_used_at` on `ready` and schedules its first renewal when its type requires one, and audits `environment.{ready | stopped | deleted}` with no actor. A failure clears the claim, keeps any state the provider reported, and records the failure on the same phase. A failure before dispatch after an earlier unresolved dispatch is recorded as unknown. A dispatcher interrupted mid-call publishes the unknown failure `environment_operation_interrupted`.

An expired claim lets the next dispatcher continue the **same** operation. The Harness lifecycle calls reconcile the instance they are bound to, acting as its owner, so they find it by the environment ID even when no state was recorded: preparation looks the instance up before creating one, and stop and destroy observe its actual state and never create. So continuing never issues conflicting work, and a timeout never mints a new operation ID.

| Status     | Next action                                                                                     |
| ---------- | ----------------------------------------------------------------------------------------------- |
| `creating` | Prepare the instance from the frozen recipe; a continued create finds the instance first made   |
| `ready`    | Renewed before it ends when its type requires it; opening a client is not a lifecycle operation |
| `stopping` | Continue the same stop until it is known                                                        |
| `stopped`  | A waiting attempt begins `starting`                                                             |
| `starting` | Resume the same handle; a lost instance fails permanently and is never recreated                |
| `deleting` | Continue destruction; new mounts and starts are refused                                         |
| `deleted`  | Terminal tombstone; the ID is never reused                                                      |

**Maintenance.** The `maintain_environments` sweep ([09](09-runtime.md#sweeps)) runs every `environments.scan_seconds`. Each pass begins idle deletes, then idle stops, then dispatches, concurrently, up to `environments.batch` outstanding operations whose claim is free or expired, oldest change first. A failed operation is revisited at the fixed interval with the same operation ID; there is no per-instance backoff. A dispatch that fails unexpectedly is logged and retried by a later pass. A pass is bounded by twice `environments.operation_seconds` plus 30 seconds. Renewals have their own sweep, so a slow lifecycle call never delays one ([renewal](#renewal)).

### Idle policy

Idle time counts from `last_used_at`, or `created_at` when the instance was never used. `last_used_at` is set when an instance becomes ready, when an attempt finds it ready and when an attempt stops using it. Instances in `creating` are not subject to the policy.

- **Idle stop.** A ready managed instance whose type supports stop, idle past its template's `stop_after_seconds`, not used by an active run and without a permanent failure begins `stopping`. Stopping would clear a failure, such as `environment_lost`, that refuses its use.
- **Idle delete.** A ready or stopped managed instance whose type supports destroy, idle past `delete_after_seconds`, mounted by no thread and used by no active run begins `deleting`.

Both recheck active use and mounts with fresh statements under the row lock, and audit `environment.stop` or `environment.delete` with no actor and reason `idle`.

### Renewal

Some hosted types end a running sandbox at a deadline unless it is renewed; their definition sets `requires_keepalive` ([08](08-providers.md#environment-providers)). The `renew_environments` sweep ([09](09-runtime.md#sweeps)) keeps a ready instance of such a type alive for as long as it stays ready, so the template's idle policy, not the vendor's timeout, decides when it stops. Every `environments.scan_seconds` it renews, concurrently, up to `environments.batch` instances whose `renew_at` has come, earliest first; a pass is bounded by `environments.renewal_seconds` plus 40 seconds.

- **Schedule.** Becoming `ready` makes a renewal due at once. Each renewal asks the provider, within `environments.renewal_seconds` and with no database session held, to keep the sandbox for at least the adapter's keepalive horizon, records the expiry the provider reports in `expires_at`, and falls due again halfway to it. The horizon is at least 300 seconds, which the Service's E2B timeout floor keeps ([08](08-providers.md#registry)), and settings keep `environments.scan_seconds` plus twice `environments.renewal_seconds` plus the 10-second publication margin below half of it ([09](09-runtime.md#operational-limits)): a due renewal that a pass just missed finishes before the expiry even when that pass takes its full bound. Beginning any operation clears `renew_at` and `expires_at`, so a stopping, stopped or deleted instance is never renewed; nor is an external target or an instance of another type.
- **Claim.** A renewal only extends a deadline; it never creates, starts or stops a sandbox, and its adapter does not act as the owner. So the claim is the lightest one that excludes a second dispatcher: under the row lock, a due renewal moves `renew_at` past its call deadline plus the publication margin. Its outcome is recorded only while `renew_at` still holds that claim, which an operation that began or a later claim replaces. An interrupted renewal records nothing; its claim expires and a later pass renews. A provider whose backend identity changed or cannot be resolved is not called until it is restored, as for runs.
- **Outcome.** A success clears `failure` and records in `handle.credential_version` the credential that reached the sandbox. A provider that no longer has the sandbox makes the instance permanently `environment_lost` (certainty `known`) and ends renewal, unless the provider's credential changed since it last reached the sandbox: the new credential may belong to another account that cannot see it, so that failure is `provider_credential_changed`, which is not permanent, and renewal goes on. A provider that reports the sandbox stopped, at its own time limit say, leaves the instance `stopped`, clears the failure and audits `environment.stopped` with reason `provider`; the next run starts it as after any stop. One that cannot keep the sandbox that long, near a vendor's hard lifetime (`provider_keepalive_limit`), is expected: nothing is recorded, and the next renewal waits for the reported expiry, when the sandbox is found gone or stopped. Any other failure is recorded, permanent by its category as for [operations](#provider-contract), so an unfixable one refuses new use; a repeated failure with the same code keeps its first record.
- **Retry.** A failure that is not permanent is retried halfway to the reported expiry while one remains. Otherwise the retry waits as long as the same failure has lasted, at most ten minutes.

## Stop, start and delete

Stop and delete arbitrate with use under the environment row lock and only begin an operation; provider calls happen in the fenced lifecycle, never in the request. Acceptance share-locks the same row before installing new active use, so whichever commits first wins: a stop that wins is observed by the run, which starts the instance again; an acceptance that wins makes the stop decline.

- `POST …/environments/{id}/stop` (`write`, `If-Match`, 202) needs a ready managed instance without a permanent failure, whose type supports stop, used by no active run. Otherwise it is `conflict` with `connect_only`, `environment_{status}`, the permanent failure's code (stopping would clear it), `stop_unsupported` or `in_use`.
- `DELETE …/environments/{id}` (`write`, `If-Match`, 202) returns a `deleting` or `deleted` instance unchanged. It refuses an instance a thread mounts (`mounted`), an active run uses (`in_use`) or whose outstanding operation is not settled (`operation_unresolved`). An instance with a permanent failure, such as a lost one, is not refused for its mounts: it leaves the threads that mount it, which it locks first in ID order as the lock order requires, so their next run can use another; a thread that loses its `workspace` mount gets a new primary sandbox at its next acceptance when its agent has a default template. An external target, or a reservation whose create was never dispatched, becomes `deleted` at once without a provider call. A managed instance whose type cannot destroy is `conflict` (`destroy_unsupported`); any other begins `deleting`.
- `PATCH …/environments/{id}` (`write`, `If-Match`) renames an instance and points an external target at a new endpoint or token ([external targets](#external-targets)).
- Starting is never a request: a waiting attempt begins it ([execution](#execution)).

An external target is managed by its owner, or by a workspace administrator; anyone else is `forbidden`. Stop, delete and update are audited as `environment.stop`, `.delete` and `.update`. `GET …/environments` lists a workspace's instances; `status` filters by one status, and without it tombstones are left out.

Archiving a thread removes its desired mounts ([05](05-runs.md#waiting-interrupt-and-fork)); an active run's frozen use delays deletion until the run seals.

## Execution

Before the Harness run, an attempt prepares every frozen mount, waiting at most `environments.wait_seconds` in all. Each check is a short transaction that proves the attempt's lease and locks the instance:

- The instance must still be usable by the run's principal (the checks in [mounts](#mounts)), and a managed instance's provider must resolve under the run's authority. For an external target the check then sets `last_used_at` and returns it.
- A `ready` instance whose provider identity changed is refused as `provider_identity_changed` without recording a failure, so restoring the provider heals it; otherwise the check sets `last_used_at` and returns the target.
- A `stopped` instance begins `starting`.
- If the outstanding operation has no failure and is not `stopping`, the attempt dispatches it once itself; after a failed call it only waits, leaving retries to maintenance.

A mount that can no longer be used fails the run with `environment_unavailable`. A wait that runs out is `unavailable` (reason `environment_not_ready`): the attempt fails and the run recovers within `max_attempts` ([05](05-runs.md#failure-semantics)). Cancelling the attempt stops the wait at once. A drain ends it too: the attempt yields as a handoff and is not charged ([05](05-runs.md#execute)).

The attempt then builds a fresh adapter per mount that connects to the ready instance and never creates, starts or replaces one. `workspace` appears at `/workspace` and is the default environment; other mounts appear at `/mnt/{name}`; `working_directory` is the mount's default directory and route root. The Harness enters and closes the adapters it binds; when the run ends, the rest are closed and the instances are marked used. Closing an adapter releases client resources only; the instance keeps running.

## External targets

An external target is an envd daemon someone else operates, reached over HTTP(S) at its own endpoint with its own token. It is no provider resource: the Harness `http_envd` adapter reaches it, built by `providers/envd.py` from the row's values. Each executing worker connects directly to the endpoint, which must be reachable from every process that uses it.

`POST …/environments {endpoint, token, name?}` registers one (`write`; 201; audited as `environment.register`). The endpoint is an HTTP(S) origin of at most 2048 characters, plain HTTP only for a loopback address, and the token at most 4096 characters; a malformed one is `invalid_argument` on the field. Outside any transaction and within `providers.operation_seconds`, the Service checks the endpoint against the [endpoint policy](08-providers.md#outbound-endpoint-policy), asks the daemon which device it is, then opens and closes one session expecting that device. A refusal that repeating cannot overcome (an invalid, unsupported, missing, denied or conflicting outcome, such as `provider_endpoint_denied`) is `conflict` with details `{field: "endpoint", reason}`, the provider's error code as reason; any other failure is `unavailable` (dependency `environment:http_envd`) with that code, or `environment_timeout` for a timeout, as reason. A daemon refusing the token cannot be told from one that cannot be reached, so it is `unavailable` with `provider_connection_failed`. It then records a `ready` target owned by the caller with only the daemon's `device_id`, the endpoint and the token, encrypted for the row; the name defaults to the `device_id`. The view shows the endpoint and `device_id`, never the token.

Every later connection, whether a run's or a verification's, builds its adapter from the row's endpoint, device and revealed token, checks the endpoint policy before dialing and expects the stored device. A daemon that is another device refuses to initialize, which the adapter reports as the permanent `provider_device_mismatch`. `PATCH …/environments/{id}` with a `token`, and a new `endpoint` if it moves (a new endpoint always comes with its token), repeats the verification with the stored device outside any transaction, then stores both, and a new name if one is given, under the same `If-Match` precondition. A daemon that is another device is `conflict` (`provider_device_mismatch`); any other refusal is `conflict` on the environment or `unavailable`, as for registration. A token without a new endpoint replaces the token only. An endpoint without a token is `invalid_argument` on `token`; for a managed instance, a new endpoint is `invalid_argument` on `endpoint` and a token alone on `token`; for a deleted target either is `conflict` (`environment_deleted`).

The Service never creates, starts, stops or destroys a target, and maintenance never touches it. `ready` means selectable, not currently reachable: an unreachable target fails preparation of the runs that mount it. Deleting a target only removes the Service's access; the tombstone keeps the `device_id` and endpoint and drops the token. A timeout or broken connection after dispatch does not authorize replaying an unknown command; the envd session and operation-identity checks of the Harness client apply.

## Provider contract

The Harness environment definition is the contract ([08](08-providers.md#environment-providers)). It builds a single-use adapter from plain values: the provider configuration and credential, the instance's recipe and portable state, the operation ID, and whether this call acts as the instance's owner and so may create it. Lifecycle operations act as the owner and use one adapter per call and close it; execution never acts as the owner and passes adapters to the Harness. The definition declares `supports_stop`, `supports_destroy` and `requires_keepalive`; every registered type backs templates. What the Service's Docker and E2B types let a recipe and an account do is [08](08-providers.md#registry)'s.

A provider error carries a category and a certainty. Invalid, unsupported, missing, denied and conflicting outcomes are permanent unless their outcome is unknown; transport errors, timeouts and unknown outcomes are retried on the same operation.

## Failure semantics

| Situation                                                                                       | Observable outcome                                                       | Recovery                                          |
| ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ | ------------------------------------------------- |
| Provider call times out or fails transiently                                                    | `failure` on the same phase, certainty `unknown` when it was dispatched  | Maintenance continues the same operation          |
| Dispatcher dies mid-call                                                                        | Claim expires; `environment_operation_interrupted` when it could publish | The next dispatcher reconciles the same operation |
| Permanent refusal (disabled provider or template, identity changed, invalid recipe)             | Permanent `failure`; new mounts and acceptance refused with its code     | Fix the cause, or delete the instance             |
| Template moved to another provider before the create was claimed (`environment_template_moved`) | Permanent `failure`                                                      | Reserve a new environment                         |
| Instance lost while stopped                                                                     | `starting` fails permanently with `environment_lost`                     | Delete it or mount another; no silent replacement |
| Provider no longer has a ready sandbox that it should keep                                      | The renewal records the permanent `environment_lost`; renewal ends       | Delete it or mount another; no silent replacement |
| Provider's changed credential no longer sees a ready sandbox                                    | `provider_credential_changed`, not permanent; renewal goes on            | Restore a credential of the sandbox's account     |
| Provider stopped a ready sandbox itself                                                         | The renewal leaves the instance `stopped`                                | The next run starts it                            |
| Provider cannot keep a ready sandbox any longer (`provider_keepalive_limit`)                    | Nothing recorded; the next renewal waits for the expiry                  | The renewal then finds it gone or stopped         |
| Renewal fails otherwise                                                                         | A failure on the ready instance, permanent by its category               | Retried with backoff; a success clears it         |
| Mount unusable during preparation                                                               | The run fails with `environment_unavailable`                             | None for that run                                 |
| Instance not ready within `environments.wait_seconds`                                           | The attempt fails; the run recovers                                      | Within `max_attempts`                             |
| Delete requested during an unresolved operation                                                 | `conflict` (`operation_unresolved`)                                      | Retry once the operation settles                  |

## Trade-offs

- **Shared environments share mutable files.** Threads, forks and children that mount one instance see each other's changes and can run tools concurrently. Checkpoints neither snapshot nor roll back files; a caller that needs isolation mounts fresh environments.
- **Frozen recipes.** A template fix does not reach existing instances; replacing an instance is explicit.
- **Fixed-interval recovery.** A failed operation waits for the next maintenance pass instead of a per-instance schedule, in exchange for one simple rule: an operation is only ever continued, never duplicated.

## Invariants

- An instance has at most one outstanding operation, and a completion is recorded only by the dispatcher whose generation, operation ID and claim token still match.
- A different operation begins only after the previous one is settled.
- Stop and delete never begin while an accepted or running run's frozen mounts name the instance; delete also requires that no thread mounts it, except that deleting an instance a permanent failure makes unusable removes those mounts in the same transaction.
- Opening an instance for execution never creates, starts or replaces it, and neither does a renewal.
- Only a ready instance is renewed, and a renewal's outcome is recorded only while its claim still holds.
- A managed instance's recipe and provider identity never change after its create is first claimed.
- The Service never calls a lifecycle operation on an external target, and every connection to one expects its registered device.
