# Connector events

## Ownership

Connector events are built-in Service input. Control receives signed provider callbacks, commits their source facts and submits accepted input through ordinary Thread/Inbox admission. Worker executes the resulting Runs. A configured Connector Provider and authorized Connection supply the external account; no installed App is required.

The optional Harness Connector event adapter owns dynamic event discovery, remote subscription operations, signature verification and normalized values. Service owns scope, encrypted signing credentials, subscription configuration, persistence, retries and execution authority. The existing lifecycle webhook outbox continues to send Service events outward.

## Configuration and identity

The existing workspace-owned `connector_providers` row stores nullable `event_webhook_id`, encrypted `event_secret`, `event_config` and `event_operation`. A single Composio project webhook serves all that Provider's subscriptions. Its URL is `{connector_events.public_url}/api/v1/connector-webhooks/{provider_id}`; `public_url` is an operator-configured HTTPS base. The Provider ID selects verification configuration and confers no authority.

The authenticated upstream webhook ID and exact callback URL establish project binding. A unique webhook ID prevents two local Providers from binding the same remote callback. Setup refuses an existing different URL; it does not overwrite another consumer. Changing the API credential or configuration after setup requires workspace `admin` and a successful read of the same remote webhook identity through the proposed credential before the update commits. Switching projects requires another Provider. Ordinary Provider updates preserve event configuration, and outstanding event operations block them. A Provider with event configuration cannot be physically deleted.

The signing key is encrypted for its Organization, Provider ID and `event_secret` column, separately from the API credential. Management responses expose URL, remote ID, key version, operation and safe failure only. Setup and rotation require workspace `admin` and the Provider ETag. Remote I/O occurs between short transactions, with an operation ID fencing publication. After an unknown rotation outcome, recovery reads the current remote key instead of rotating again. Attempts stop at `connector_events.max_attempts`; a subsequent setup command can explicitly resume exhausted recovery after a reported failure or the last attempt deadline. An active last attempt cannot be replaced. New claims clear old failures; an expired last attempt becomes a visible failure even if the process stopped before reporting one.

## Event directory and subscriptions

Event types and their parameter/payload schemas come from the Provider API. Directory reads require `run`, use bounded HTTP and pagination, and cache by Provider identity/version, app and event type for `providers.discovery_ttl`. Failed discovery is not cached as an empty directory. Creation reads a fresh definition, validates bounded local JSON Schema references and pins the returned provider version. Source parameters are limited to 64 KiB.

`connector_triggers` (`ctrig_`) owns the selected Provider, Connection, exact connected account and user identity, event type, parameters, definition/version snapshot, target, remote instance ID and operation state. Composite foreign keys preserve tenant scope. `(provider_id, remote_id)` is unique; active or paused subscriptions also uniquely bind `(provider_id, account_id, event_type, config_digest)`. Creation is idempotent per workspace, creator and request key; a reused key with a different body conflicts.

A target is `store_only` or `new_thread`. A new Thread target selects a service account, Agent, exact Agent revision and bounded instructions. Saving a target requires workspace `admin`, current service-account `run` authority and a usable Agent revision; an omitted revision resolves at save time. Store-only creation requires `write` and use of the Connection. Readers need workspace `read`.

Names and targets can be edited with `If-Match`; changes apply to subsequently accepted events. The external account, event type and source parameters identify one remote subscription: changing them requires retiring it and creating a replacement. This preserves attribution for callbacks and history. Stored events do not become executable when a target changes.

States are `enabled`, `paused`, `retired`. Pause and retirement commit local intent immediately, including during remote creation; the original operation resolves its identity and then applies the desired remote state. Retirement is final. Enable revalidates the target and exact account. Maintenance pauses subscriptions whose Connection is disabled, revoked or rebound. Remote failures retain safe status and bounded retries. `reconcile` resumes retry-safe operations after an explicit failure or an expired attempt, preserving their phase; active attempts cannot be replaced. Exhausted interrupted attempts become visible failures even without a captured exception. Each new claim clears the previous failure and fences late results. The public `retryable` flag follows these same rules.

Creation first checks the exact account, type and configuration. Every precheck, including retries, refuses a pre-existing instance. Before sending the creation request, a short transaction persists `create_unconfirmed`. Only the acknowledged response proves the created remote identity. A typed adapter error with `outcome_unknown=false` from the creation call restores the precheck phase: retryable rejections (including HTTP 429) back off automatically, respecting `Retry-After`; non-retryable rejections stop automatic attempts but remain manually recoverable after their cause is fixed. Generic exceptions, interrupted requests and adapter errors with `outcome_unknown=true` retain the uncertain phase. A later publication failure never turns an acknowledged creation into a rejected request. If the response is lost, this phase ends with `subscription_creation_unconfirmed`: configuration equality alone does not prove ownership, so Service neither adopts, enables, deletes nor automatically recreates the uncertain instance. An operator must resolve the upstream subscription and retire the local record before creating a replacement. Retirement of an unowned record never modifies the remote instance.

## Durable receipt

Only the exact callback POST bypasses ordinary Service bearer authentication. Its adapter authenticates raw bytes before parsing; Composio V3 uses HMAC-SHA256 over `webhook-id.webhook-timestamp.body` with the literal project signing secret, accepts only `v1` signatures and a 300-second past/future window, and requires the body ID to match the signed header. Source timestamps must have timezones. The receiver enforces a 256 KiB default body bound before allocation and a 600/minute best-effort provider/peer rate limit.

