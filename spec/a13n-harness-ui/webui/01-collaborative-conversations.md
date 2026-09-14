# Collaborative Conversations

## Design Position

Collaboration combines awareness of participants' current pages with shared work on those pages. Page presence describes where people are looking; shared prompt editing merges changes to one root Thread's input. Neither changes another participant's navigation or turns the conversation transcript into a jointly editable document. The App owns mutations and acceptance; browser replicas and synchronization delivery are not execution authority.

Pair prompting means multiple participants editing the same prompt, not submitting independent personal drafts to a shared transcript. One root Thread provides shared conversation context, editable input, and current execution presentation. [Output comments](05-output-comments.md) add separately persisted human discussion on saved AI output; their lifetime is not the shared draft's lifetime.

Collaboration is scoped to one running server instance. Opening the same data root in several independent App processes does not create a distributed collaboration server or share their current operation receipts.

## Shared Values and Authority

| Value                | Meaning                                                                      | Authority and lifetime                                                       |
| -------------------- | ---------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| Shared draft         | Editable prompt content and selected attachments/context for one Thread      | In-memory CRDT document, separate from continuation                          |
| Submitted input      | Definite immutable content selected for one submission                       | Existing root admission and captured input boundary                          |
| Page presence        | Display profile, current page/focus, and foreground/background participation | Transient App state, not an account, permission, or navigation command       |
| Editor presence      | Cursor and selection relative to a specific shared draft                     | Transient state associated with that editor, not CRDT document content       |
| Output comment       | Published human discussion about saved assistant text                        | App-owned durable record under the [comment contract](05-output-comments.md) |
| Operation receipt    | Exact current-process execution/control correlation                          | Existing root-operation authority                                            |
| Conversation history | Inspection of selected continuation and current live output                  | Existing transcript/checkpoint and live presentation contracts               |

Participants see one shared draft and can edit it concurrently. Edits merge rather than replacing the entire document with the most recently received browser text. The UI shows collaborator names, cursors, and selections without forcing participants to share navigation or scroll.

Names and colors identify collaborators for interaction and attribution; they are not verified identities. Input submission, approval, cancellation, steering, and other shared controls identify their originating participant where available, without fabricating Agent tool messages for human actions.

## Create-Only Conversation Identity

The existing Thread creation API accepts an optional `thread_id` in the canonical `thread-` plus 32 lowercase hexadecimal format. Omitting it retains server-generated identity. A browser may allocate and retain this identity before creating its first conversation so a lost acknowledgement can be resolved through the ordinary exact Thread read.

Creation is create-only, not an upsert or a permanent request cache. The storage transaction rejects an existing identity without replacing its configuration, title, or initial state. Concurrent requests for one identity can create at most one Thread. A client receiving a conflict or uncertain result reads that exact identity; it never silently allocates another one. Current mutable Thread fields are not interpreted as an immutable creation-request receipt. Thread creation remains separate from prompt admission and starts no Run.

## Page Presence and Personal Focus

Page presence is available across the authenticated workbench, including when no Thread or shared draft is open. It does not require native computer sharing. The App owns the current participant directory; clients report their own current view and consume detached directory and same-page membership projections.

A live participation identity identifies one browser tab in the current App lifetime. Display name and color are user-supplied presentation, not verified account identity. Separate tabs remain distinguishable even when they use the same name; neither a matching name nor an IP address establishes that two connections are one person. Reconnect establishes fresh live participation and reports the current page again rather than replaying navigation history. A comment retains its publication-time author attribution independently of these temporary identities.

A page target identifies a workbench area and its semantic resource, not a raw browser URL, DOM node, screen coordinate, or new persistent Page resource. Examples include a root Thread's conversation, Project settings, a configured resource, or a native file/diff or terminal view. Resource identity follows its owning App projection; a native location remains on the server and does not become an Agent Environment path. A Thread-backed page also retains its root-Thread context. Unsupported or no-longer-available targets are shown as unavailable, not silently rebound to another resource.

Two participants are on the same page when their area and resource target match. Layout, scroll offset, URL access fragments, and personal display preferences do not change that identity. Opening multiple panes does not imply simultaneous focus: the tab reports the currently focused area. The containing Thread and an editor's draft membership are separate context, so focusing a file pane does not move the shared draft to a new room.

