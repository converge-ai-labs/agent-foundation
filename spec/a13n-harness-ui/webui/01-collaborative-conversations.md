# Collaborative Conversations

## Design Position

Pair prompting means multiple participants editing the same prompt, not submitting independent personal drafts to a shared transcript. One root Thread provides shared conversation context, editable input, and current execution presentation. The App owns mutations and acceptance; browser replicas and synchronization delivery are not execution authority.

Collaboration is scoped to one running server instance. Opening the same data root in several independent App processes does not create a distributed collaboration server or share their current operation receipts.

## Shared Values and Authority

| Value                | Meaning                                                                 | Authority and lifetime                                         |
| -------------------- | ----------------------------------------------------------------------- | -------------------------------------------------------------- |
| Shared draft         | Editable prompt content and selected attachments/context for one Thread | In-memory CRDT document, separate from continuation            |
| Submitted input      | Definite immutable content selected for one submission                  | Existing root admission and captured input boundary            |
| Participant presence | Display name, editor cursor/selection, and current participation        | Transient collaboration state, not an account or permission    |
| Operation receipt    | Exact current-process execution/control correlation                     | Existing root-operation authority                              |
| Conversation history | Inspection of selected continuation and current live output             | Existing transcript/checkpoint and live presentation contracts |

Participants see one shared draft and can edit it concurrently. Edits merge rather than replacing the entire document with the most recently received browser text. The UI shows collaborator names, cursors, and selections without forcing participants to share navigation or scroll.

Names and colors identify collaborators for interaction and attribution; they are not verified identities. Input submission, approval, cancellation, steering, and other shared controls identify their originating participant where available, without fabricating Agent tool messages for human actions.

## Editing, Synchronization, and Submission

Browsers interact through one shared CRDT document per participating root Thread. The server retains synchronization state in memory under the [storage boundary](../03-local-storage-and-recovery.md#shared-browser-drafts); the CRDT library owns merging and synchronization. A draft can exist before a Run and remain editable while a Run executes.

The browser distinguishes local unsynchronized changes, synchronized editing state, and submitted content. Synchronization is not a durable save. Server restart does not promise draft recovery, and unsynchronized content is not promised to survive a lost browser process.

Send is a frontend action: it captures the current prompt and selected attachments and calls the existing root submission API. The immutable submitted input does not change when participants continue editing. The App's one-active-root-operation rule remains in force. Document versions are synchronization metadata, not a separate server-side submission, deduplication, or recovery protocol.

Successful submission does not discard edits outside the captured snapshot. A rejected submission retains editable content. Preparing the next prompt during a Run is editing, not acceptance into a durable execution queue. The UI distinguishes explicit steering of the current Run from an ordinary next prompt.

Submission rejection, acceptance, and an unknown outcome are different observations. A lost response does not trigger an automatic retry. The browser shows available operation evidence and leaves an unresolved outcome explicit. Neither CRDT synchronization nor root admission promises exactly-once submission or external effects. Editing state never authorizes automatic execution.

Attachments use the existing Thread-scoped input identity and limits. Draft/context selection does not silently drop unsupported or unavailable content. A selected file or diff includes its location and the content/baseline reviewed by the participants; a later file edit cannot silently change that selection while it is represented as the same submitted context. A Host path alone does not prove readability in a remote Agent Environment.

## Shared Execution and Decisions

All participants observe the same root operation, child activity, pending requests, and resulting decisions through App projections. Multiple subscribers do not start additional Runs.

Approval or question responses target the exact pending request/continuation. Competing responses cannot both resolve that same request; a stale response receives a conflict or already-resolved result and refreshes current state. Cancel and steer target the exact current receipt and cannot accidentally affect a later Run.

Execution continues independently of browser presence. A browser may close its subscription without cancelling the Run. Reopening the Thread obtains current App state and resumes supported live delivery; retained checkpoints, not browser transcripts, authorize continuation.

## Failure and Reconnect Behavior

| Event                                              | Observable outcome                                                                               |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Browser transport disconnect                       | Presence becomes unavailable; no automatic prompt submission, cancellation, or tool-input replay |
| Reconnect to the same instance                     | Synchronize the in-memory draft and query current operation evidence                             |
| Live cursor no longer usable                       | Follow the owning stream reset/snapshot contract, not fabricated replay                          |
| Another participant submits while edits arrive     | Preserve edits not captured in the submitted version; never blindly clear newer content          |
| Admission fails or Thread is busy                  | Report rejection and preserve the draft; do not silently queue a root operation                  |
| Server process restarts                            | Restore selected continuation, not shared drafts, presence, receipts, or execution control       |
| Post-restart submission outcome is not established | Show uncertainty and require deliberate action; do not submit text automatically                 |

Uncertainty is not proof of rollback. A provider or tool operation might have taken effect even when no terminal result was saved.

## Invariants

1. Collaborators edit one draft per root Thread, not independent per-browser prompts.
2. A shared draft, a submitted input snapshot, and a continuation checkpoint are distinct values.
3. Editing synchronization alone cannot start execution.
4. Send uses existing root admission; CRDT synchronization adds no execution authority.
5. Submission does not erase uncaptured edits or silently replace selected context with newer file content.
6. Shared drafts are process-local and never authorize automatic execution after a restart.
7. Presence does not grant different permissions or establish verified identity.
8. Shared control decisions use existing exact-receipt and exact-continuation authority.
