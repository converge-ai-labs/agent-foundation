# Collaborative Conversations

## Design Position

Collaboration combines awareness of participants' current pages with shared work on those pages. Page presence describes where people are looking; shared prompt editing merges changes to one root Thread's input. Neither changes another participant's navigation or turns the conversation transcript into a jointly editable document. The App owns mutations and acceptance; browser replicas and synchronization delivery are not execution authority.

Pair prompting means multiple participants editing the same prompt, not submitting independent personal drafts to a shared transcript. One root Thread provides shared conversation context, editable input, and current execution presentation.

Collaboration is scoped to one running server instance. Opening the same data root in several independent App processes does not create a distributed collaboration server or share their current operation receipts.

## Shared Values and Authority

| Value                | Meaning                                                                       | Authority and lifetime                                                 |
| -------------------- | ----------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| Shared draft         | Editable text, attachment references, and composer Run choices for one Thread | Current saved CRDT state, separate from continuation                   |
| Submitted input      | Definite immutable content selected for one submission                        | Existing root admission and captured input boundary                    |
| Page presence        | Display profile, current page/focus, and foreground/background participation  | Transient App state, not an account, permission, or navigation command |
| Editor presence      | Cursor and selection relative to a specific shared draft                      | Transient state associated with that editor, not CRDT document content |
| Operation receipt    | Exact current-process execution/control correlation                           | Existing root-operation authority                                      |
| Conversation history | Inspection of selected continuation and current live output                   | Existing transcript/checkpoint and live presentation contracts         |

Instance-key participants and [prompt guests](05-scoped-sharing.md) see one shared draft and can edit it concurrently. View guests observe saved messages and live output without joining the draft. Guest page presence exposes only their authorized current conversation, not the instance directory. Edits merge rather than replacing the entire document with the most recently received browser text. The UI shows collaborator names, cursors, and selections without forcing participants to share navigation or scroll.

Names and colors identify collaborators for interaction and attribution; they are not verified identities. Input submission, approval, cancellation, steering, and other shared controls identify their originating participant where available, without fabricating Agent tool messages for human actions.

## Create-Only Conversation Identity

