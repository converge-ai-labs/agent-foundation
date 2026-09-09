# Collaborative Conversations

## Design Position

Pair prompting means multiple participants editing the same prompt, not submitting independent personal drafts to a shared transcript. One root Thread provides shared conversation context, editable input, and current execution presentation. The App owns mutations and acceptance; browser replicas and synchronization delivery are not execution authority.

Collaboration is scoped to one running server instance. Opening the same data root in several independent App processes does not create a distributed collaboration server or share their current operation receipts.

## Shared Values and Authority

| Value                | Meaning                                                                 | Authority and lifetime                                         |
| -------------------- | ----------------------------------------------------------------------- | -------------------------------------------------------------- |
| Shared draft         | Editable prompt content and selected attachments/context for one Thread | App-owned saved editing state, separate from continuation      |
| Submitted input      | Definite immutable content selected for one submission                  | Existing root admission and captured input boundary            |
| Participant presence | Display name, editor cursor/selection, and current participation        | Transient collaboration state, not an account or permission    |
| Operation receipt    | Exact current-process execution/control correlation                     | Existing root-operation authority                              |
| Conversation history | Inspection of selected continuation and current live output             | Existing transcript/checkpoint and live presentation contracts |

Participants see one shared draft and can edit it concurrently. Edits merge rather than replacing the entire document with the most recently received browser text. The UI shows collaborator names, cursors, and selections without forcing participants to share navigation or scroll.

Names and colors identify collaborators for interaction and attribution; they are not verified identities. Input submission, approval, cancellation, steering, and other shared controls identify their originating participant where available, without fabricating Agent tool messages for human actions.

## Editing, Persistence, and Submission

The App persists shared draft content independently of the selected `HarnessState` checkpoint under the [storage boundary](../03-local-storage-and-recovery.md#shared-browser-drafts). Presence is transient. A draft can exist before a Run and remain editable while a Run executes.

The browser distinguishes local unsynchronized changes, saved editing state, and submitted content. A save indication requires server persistence evidence, not merely local rendering or transport delivery. Unsynchronized content is not promised to survive a lost browser process.

Submission captures a definite synchronized draft version and its selected content. The immutable submitted snapshot does not change when participants continue editing. Concurrent requests to submit that same version represent one logical submission and must not admit duplicate root executions within the running App. The App's one-active-root-operation rule remains in force.

Successful submission does not discard edits outside the captured snapshot. A rejected submission retains editable content. Preparing the next prompt during a Run is editing, not acceptance into a durable execution queue. The UI distinguishes explicit steering of the current Run from an ordinary next prompt.

Submission rejection, acceptance, and an unknown outcome are different observations. A lost response causes the browser to reconcile the original submission against current App evidence, not silently send the prompt as a new operation. Server process loss does not authorize replay from a saved draft. Neither draft durability nor same-submission deduplication promises exactly-once external effects or durable root-input acceptance.

Attachments use the existing Thread-scoped input identity and limits. Draft/context selection does not silently drop unsupported or unavailable content. A selected file or diff includes its location and the content/baseline reviewed by the participants; a later file edit cannot silently change that selection while it is represented as the same submitted context. A Host path alone does not prove readability in a remote Agent Environment.

## Shared Execution and Decisions

All participants observe the same root operation, child activity, pending requests, and resulting decisions through App projections. Multiple subscribers do not start additional Runs.

Approval or question responses target the exact pending request/continuation. Competing responses cannot both resolve that same request; a stale response receives a conflict or already-resolved result and refreshes current state. Cancel and steer target the exact current receipt and cannot accidentally affect a later Run.

Execution continues independently of browser presence. A browser may close its subscription without cancelling the Run. Reopening the Thread obtains current App state and resumes supported live delivery; retained checkpoints, not browser transcripts, authorize continuation.

## Failure and Reconnect Behavior

| Event                                              | Observable outcome                                                                                  |
| -------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Browser transport disconnect                       | Presence becomes unavailable; no automatic prompt submission, cancellation, or tool-input replay    |
| Reconnect to the same instance                     | Synchronize current draft and reconcile retained local edits and submission evidence                |
| Live cursor no longer usable                       | Follow the owning stream reset/snapshot contract, not fabricated replay                             |
| Shared-draft persistence failure                   | Do not report the failed update as saved; retain recoverable editing state and surface the failure  |
| Another participant submits while edits arrive     | Preserve edits not captured in the submitted version; never blindly clear newer content             |
| Admission fails or Thread is busy                  | Report rejection and preserve the draft; do not silently queue a root operation                     |
| Server process restarts                            | Restore saved draft and selected continuation, but not old presence, receipts, or execution control |
| Post-restart submission outcome is not established | Show uncertainty and require deliberate recovery; do not run the saved text automatically           |

Uncertainty is not proof of rollback. A provider or tool operation might have taken effect even when no terminal result was saved.

## Invariants

1. Collaborators edit one draft per root Thread, not independent per-browser prompts.
2. A shared draft, a submitted input snapshot, and a continuation checkpoint are distinct values.
3. Editing synchronization alone cannot start execution.
4. The same logical submission does not create duplicate root executions within the App lifetime.
5. Submission does not erase uncaptured edits or silently replace selected context with newer file content.
6. Saved draft state never authorizes automatic execution after a restart.
7. Presence does not grant different permissions or establish verified identity.
8. Shared control decisions use existing exact-receipt and exact-continuation authority.