A visible focused tab reports foreground participation. A background or unfocused tab may remain attached but is shown as inactive rather than claiming the person is currently reading it. The UI distinguishes that observation from disconnected or unknown state; transport reachability alone is not human attention. On reported navigation, the old page membership is replaced by the new one. Closing, forgetting access, or detecting a lost presence connection removes its live membership within the transport's bounded loss-detection interval. These observations never establish Agent-process liveness, a lease, or permission to take over execution.

Participants can inspect who is on each page and who shares their current page. Opening a collaborator's reported location is an explicit personal navigation action through the ordinary access and availability checks. There is no implicit follow mode, synchronized scrolling, or screen recording. Thread navigation shows stacked participant avatars, with an inspectable list that distinguishes the current tab, active participants, and away tabs; separate tab identities are never merged by display name. Page membership itself does not read file contents, create a terminal, change configuration, submit a prompt, or start a Run.

## Shared Mouse Pointers

On the same conversation page, participants can see named, color-coded mouse pointers over the shared composer and loaded saved transcript entries. Pointers are approximate positions relative to those surfaces, not desktop coordinates or precise text selections. A receiver resolves only the matching visible surface; absent, clipped, or covered content does not acquire a floating pointer. Different viewport sizes and personal scroll positions do not force navigation or scrolling. Other workbench areas and live output do not broadcast pointer positions.

Mouse observations use the existing authenticated per-tab presence connection with explicit pointer capability opt-in. They retain only the latest bounded position, are coalesced independently of directory/resource inspection, and never enter the shared document, transcript, comments, or storage. Only other foreground participants on the same conversation receive current positions. Navigation, leaving the supported surface, loss of focus, disconnect, and a short idle expiry clear them; reconnect does not replay old positions. Legacy participants that do not opt in continue receiving only the ordinary directory frames.

## Editor Presence and CRDT Scope

The shared document contains prompt text and selected attachment references only. Editor presence carries optional relative cursor/selection positions for that document; the browser resolves and renders those positions through the CRDT library. They are not page-wide mouse coordinates, durable comments, or edits to the document. Disconnect removes editor presence without deleting shared content. Remote carets retain a visible name label and participant color; blurring the editor or 30 seconds without local editor interaction clears its selection report without changing the draft. Remote edits and awareness heartbeats do not renew local editing activity. The server expires unrefreshed cursor reports after 30 seconds, and a browser independently clears peer cursors when draft delivery stalls, including a half-open connection.

Page presence, editor presence, and the draft are distinct values even when an implementation shares an authenticated connection. Page changes update presence, not the CRDT roots. A same-page group uses that page's existing capabilities: the shared composer for prompt coediting, [comments](05-output-comments.md) for saved AI output, and the existing native-operation rules for files or terminals. Presence does not make every text surface collaboratively editable.

## Editing, Synchronization, and Submission