The existing Thread creation API accepts an optional `thread_id` in the canonical `thread_` plus 32 lowercase hexadecimal format. The legacy `thread-` plus 32 lowercase hexadecimal shape remains accepted permanently under the [shared identity convention](../../data-conventions.md#object-identity). Omitting it retains server-generated identity. A browser allocates new identities in the canonical shape and may retain an identity before creating its first conversation so a lost acknowledgement can be resolved through the ordinary exact Thread read. Retained legacy identities are reused unchanged, including browser-local drafts. New conversation composition uses `/new`, not a route-encoded draft identity.

Creation is create-only, not an upsert or a permanent request cache. The storage transaction rejects an existing identity without replacing its configuration, title, or initial state. Concurrent requests for one identity can create at most one Thread. A client receiving a conflict or uncertain result reads that exact identity; it never silently allocates another one. Current mutable Thread fields are not interpreted as an immutable creation-request receipt. Thread creation remains separate from prompt admission and starts no Run.

## Page Presence and Personal Focus

Page presence runs across the authenticated workbench independently of drafts and native computer sharing. The App owns the participant directory; clients report their current view and consume detached directory and same-page projections.

- **Identity:** A live participation ID identifies one browser tab in the current App lifetime. Display names and colors are user-supplied attribution; separate tabs stay distinct even with identical names. Reconnect registers a fresh participation ID and reports the current page.
- **Page targets:** A target identifies a workbench area and semantic resource (root Thread, Project settings, configured resource, or native file/diff/terminal view) using App projections. Unsupported or missing targets show as unavailable.
- **Same-page and activity:** Participants share a page when their area and resource match (layout, scroll, and display preferences are personal). Visible focused tabs report foreground participation; background/unfocused tabs report inactive. Closing, forgetting access, or transport loss clears membership within the bounded loss-detection interval. Opening a collaborator's location is an explicit personal navigation action.

## Shared Mouse Pointers

On the same conversation page, participants opting into pointer capability over their per-tab presence connection see named, color-coded mouse pointers over the shared composer and loaded saved transcript entries (relative to matching visible surfaces only; clipped/covered surfaces show no pointer). Positions are coalesced in memory, broadcast only to other foreground participants on the same conversation, and cleared on navigation, focus loss, disconnect, or short idle expiry. Legacy clients continue receiving directory frames only.

## Editor Presence and CRDT Scope

One logical shared composer holds prompt text, attachment references, and Run choices (`Model`/thinking/fast/reasoning-mode, `Environment`, `Goal`). Content and options use separate CRDT documents stored atomically in one row: prompt guests edit and receive content only (any options update from a guest is rejected); instance-key participants edit and receive both. Agent, Project, and Coordinator changes use existing Thread operations; unsaved popup edits, local upload bytes, annotations, and presence stay local or transient.

Editor presence reports relative cursor/selection positions rendered via the CRDT library. Blurring the editor or 30 seconds without local interaction clears the selection report; the server expires unrefreshed reports after 30 seconds, and browsers clear peer cursors when draft delivery stalls. Page presence, editor presence, and draft documents remain independent state owners.

## Unsent Input Discovery

The App exposes a content-free index of nonempty shared drafts independently of editor membership. Authenticated `GET /api/drafts` returns `thread_id`, `draft_id`, and `unsent_since` for each existing nonempty room. Reading this index never creates a room, joins an editor, changes Thread navigation recency, or starts execution. Thread metadata and archive state remain on the ordinary Thread lookup APIs.

A draft is nonempty when it contains non-whitespace prompt text or selected attachments, including pending or failed uploads. Dormant inline attachment registry entries retained for undo do not count. `unsent_since` records the latest empty-to-nonempty transition and does not change on subsequent typing or presence updates. Validated transitions between empty and nonempty publish a `draft` summary invalidation carrying only the Thread identity. Rejected edits leave the index unchanged.

The browser discovers rooms on initial load and summary reconciliation without opening every editor. Current editor content and unacknowledged local edits provide immediate reminders; a clean detached replica does not override newer room discovery. Reminders describe shared Thread input, not private participant ownership or accepted execution queues. The index reads saved metadata without loading CRDT documents and survives browser reload and App restart under the draft retention contract. A replaced or expired draft still requires explicit rejoin/recovery before merging a retained browser replica.

## Goal Submission Mode

The shared composer exposes a per-submission Goal toggle for new and existing root conversations. Its `normal`/`goal` selection belongs to shared composer options, independently of Thread configuration. The browser-local pre-Thread draft also restores this selection and defaults older drafts to `normal`.

After preparation and synchronization, Send captures content and choices together without an intervening await, then passes the immutable mode to ordinary root admission. Confirmed acceptance consumes only the captured Goal assignment; a concurrent new mode selection survives. Rejected and unknown submissions never resend automatically. Steering has no Goal-mode field. The toggle is unavailable while submission is preparing, pending/unknown, or the conversation cannot admit a new Run.

A lightweight Goal entry leads the work-inspection strip above the composer and consumes the App's optional live/saved [Goal projection](../05-runtime-subagents-and-surfaces.md#goal-execution). It replaces the duplicate generic working indicator while visible. The entry shows the current phase, with the additional-check number during checking; terminal states show the outcome without a count or completion percentage. It remains while the current or most recent execution is a Goal and disappears when an ordinary execution clears that projection. Selecting the unsent composer Goal toggle alone does not create an inspection entry.

The Goal popover shares the strip's single-open inspection behavior. It presents the current phase or final conclusion and its specific reason first, followed by an expandable objective preview, captured check budget, and observed token totals. Restore-audit requirements remain explicit. Long content stays within the viewport, and phase changes preserve the open popover and focus. Terminal failures and exhaustion remain unverified; verified completion identifies the same Agent as the verifier. Work artifacts and prose conclusions remain in the conversation rather than being copied into this execution summary. For an incomplete outcome where a new Run is permitted, **Use objective in composer** appends the objective without overwriting the draft and selects Goal mode; it does not submit or resume the old budget.

Only a matching live receipt can override the saved Goal projection; an old terminal receipt cannot restore a cleared Goal. Motion requires current preparing/running activity and respects reduced-motion preference. The entry stays intact when the inspection strip wraps on narrow screens.

## Editing, Synchronization, and Submission

Browsers share one logical composer per root Thread, persisted under the [storage boundary](../03-local-storage-and-recovery.md#shared-browser-drafts). The CRDT library owns document merging; the App owns validated publication, membership, and delivery. Acknowledged sync persists editing state across server restarts. On upgrade, an owner's legacy browser-local Model/control record initializes a pristine options document once (matching Agent/Model binding) and is then removed; existing shared state wins and prompt guests skip this migration.

- **Inline attachments:** Authored text and attachments share one ordered editor surface. Attachments are atomic identities (un-cropped thumbnails for raster images with full-size preview; labeled chips for other files; upload errors visible; pending/failed uploads block submission until resolved or removed). Removing a token leaves Thread bytes intact, and a bounded dormant registry supports undo while only live occurrences count as selected. Live and saved transcripts render attachments in authored order. Clicking a non-image draft or transcript attachment opens a right-side preview drawer that renders bounded text/code, Markdown, isolated SVG, local documents (PDF, spreadsheets, slides), and audio/video with one-click download, copy, and collapsible source and model-visible disclosures; oversized or unsupported files skip eager byte fetches and offer direct download.
- **Environments editor:** Groups local mode (`Full Control`, `Sandbox`, or profile) and local roots beside Device bindings and one default working location. Applied selections persist in shared composer options and capture on Send as a Run-only `environment` patch (Default inherits Thread/Agent config; unavailable selections stay visible; disabled during pending/unknown submissions; steering/deferred responses leave active Run environments unchanged).
- **Send, Steer, and draft capture:** HTTP submission requires ordered text/attachment `parts` and accepts an optional correlation `source_id` (`input_` + 32 lowercase hex, plus legacy `input-`). Send and Steer accept an optional `draft` capture (`draft_id`, `update_base64`, plus owner `options_update_base64`); the App validates the room incarnation and verifies all captured edits have synchronized before admission. After acceptance, the App consumes only the captured text, attachment occurrences, and (on Send) Goal assignment—preserving concurrent uncaptured edits, Model/Environment choices, and rejected drafts.
- **Optimistic preview and reconciliation:** While pending, the initiating tab keeps input in the composer with loading feedback (no speculative transcript bubble). Positive admission reveals a private preview reconciled by exact `source_id` against streamed/saved input; rejected submissions retain input; unknown outcomes surface explicit recovery without automatic retry. Selected file/diff context captures reviewed bytes/baseline immediately so later disk edits leave submitted context unchanged.

## Independent Delivery and Reconnect

| Concern                          | Browser delivery                                                   | Authoritative state                              |
| -------------------------------- | ------------------------------------------------------------------ | ------------------------------------------------ |
| Current page and editor presence | Authenticated interactive updates and current membership snapshots | Current App participation only                   |
| Shared composer                  | Authenticated bidirectional CRDT synchronization                   | Current bounded persisted composer               |
| Thread execution and history     | Focused realtime channels plus detached history queries            | Existing Run observations and saved continuation |

A Thread observation update or reset does not reset the composer, or change the participant's page. A draft update does not append an assistant message or alter saved history. The frontend keeps these state owners independent of component rerendering. There is no global ordering or transaction across realtime observation, interactive delivery, and HTTP acknowledgements; submission has its own explicit completion boundary.

After a page reload, the browser reestablishes presence, reloads saved history, and rejoins an available draft independently. A presence snapshot is not proof that all Thread events have been received.

## Shared Execution and Decisions

All participants observe the same root operation, child activity, pending requests, and resulting decisions through App projections. Multiple subscribers do not start additional Runs.

Approval or question responses target the exact pending request/continuation. Competing responses cannot both resolve that same request; a stale response receives a conflict or already-resolved result and refreshes current state. Cancel and steer target the exact current receipt and cannot accidentally affect a later Run.

### Decision Forms

The browser renders all three App decision kinds: structured questions, tool approvals, and external results. Approvals show available target, reason, risk, and arguments, with generic presentation for tools without a dedicated view. Shell approvals use the stable tool identity in advisory approval presentation metadata (with a read fallback for saved older metadata) to show the command, working directory, selected mount, and review assessment. Environment argument previews show variable names rather than values. Review text is inert text, not executable markup; internal approval bindings are not user-facing evidence.

A single approval exposes direct **Approve once** and **Deny** actions with an optional denial reason. Replacement arguments require the explicit App capability and a complete JSON object. Mixed or multiple requests retain local selections until the entire batch is answered and explicitly submitted together. No action is preselected. External results require explicit valid JSON, including explicit `null` when intended; an empty editor is not a result and does not execute an external tool. Existing omission and override restrictions follow the [App decision projection](../05-runtime-subagents-and-surfaces.md#root-deferred-response).

Submitted decisions are disabled while pending or accepted. Unknown acknowledgements are disclosed without replay; conflicts refetch the authoritative request. An accepted response receipt is not evidence that the tool executed or its result was saved. Browser deadlines remain advisory under the App-owned timeout contract.

Execution continues independently of browser presence. A browser may close its subscription without cancelling the Run. Reopening the Thread obtains current App state and resumes supported live delivery; retained checkpoints, not browser transcripts, authorize continuation.

## Failure and Reconnect Behavior

| Event                                              | Observable outcome                                                                                   |
| -------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Browser transport disconnect                       | Presence becomes unavailable; no automatic prompt submission, cancellation, or tool-input replay     |
| Reconnect to the same instance                     | Synchronize the current persisted draft and query current operation evidence                         |
| Live cursor no longer usable                       | Follow the owning stream reset/snapshot contract, not fabricated replay                              |
| Another participant submits while edits arrive     | Preserve edits not captured in the submitted version; never blindly clear newer content              |
| Admission fails or Thread is busy                  | Report rejection and preserve the draft; do not silently queue a root operation                      |
| Server process restarts                            | Reload selected continuation; do not restore shared drafts, presence, receipts, or execution control |
| Post-restart submission outcome is not established | Show uncertainty and require deliberate action; do not submit text automatically                     |

Uncertainty is not proof of rollback. A provider or tool operation might have taken effect even when no terminal result was saved.

## Invariants

01. Collaborators edit one draft per root Thread, not independent per-browser prompts.
02. A shared draft, a submitted input snapshot, and a continuation checkpoint are distinct values.
03. Editing synchronization alone cannot start execution.
04. Send uses existing root admission; CRDT synchronization adds no execution authority.
05. Submission does not erase uncaptured edits or silently replace selected context with newer file content.
06. Shared drafts are process-local and never authorize automatic execution after a restart.
07. Presence does not grant different permissions or establish verified identity.
08. Shared control decisions use existing exact-receipt and exact-continuation authority.
09. Page presence works without a draft and never changes another participant's navigation.
10. Background tabs are not presented as confirmed foreground attention; duplicate names are not merged into verified users.
11. Thread observation resets and draft synchronization cannot overwrite one another's state.
