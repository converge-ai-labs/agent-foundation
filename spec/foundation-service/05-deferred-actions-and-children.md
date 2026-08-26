# Deferred Actions and Asynchronous Children

## Design Position

Foundation turns Harness suspension and Host-managed child work into durable lifecycles without extending a worker process across human or external waits. Deferred approvals, external client tools, and structured user input are durable pending actions represented by Items in the current Turn. An asynchronous subagent receives an independent child Thread and initial Turn with its own Agent instance, worker leases, checkpoints, cancellation, and result-delivery state.

The Harness continues to own native Pydantic deferred values and blocking inline delegation. Foundation does not encode pending authority in `HarnessState` or reinterpret an asynchronous child as an unfinished Pydantic tool call.

## Pending Actions

Pending action kinds remain semantically distinct:

- an `approval` asks an authorized human or service to permit a proposed action;
- a `client_tool` asks an external client to perform a named effect and return its native result;
- `user_input` requests structured information without authorizing another effect.

A conceptual pending record contains:

```python
class PendingAction:
    id: PendingActionId
    turn_id: TurnId
    lease_generation: int
    thread_version: int
    request_item_id: ItemId
    kind: PendingActionKind
    native_request_envelope: NativeDeferredRequestEnvelope
    expected_responder: ResponderPolicy
    status: PendingActionStatus
    expires_at: datetime | None
```

The native request envelope preserves the exact complete deferred request returned by the Harness and the identity of the suspended message/tool surface. It carries no credential or ambient authority. Foundation separately records who can inspect, approve, execute, reject, or answer the action.

## Suspension and Resume

```mermaid
sequenceDiagram
    participant Harness
    participant Worker
    participant DB as Durable store
    participant Client
    participant NextWorker as Replacement worker

    Harness-->>Worker: suspended result, complete HarnessState, and native requests
    Worker->>DB: fenced checkpoint, request Items, pending actions, Turn waiting
    Worker-->>Worker: release run resources and lease
    Client->>DB: authenticated idempotent feedback
    DB->>DB: validate exact pending identity and close action
    DB->>DB: move Turn to queued
    NextWorker->>DB: claim next worker lease generation
    NextWorker->>Harness: fresh bindings, selected state, and exact DeferredToolResume
```

Suspension commits the complete checkpoint, exact pending requests, corresponding request Items, Turn wait reason, lifecycle event, and outbox entry as one generation-fenced logical transition. The worker then closes Harness and Environment resources. No lease, credential, provider client, socket, or database session remains alive while waiting.

Feedback authenticates the responder, authorizes the specific pending action, checks expiration and Thread version, and applies an idempotency key. Accepted feedback creates or completes the corresponding response Item in the same Turn. The same feedback identity and canonical content return the original receipt; different content conflicts. Closing every required pending action makes the Turn eligible for another worker lease generation.

The replacement worker reconstructs the exact Agent revision and tool surface, supplies fresh bindings, and passes the authoritative prior request together with complete results through native `DeferredToolResume`. Feedback is supplied once on the first inner Harness attempt and is not copied into generic metadata or reconstructed from message text.

Approval and external execution remain separate. Approval does not claim that the client effect occurred; client success does not retroactively prove an approval decision. Rejection and expiry produce their owning native deferred result or a bounded durable failure without inventing a tool outcome.

## Asynchronous Child Threads

```mermaid
flowchart LR
    Parent[Parent Turn] --> Spawn[Authorized child submission]
    Spawn --> ParentItem[Parent child_agent Item]
    Spawn --> ChildThread[Child Thread and Agent instance]
    ChildThread --> Child[Initial child Turn]
    Child --> Runs[Worker leases, Harness runs, and checkpoints]
    Child --> Frozen[Terminal child result]
    Frozen --> Delivery[Parent delivery ledger]
    Delivery --> ParentInput[Later parent-selected semantic input]
```

A Host-managed asynchronous spawn is an ordinary completed parent tool operation. Its parent `child_agent` Item names the accepted child Thread and initial Turn. It is not a deferred tool request, and child completion never fills the original spawn tool-call ID.

Child acceptance verifies the current parent Turn and worker lease generation, authorizes the exact declared child definition, intersects delegation and run grants, selects the child Agent revision, creates a stable child Agent instance and Thread, and records the parent-child relationship. The child then follows the ordinary Thread and Turn lifecycle independently.

The parent-child record states cancellation propagation, result visibility, retention, and delivery policy. A parent may continue, wait for a child, or terminate according to that explicit policy. Parent termination does not silently cancel a child whose contract declares independent continuation.

## Result Delivery

Child Turn outcome and parent delivery advance independently. A terminal child result is immutable after its fenced commit. A delivery ledger records whether that exact child outcome is available, offered to the parent lineage, selected into a parent checkpoint, expired, or discarded under policy.

Repeated notifications reuse one child and result identity. If the parent Turn is waiting for the child, accepted delivery creates the corresponding result Item and makes that same Turn eligible to resume. If the parent Turn already completed independently, the ledger retains the outcome for an explicitly selected later Turn rather than reopening terminal state. The parent incorporates the result at most once into a selected checkpoint under Thread version and worker-generation fencing. A lost notification can be regenerated from the durable ledger; a delivered notification does not by itself prove that the parent model observed or checkpointed the result.

Result content is bounded and authorized at read and incorporation time. The parent receives a typed projection rather than the child's credentials, live Environment binding, private Capability state, or complete internal trace.

## Cancellation and Failure

| Condition                                      | Outcome                                                                         |
| ---------------------------------------------- | ------------------------------------------------------------------------------- |
| Worker lost after pending commit               | Turn remains waiting; no worker ownership is required                           |
| Feedback duplicated                            | Original feedback receipt is returned                                           |
| Feedback targets stale or closed action        | Request conflicts without claiming another worker lease                         |
| Feedback responder unauthorized                | Request is denied without disclosing private pending content                    |
| Resume surface differs from suspended surface  | Turn fails before Harness resume                                                |
| Child submission acknowledgement lost          | Parent retries with the same operation identity and receives the existing child |
| Child fails                                    | Terminal child failure is frozen and delivered according to parent policy       |
| Parent lost after child creation               | Child remains durable; delivery ledger preserves the relationship               |
| Parent incorporates result then commit is lost | Selected parent checkpoint determines whether incorporation occurred            |

## Invariants

1. Waiting for approval, client tools, user input, or child completion holds no worker lease.
2. Pending requests and feedback remain native typed values associated with one exact suspended checkpoint.
3. Every continuation claims a new worker lease generation and starts a fresh Harness run for the same Turn.
4. Approval and external client effect are independent facts.
5. An asynchronous child has an independent Thread and initial Turn; it is not inline Harness delegation or a deferred spawn call.
6. Child acceptance, completion, notification, parent incorporation, and parent checkpoint selection are separate facts.
7. One terminal child result is incorporated into a parent lineage at most once.
8. Parent or child cancellation follows explicit durable policy and never implies rollback of effects.
