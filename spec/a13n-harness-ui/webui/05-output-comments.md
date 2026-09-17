# Saved Output Comments

## Browser Availability

The current browser disables comment entry points, discussion loading, selection actions, markers, and mutations. This document retains the App/API and storage contract; disabling the frontend does not delete records, migrate targets, or revoke API access. Captured references already present in submitted input remain readable. [Workbench interaction](04-workbench-interaction.md#saved-output-and-disabled-comments) owns the current browser surface.

Saved child inspection returns the latest final result before activity excerpts. Its initial text window and subsequent exact-target reads use bounded pages with total character count and next offset. Pagination is inspection, not evidence that a partial excerpt is a complete comment source.

## Design Position

Participants publish comments about saved AI output without editing that output or adding messages to the Agent's conversation. Comments are durable human collaboration records owned by `HarnessUiApp`. They are separate from [page/editor presence and shared prompt drafts](01-collaborative-conversations.md), and use the existing [local storage boundary](../03-local-storage-and-recovery.md#output-comment-storage).

The comment contract covers publishing, reading, editing, and deleting comments on saved assistant text, either a whole text block or a contiguous selection within it. It does not introduce jointly edited comment bodies, reply trees, resolution workflows, account roles, or an issue tracker. Publishing another comment does not replace an earlier participant's comment. Drafting a comment before publication is personal browser state, not the Thread's shared composer.

Only output already available through an App-owned saved transcript or parent-scoped saved child inspection is commentable. Output still streaming or retained only in a process-local observer/receipt is not commentable, even when generation has stopped. Saving failure does not become saving success because text is visible. Tool payloads, hidden context, system instructions, and thinking are not assistant-text comment targets.

## Ownership and Identity

| Value                          | Meaning and owner                                                                                                         |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------- |
| Comment identity               | Stable opaque identity for one published comment; independent of transport, page membership, and root receipts            |
| Root Thread                    | Conversation family under which comments are queried and shared                                                           |
| Producing Thread               | Root or descendant whose saved assistant output is being discussed; App validates the family relationship                 |
| Saved output target            | Exact saved source and assistant text-block locator, obtained from a detached App projection                              |
| Optional text selection        | Start/end positions and exact selected text within that source block, not page coordinates                                |
| Author attribution             | Publication-time self-declared display name and optional participant correlation; not a verified user or permission claim |
| Comment body and creation time | Bounded nonempty human-authored text and App-assigned UTC publication time                                                |

These are conceptual relationships, not a serialized wire schema or additional persistent Page/Room/User resources. Comment identity follows [Platform Data Conventions](../../data-conventions.md). A display name can change later without rewriting attribution on published comments. Disconnect or server restart does not delete the author label with transient presence.

All participants use the existing trusted-instance access boundary. Page presence is neither required for reading a saved comment nor evidence of permission to access its target. Comment lookup, publication, and referenced-output reads validate the owning Thread scope. A browser never acquires arbitrary immutable-object read access by supplying a comment target.

## Saved Output Anchors

The target is bound to an immutable saved source, not to the Thread's changing latest continuation. Within that source, the App identifies the exact visible assistant text block. For root history this uses the saved continuation's message/part location; saved child output follows its checkpoint's own display location. These location domains are not interchangeable and are wrapped in a typed detached target rather than an unqualified message index.

A first publication resolves against the saved source selected by the corresponding transcript or child-inspection projection and checks that selection again at commit. A stale selection fails explicitly and leaves the local comment text intact for refetch and review; the server does not guess a replacement target. Further comments can refer to an original saved target already retained by a published comment, without requiring that source to become the Thread's latest continuation again.

An optional selection uses Unicode code-point positions in the exact source text before Markdown rendering or whitespace normalization. Its quoted text must match the specified range. Browsers translate DOM selections to that source representation; an ambiguous or unrepresentable selection offers whole-block commenting rather than publishing an approximate range. Content clipping does not turn the displayed excerpt into the complete source. The API bounds comment text and selected quotes and rejects oversized values rather than silently truncating the anchor.

Once published, the source identity and location are immutable. A later Run, model-history compaction, or a new selected continuation cannot retarget the comment to a different output at the same position. Identical text in another response is not proof of identical output. Comments remain queryable under their root Thread and expose their original referenced output through a bounded App read. Inline markers appear only where the renderer can establish the same saved target; otherwise the comment remains visible in the Thread's comment list with an explicit original-output view. Navigation to that view does not select an execution continuation.

The comment's source reference participates in storage retention. It does not require a second transcript database or a copy of every Run's output. A missing or invalid retained source is reported as unavailable while the committed comment remains readable; no current file, later response, or browser cache silently substitutes for it.

## Publish, Read, and Delivery

The ordinary App publication operation validates the target, text selection, attribution, and body, then commits one complete comment. HTTP is a thin adapter over that operation, not a second comment store. Publication uses no root admission, changes no Thread configuration/continuation version, and is allowed while another Run executes against the same Thread when the saved target remains valid.

The initiating client allocates and retains one comment identity for the publication attempt. Repeating that identity with the same canonical target, attribution, and body returns the committed record; reuse with different content is a conflict. This is reconciliation against the comment's own durable identity, not a separate deduplication ledger or an execution-submission protocol. Current instance access is revalidated on every request. Loss of transient participant correlation after reconnect does not invalidate the saved publication identity.

Success is acknowledged only after the SQLite commit. A failure before commit publishes no comment. If a response is lost after possible commit, the client keeps its text and identity, reads or republishes that same identity, and does not allocate a second identity automatically. Server creation time remains the original time on reconciliation. Concurrent comments with different identities are independent appends; they do not need a synthetic Thread version precondition or CRDT merging.

Reads return bounded detached records, ordered deterministically by creation time with comment identity as tie-breaker, through cursor-based pagination scoped to the root Thread, ordering direction, and any exact output-target filter. Ascending order remains the API default; the workbench requests newest-first so a just-published comment appears on the first page. Comment history is not restricted to the current selected continuation. No comments for a Thread means an empty collection, not missing or failed conversation history.

After commit, the App emits a best-effort comment-scope invalidation through its existing summary delivery boundary. A missed hint or slow/disconnected subscriber cannot undo publication. Clients refetch comments after a fresh subscription or reset; a realtime replay cursor is not a comment-history cursor or a durable notification ledger. Independent Apps sharing a data root can read committed comments but do not thereby share live presence or in-process notifications.

## Editing and Deletion

Trusted instance participants can edit or delete published comments; self-declared author labels are attribution, not ownership permissions. Editing changes only the body. Comment identity, target, selection, author, and creation time remain fixed. A comment has a positive `version`, initially one, and an optional last-edit time. Edits and deletion require the version the participant reviewed. A stale version fails with a conflict and preserves the browser's unfinished edit; it never silently overwrites newer feedback.

The App applies each mutation in a short SQLite transaction and emits the existing comment-scope invalidation only after commit. An exact edit retry can reconcile the immediately following version with the same body, without another version increment. Older retries cannot overwrite later edits. Reposting a publication cannot restore an edited body or a deleted comment.

Deletion removes the comment from reads, listings, inline highlights, and new capture. It clears the stored publication body and leaves an identity tombstone so a lost acknowledgement or old publication retry cannot resurrect the comment. Repeated deletion of that scoped tombstone succeeds. Deletion is not a recall of immutable copies already captured into messages. The browser confirms this distinction before deleting. No reply tree, trash browser, restoration workflow, or durable edit history is introduced.

## Relationship to Agent Execution

Comments do not modify `HarnessState`, create synthetic user/tool messages, answer pending decisions, or automatically steer an Agent. Reading or posting a comment is not model input.

To discuss feedback with the Agent, a participant explicitly captures a selected published comment and its complete referenced assistant text into the ordinary composer. The browser supplies the reviewed comment version; a changed version is rejected before capture. Capture fixes the version read at the start of the operation; later edits or deletion do not rewrite that snapshot. A comment is one complete independent publication; there is no hidden reply tree to omit. The capture preserves the full body, selection, attribution and saved target rather than substituting a quoted excerpt for the original output. Oversized or unavailable complete context is rejected without truncation or a guessed replacement.

Capture uses the existing Thread-scoped retained-input owner and shared attachment selection, not another composer document root or a model-side comment-read Capability. Model input contains the captured text; existing input metadata identifies its comment provenance so the browser can render an inspectable reference card instead of duplicating the expanded text in the editor. The actual captured model-visible text remains available for review before submission and in input history. Opening, publishing or capturing a comment never starts a Run. The explicit Add to message action changes the shared selection only after capture succeeds. It uses the same inline attachment pointer, inspection, removal, undo, and submission lifecycle as images. Bounded author/body/quote previews and the captured version are additive attachment-level presentation metadata; the strict source provenance retains its existing shape for older readers. Previews never replace the complete model-visible captured text.

Existing input bounds, immutable capture, admission, capture-only clearing and root steering rules apply. Comments remain independent, editable collaboration records after the resulting input is submitted. Captured references remain independent immutable inputs, even when the original comment is subsequently edited or deleted. Captured bytes are fixed at selection time; later navigation, comment publication, continuation changes or source inspection do not replace them. No comment-read or navigation action changes the shared prompt implicitly.

## Failure and Recovery

| Event                                                    | Observable result                                                                               |
| -------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Output has no saved source                               | Comment publication is unavailable; visible live text is not made durable implicitly            |
| Saved selection changes before first publication commits | Explicit stale-target result; preserve the local comment for refetch and review                 |
| Target, quote, family scope, or body is invalid          | Reject the complete comment without a partial stored record                                     |
| Database commit fails                                    | Do not report publication or emit a successful-change hint                                      |
| Publication response is lost                             | Reconcile the same comment identity; do not silently post a duplicate                           |
| Presence or realtime connection is lost                  | Committed comments remain queryable; only live delivery/awareness is unavailable                |
| New Run, archive, compaction, or App restart             | Keep comments and original saved target references; do not reconstruct draft or execution state |
| Original source cannot be read                           | Preserve the comment and report source unavailability; never retarget by text similarity        |

## Invariants

1. Comments discuss saved assistant output; they do not make unsaved streaming output durable.
2. AI output remains read-only to collaborators, and comments are not CRDT document roots.
3. A comment's target and publication-time attribution survive presence loss and continuation changes.
4. Every acknowledged comment is committed independently of Run success or live delivery.
5. Reconciliation uses the same comment identity and cannot overwrite different content.
6. Comment reads and referenced-output views grant no execution, configuration, or generic storage authority.
7. Comment publication alone never enters model context or resolves an execution decision.
8. Schema migration and retention reuse Harness UI's existing local store; no database service, distributed room system, or durable live-event log is introduced.