Browsers interact through one shared CRDT document per participating root Thread. The server retains synchronization state in memory under the [storage boundary](../03-local-storage-and-recovery.md#shared-browser-drafts); the CRDT library owns document identities and merging, while the App owns validated publication, membership, and transport delivery. A draft can exist before a Run and remain editable while a Run executes.

Authored text and attachments share one ordered editor surface. Attachments are atomic identities, not interpretations of visible filename labels: typing or pasting ordinary label text does not select bytes. Native editor deletion, selection, cut/paste within the same draft, and undo/redo preserve that distinction. Uploads reserve their position before asynchronous acquisition completes; pending, failed, or unresolved selections block submission until completed or explicitly removed. Removing a token does not delete Thread bytes. A bounded reference registry may retain dormant identities so undo can restore deleted tokens; only live occurrences count as selected attachments.

The browser distinguishes local unsynchronized changes, synchronized editing state, and submitted content. Synchronization is not a durable save. Server restart does not promise draft recovery, and unsynchronized content is not promised to survive a lost browser process.

Send is a frontend action: it captures the current authored order and selected attachments and calls the existing root submission API. The HTTP boundary accepts ordered text/Thread-reference parts, while retaining the legacy prompt-plus-trailing-IDs form; combining nonempty legacy content with ordered parts is rejected. The App resolves references through its ordinary composer owner, not a new runtime protocol. The immutable submitted input does not change when participants continue editing. The App's one-active-root-operation rule remains in force. Document versions are synchronization metadata, not a separate server-side submission, deduplication, or recovery protocol.

Successful submission does not discard edits outside the captured snapshot. A client can retain an exact CRDT replica for the captured input, delete its visible text and selections only after a positive submission acknowledgement, then merge that deletion update into its current replica. Clearing by current text offsets or replacing the live document with an empty value is not equivalent: it can erase uncaptured edits. The server owns no Send-version registry. A rejected submission retains editable content. Preparing the next prompt during a Run is editing, not acceptance into a durable execution queue. The UI distinguishes explicit steering of the current Run from an ordinary next prompt.

Submission rejection, acceptance, and an unknown outcome are different observations. A lost response does not trigger an automatic retry. The browser shows available operation evidence and leaves an unresolved outcome explicit. Neither CRDT synchronization nor root admission promises exactly-once submission or external effects. Editing state never authorizes automatic execution.

Live and saved input display the same authored order, using source/part metadata to collapse an attachment's multiple native content pieces without collapsing repeated authored references. Compact attachment controls expose retained metadata and explicit original-byte downloads. Image thumbnails read only authenticated Thread attachment bytes; arbitrary remote media URLs and current Host paths are not automatically fetched.

Attachments use the existing Thread-scoped input identity and limits. Draft/context selection does not silently drop unsupported or unavailable content. A selected file or diff includes its location and the content/baseline reviewed by the participants; a later file edit cannot silently change that selection while it is represented as the same submitted context. A Host path alone does not prove readability in a remote Agent Environment.

## Independent Delivery and Reconnect

| Concern                          | Browser delivery                                                   | Authoritative state                              |
| -------------------------------- | ------------------------------------------------------------------ | ------------------------------------------------ |
| Current page and editor presence | Authenticated interactive updates and current membership snapshots | Current App participation only                   |
| Shared composer                  | Authenticated bidirectional CRDT synchronization                   | In-memory draft document                         |
| Thread execution and history     | Existing focused SSE plus detached history queries                 | Existing Run observations and saved continuation |
| Published comments               | Ordinary App write/read operations and post-commit refetch hints   | Persisted comment records                        |

A Thread SSE update or reset does not reset the composer, clear published comments, or change the participant's page. A draft update does not append an assistant message or alter saved history. The frontend keeps these state owners independent of component rerendering. There is no global ordering or transaction across SSE, interactive delivery, and HTTP acknowledgements; submission and comment publication use their own explicit completion boundaries.

After a page reload, the browser reestablishes presence, reloads saved comments and history, and rejoins an available draft independently. A draft incarnation change is not a comment reset. A presence snapshot is not proof that all Thread events or comments have been received.

## Shared Execution and Decisions

All participants observe the same root operation, child activity, pending requests, and resulting decisions through App projections. Multiple subscribers do not start additional Runs.

Approval or question responses target the exact pending request/continuation. Competing responses cannot both resolve that same request; a stale response receives a conflict or already-resolved result and refreshes current state. Cancel and steer target the exact current receipt and cannot accidentally affect a later Run.

Execution continues independently of browser presence. A browser may close its subscription without cancelling the Run. Reopening the Thread obtains current App state and resumes supported live delivery; retained checkpoints, not browser transcripts, authorize continuation.

## Failure and Reconnect Behavior

| Event                                              | Observable outcome                                                                                                          |
| -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| Browser transport disconnect                       | Presence becomes unavailable; no automatic prompt submission, cancellation, or tool-input replay                            |
| Reconnect to the same instance                     | Synchronize the in-memory draft and query current operation evidence                                                        |
| Live cursor no longer usable                       | Follow the owning stream reset/snapshot contract, not fabricated replay                                                     |
| Another participant submits while edits arrive     | Preserve edits not captured in the submitted version; never blindly clear newer content                                     |
| Admission fails or Thread is busy                  | Report rejection and preserve the draft; do not silently queue a root operation                                             |
| Server process restarts                            | Reload selected continuation and committed comments; do not restore shared drafts, presence, receipts, or execution control |
| Post-restart submission outcome is not established | Show uncertainty and require deliberate action; do not submit text automatically                                            |

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
11. Thread SSE resets and draft synchronization cannot overwrite one another's state or erase published comments.