Within a short transaction, receipt rechecks the Provider version and workspace state, enforces backlog limits and matches the exact remote instance, connected account, user and event type. Tenant and execution identities come exclusively from local configuration. HTTP 204 follows the committed event; database failure returns an error and capacity exhaustion returns 429 for upstream retry.

`connector_events` (`cevt_`) stores normalized source identity/time/payload, a stable content digest, optional Trigger and immutable target snapshot, delivery state, attempts/due time and Inbox/Thread receipt. `(provider_id, source_id)` is unique. An identical redelivery succeeds; reuse with different normalized content conflicts and is logged. Delivery attempts and signature timestamps do not enter the digest. Database guards prohibit overwriting source facts, an associated snapshot or a committed receipt; retention can clear payload bytes.

| Initial condition                                  | Event state |
| -------------------------------------------------- | ----------- |
| Enabled, store-only target                         | `stored`    |
| Enabled, new-Thread target                         | `pending`   |
| Paused or retired subscription                     | `ignored`   |
| Valid signature, incompatible saved payload schema | `failed`    |
| Remote identity not yet published locally          | `unmatched` |

Unmatched events are bounded separately and visible only to organization administrators. Maintenance associates them after remote creation publishes the exact identity. Events received before that publication use the original provisioning target. The first local pause or retirement during creation saves `provisioning_stopped_at` in the same transaction as the state change, without waiting for remote acknowledgement. Earlier events continue; events received at or after this cutoff become `ignored`, even when association happens after re-enabling. The cutoff is retained. Receipt timestamps use the database clock at acceptance, and receipt and remote-identity publication serialize under the existing Provider receipt lock so unmatched events precede publication. Unrelated identities become visible failures after `unmatched_seconds`. No unmatched payload is sent to an Agent.

## Inbox handoff

For each due pending event, a short transaction obtains the existing native submission authority locks, locks the Event and confirms it is still pending/due. It rechecks the service account, workspace, Connection and exact account and submits the saved target through native Thread/Inbox admission with `connector-event:{event_id}` as the idempotency key. Inbox creation and the Event's `submitted` state and receipt commit together. Failed admission rolls back the new Thread too.

Dispatch does not read or lock the Trigger and does not compare its current version or state. Accepted pending events continue after target edits, pause or retirement using their saved snapshot. The snapshot does not grant current execution authority. App submissions retain their own provenance and ownership checks; Connector input creates ordinary Threads without App provenance.

A competing control sees the committed receipt and skips submission. Failure rolls back, then a separate conditional update changes only the still-pending Event version observed by that attempt. Temporary capacity or dependency errors retry with bounded backoff; authorization failures become visible failures. Administrator retry requires a failed unsubmitted event with its retained payload and valid saved target; it reuses the original identity/snapshot. Payload schema failures cannot be retried into execution. `submitted` confirms Inbox handoff; Inbox and Run reads own execution status.

## Runtime, storage and compatibility

Control/all runs two bounded sweeps: event association/delivery/retention and remote subscription/setup recovery. Database work does not wait on HTTP, Redis or models. Redis only accelerates directory discovery and existing execution wakeups. Structured logs carry Provider/Trigger/Event/Thread IDs and safe reasons; metrics count accepted, duplicate, rejected and delivery outcomes and receipt-to-Inbox latency, without identity labels.

The migration adds two Core tables, their tenant/deduplication/due/retention constraints and indexes, and nullable Provider columns. Existing Providers retain normal tool behavior; no old Connection becomes a subscription automatically. No new object store or queue is introduced. Finished payloads expire after `payload_days` (default 7), while source digests and receipts remain for `retention_days` (default 30). Pending/unmatched work is excluded from ordinary retention; retries or unmatched deadlines produce visible terminal failures first. Inbox message retention remains independently owned by Threads.

Upgrade all callback-serving controls before enabling callbacks; old controls lack this route and its sweeps. Rollback stops ingress and dispatch and explicitly resolves pending work while retaining the new tables for forward repair. Dropping the tables would discard subscription identity and deduplication history.

## HTTP surface

All management paths use the ordinary workspace selection, cursor and error contracts. List/detail GET operations are explicitly admitted to management MCP. Mutations use HTTP; no model-facing platform write tools are added.

| Path (under `/api/v1`)                                      | Operation                                                                                                  |
| ----------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `/connector-providers/{id}/apps/{app}/event-types[/{type}]` | GET dynamic directory/detail; `run`                                                                        |
| `/connector-providers/{id}/event-webhook`                   | GET safe status; POST setup, exhausted recovery or `?rotate=true`; admin mutation with Provider `If-Match` |
| `/connector-triggers`                                       | GET paginated history filtered by Connection; POST with `Idempotency-Key`                                  |
| `/connector-triggers/{id}`                                  | GET, PATCH name/target with `If-Match`                                                                     |
| `/connector-triggers/{id}/{enable,pause,retire,reconcile}`  | POST lifecycle commands with `If-Match`                                                                    |
| `/connector-events[/{id}]`                                  | GET history/detail, optionally filtered by Trigger                                                         |
| `/connector-events/{id}/retry`                              | POST admin retry with `If-Match`                                                                           |
| `/connector-webhooks/{provider_id}`                         | POST signature-authenticated callback; 204 after persistence                                               |
