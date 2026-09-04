# Thread Navigation and Conversation

## Design Position

The WebUI opens one continuation-backed root Thread directly as a conversation with understandable current-process activity. It deliberately shows retained and live facts as two layers. A smooth interaction experience never hides whether content is selected continuation history, provisional stream output, a saved child checkpoint, or a control that is available only in the current App process.

The canonical detail route names only the stable root Thread ID. Project, Agent, Environment profile, Plugin, Run Extension, and MCP selections are not route identity. They are visible mutable Thread configuration. A root Thread remains inspectable when one of those references is missing; only the next Run is blocked by App validation.

## Sidebar Navigation

The sidebar presents:

- one global New Thread action;
- a bounded Recent list across Projects;
- accepted Projects in configured order, each with a bounded expandable root-Thread list and a Project-scoped New Thread action;
- search and archive entry points;
- a bottom-pinned Settings action.

Project is the only root-Thread grouping. A Project list query includes root Threads whose current sticky `project_id` equals that exact ID. Recent is an App-projected cross-Project index ordered by authoritative recency; it does not own membership, acknowledgement, or another saved grouping. A Thread row shows only concise state relevant to navigation, such as active, awaiting input, failed, or idle. It never claims liveness from retained status alone.

The global New Thread action applies ordinary root-Thread creation defaults. A Project-scoped action initializes the draft with that exact Project. The create command always carries one resolved explicit Project selection. Project creation, guided editing, reordering, and deletion remain in Settings and use the ordinary expected-digest desired-resource boundary.

The sidebar never subscribes to every Thread. Its bounded recent, Project, and unresolved-Project pages consume detached summaries plus the App-wide invalidation stream. Opening a row selects its canonical Thread route; changing an expanded Project, search, archive filter, or page never mutates a Thread.

A retained Thread whose Project no longer resolves remains reachable through Recent, search, archive, or a bounded unresolved section supplied by the App. Reassignment is an explicit Thread configuration mutation; the browser never silently applies a similarly named Project.

## Conversation Shell

The selected root Thread occupies the primary region. Its header presents the Thread title, Project context, current state, Environment-panel toggle, activity entry, and a bounded overflow menu. Its body presents the semantic conversation and current activity. Its footer contains the only ordinary composer.

A selected root Thread opens directly as the ordinary conversation without an intermediate dashboard. Selecting a child, task, tool, review, or other activity opens a bounded contextual view under the owning root lineage; child Threads do not enter the root collection as peers. Exact child and operation correlations reuse the on-demand [Activity and Diagnostics](04-debugging-and-diagnostics.md) views rather than creating a permanent inspector.

Wide layouts keep the sidebar beside the conversation and can add one optional context panel. Narrow layouts show sidebar, conversation or activity, and selected context as separate panels with explicit back navigation. Layout changes preserve the same canonical Thread route, focused controller, draft, and selection.

## Opening a Thread

Opening `/threads/$threadId` establishes the focused watch before rendering detail. The first snapshot supplies Thread detail, current root operation, selected deferred request set, bounded task and child projections, selected continuation, epoch, and sequence cutover. The latest retained transcript page loads concurrently through a cursor bound to that selected continuation; a concurrent continuation change resets the query rather than combining histories.

The UI renders a stable shell immediately, then one of:

- retained timeline and connected live layer;
- retained timeline with a reconnecting live indicator;
- inspectable Thread with a configuration or resource error;
- explicit missing or inaccessible Thread state.

A stream outage does not erase retained history or imply that a process-local Run stopped. A refreshed snapshot decides current activity and control availability.

## Semantic Timeline

The timeline is a sequence of semantic blocks rather than raw protocol events:

| Block              | Source and behavior                                                                                                                 |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------- |
| User message       | Retained transcript request part; plain selectable text with media references when supplied                                         |
| Assistant message  | Retained or closed live text rendered as safe Markdown                                                                              |
| Thinking           | Non-encrypted retained or live reasoning, collapsed by default and clearly labeled                                                  |
| Tool activity      | Correlated call and status with a concise human-readable summary; exact safe detail opens contextual activity detail when available |
| Working State task | Bounded task progress and changed item; focused task detail remains inspection-only, never a second scheduler                       |
| Child activity     | Child name, task, activity, saved/local distinction, and available work controls under the owning root                              |
| Decision           | Exact pending approval or external result request from the selected suspended continuation                                          |
| Run outcome        | Human-readable execution, continuation, Environment-state, cleanup, usage, and failure facets                                       |
| Neutral activity   | Bounded generic presentation for an understood safe namespace or unknown validated event with optional activity detail              |

Multipart stream events fold into one open live block and close only on the matching event. An interrupted or reset stream marks an unfinished provisional block as incomplete and then replaces the complete live layer through a new snapshot; it does not append guessed text to retained history.

While a turn is active, current reasoning, tools, task changes, and child launches remain visible enough to explain progress. After a successful closed boundary, intermediate activity folds into a compact summary such as duration, tool count, task count, and child count only when those facts are complete. The final assistant answer remains primary. Failed, denied, interrupted, or still-running work stays expanded enough to explain its state. The on-demand activity view owns raw payload, exact correlation, schema, and timing detail.

Retained timeline order follows transcript positions. Provisional root and child blocks follow focused stream sequence and retain their producing Thread, Run, receipt, and execution correlation internally. Thread interaction presents those correlations only when needed for control or an explicit activity-detail action. Timestamp display never substitutes for the ordering authorities.

Long transcripts use incremental keyset pagination toward older history and visual virtualization. Loading older pages preserves the reader's visible anchor. New live deltas follow the viewport only when the user is already near the end; otherwise a new-activity affordance appears without stealing position.

## Safe Rich Content

Assistant and thinking text support CommonMark, GitHub-flavored tables and task lists, fenced code, syntax highlighting, and copy actions. Returned HTML is disabled rather than trusted. Links expose their destination, use safe protocols, and open external origins with opener isolation. Images or media render only from App-projected safe references or explicit data already allowed by the surface contract; Markdown cannot cause arbitrary credential-bearing fetches.

Tool arguments, results, failures, and custom payloads use bounded structured viewers in a selected context detail or activity view. Thread interaction defaults to semantic summaries and never injects keys as HTML or component names. Large or omitted values show the App's omission fact rather than a browser claim that the value was empty.

## Thread Creation

A new-Thread route begins as a browser-local draft with Project, Agent, Environment profile, ordered Harness Plugins, ordered Environment Run Extensions, ordered MCP servers, optional title, and prompt. A Project-scoped origin initializes that explicit selection; the global action applies ordinary root-Thread creation defaults. Defaults initialize the controls but do not create a Thread.

On first submission, the browser calls `create_thread()` and then submits the prompt to the returned Thread. These are two explicit App operations rather than an invented atomic create-and-run contract. If creation succeeds and prompt admission fails or has unknown outcome, the created Thread remains visible at its canonical route and the prompt draft is restored for reconciliation. The browser never creates another Thread automatically to hide that boundary.

An explicit Create without prompt persists the Thread and opens it idle. Cancelling a browser-local draft has no App effect.

## Composer State

The composer derives its legal mode from the current Thread detail and exact available actions:

| App projection                                      | Composer mode                                                           |
| --------------------------------------------------- | ----------------------------------------------------------------------- |
| No selected deferred requests and no root operation | New prompt                                                              |
| `preparing` operation                               | Submission accepted; cancel when advertised; prompt submission disabled |
| `running` operation with `steer`                    | Steering input plus cancel when advertised                              |
| `running` operation without `steer`                 | Read-only activity plus advertised controls                             |
| Selected suspended continuation                     | Complete decision response; ordinary prompt disabled                    |
| Missing selected resource or invalid composition    | Draft allowed, submission blocked with actionable configuration link    |
| Archived root Thread                                | Read-only until an exact metadata restore succeeds                      |

The browser owns no hidden next-message queue. Pressing send while a Run is active either performs an exact advertised steering operation or is disabled. Text left in a disabled new-prompt draft is not submitted later automatically.

Keyboard behavior is explicit: `Enter` submits a valid single-line action, `Shift+Enter` inserts a newline, and composition events for input methods prevent premature submission. A command remains disabled while its exact request is in flight. The draft clears only after definitive admission returns a receipt; an unknown transport outcome retains the draft but requires reconciliation before another submission.

Project-path completion uses only the App's bounded logical completion query beneath the selected Project roots. Inserting a completion adds ordinary prompt text; previewing a path neither reads its bytes nor grants filesystem authority.

## Thread Configuration and Admission

The composer exposes a compact configuration summary and an expanded editor for Project, Agent, Environment profile, ordered Harness Plugins, ordered Environment Run Extensions, and ordered MCP servers. The Environment control consumes the App's shared profile projection, presents **Full Control** and **Sandbox** before custom profiles, and shows their authority descriptions rather than inferring safety from canonical Host path presentation. The controls start from one `ThreadConfigurationView` version.

A user can either save configuration independently or submit a prompt with one optional patch. Prompt-plus-patch remains one App admission operation: the exact expected configuration version accompanies every non-empty patch, and the returned receipt proves admission only. The UI does not perform a preliminary configuration write followed by a separate prompt when the user chose atomic admission.

Omitted fields preserve the Thread's stored selection. An explicitly empty extension or MCP collection selects none. Reordering an ordered collection is a real patch. The UI labels missing current resources without silently selecting defaults, and it never changes a child Thread's source from a root-only control.

A configuration conflict preserves both the message draft and selected changes. The browser refetches the current configuration, shows which axes changed, and requires the user to reapply or discard the patch. It does not attach the new version to the old selection automatically.

## Root Operations and Control

A successful prompt or deferred-response admission returns an exact process-local receipt. The focused controller associates subsequent preparing/running/terminal activity only with that receipt and any later advertised Run ID. A stale terminal response from an older receipt cannot replace the visible current operation.

Wait is an observation, not a background job. The browser normally follows the focused stream and operation invalidations; it can use a bounded wait request when a command flow needs a terminal response. Steering and cancellation always target the exact receipt shown by the latest App projection. Controls disappear when `available_actions` no longer advertises them, even if the timeline still displays a running-looking block.

Cancellation presentation distinguishes requested, accepted, and terminal cancelled facts. A successful cancel command means the request was accepted, not that cleanup and checkpoint selection have completed.

## Deferred Decisions

A selected deferred request set replaces the normal composer with one complete decision form. Each request retains its exact ID, kind, tool name, safe arguments, and metadata. Approval requests support approve, approve with replacement JSON arguments, or deny with a bounded message. External requests support one JSON result or a bounded denial.

The form requires one valid response for every listed request and submits one batch against the exact continuation ID. Partial sequential submission, duplicate request IDs, an extra response, and ordinary prompt bypass are impossible through the UI. The user can inspect the rest of the Thread while drafting decisions, but a refreshed continuation mismatch preserves the draft only for comparison and requires rebuilding it against the new request set.

Approval is never inferred from dismissing a dialog, pressing Escape, navigating away, or closing the browser. The browser warns before abandoning a non-empty decision draft but cannot convert that warning into a Host response.

## Child Work

A selected child-work context lists child executions under their root lineage with:

- child and parent Thread identities;
- subagent name and selected definition;
- segment and resume relationship;
- saved `persisted_status` and independent `local_status`;
- compact saved activity plus provisional live activity;
- resumability, bounded failure, and exact available actions.

A saved `running` execution with `local_status=unavailable` is shown as interrupted or unavailable, not as actively running. The UI never offers wait, steer, or cancel without the corresponding action. A child can remain active after the parent root Run closes, and the root timeline continues to show that independent activity.

Child steering and cancellation target the exact execution and parent scope. The WebUI displays whether a saved child is resumable but does not invent a standalone child-resume command: linked `resume_subagent` remains an operation of the current parent Harness context. Editing a child Thread configuration for a later linked resume uses its own expected configuration version and does not mutate the parent Thread.

The child detail uses saved compact checkpoints for retained history and detailed live frames for current-process activity. It never reconstructs a child transcript or continuation from compact AG-UI display.

## Working State Tasks

A focused task sheet renders the bounded task summary and pages selected from the current continuation. It preserves task identity, status, owner, dependencies, and authoritative version supplied by the Harness/App projection. Live task events can update provisional presentation, but retained task state reconciles from the selected continuation.

The WebUI does not execute tasks, infer scheduling from dependencies, or provide a browser-only task mutation path. A task is working memory visible through the current Agent interaction, not a durable project-management record.

## Outcome Presentation

The UI does not collapse a root terminal outcome into one green or red label. It presents independent facets:

- Harness execution status and output or failure;
- continuation publication and selection status;
- Environment-state unchanged, published, and failed counts;
- cleanup failures;
- composition and usage summary when available.

Thread interaction presents these as concise human-readable qualifications. Exact continuation identity, receipt correlation, detailed usage, state counts, and timing remain available through the corresponding activity view.

A completed execution whose cleanup or state publication reports a problem remains visibly qualified. A failed continuation selection means the produced output cannot be represented as retained conversation truth. The timeline may show the provisional output for inspection, but a later reload follows the prior selected continuation.

## Thread Metadata

Title and archive actions use the exact metadata version. Inline title editing commits only on explicit submit or focus-confirmed action and supports explicit clearing. Archiving an active root Thread is disabled when the latest projection says it is unavailable and remains subject to App rejection if activity changed concurrently.

Archived Threads leave the ordinary collection but remain queryable through the archive filter. Restoring is an exact metadata mutation. No browser-only hide state substitutes for archive authority.

## Failure Semantics

| Failure                                     | Presentation and recovery                                                                        |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Thread Project missing or deleted           | Keep the Thread reachable through derived navigation and require explicit reassignment           |
| Thread or selected resource missing         | Preserve inspectable history, identify the missing selection, and link to explicit reassignment  |
| Root admission rejected                     | Retain prompt and configuration draft; refresh exact Thread and operation projections            |
| Admission outcome unknown                   | Retain draft, refetch current root activity, and require user confirmation before another submit |
| Steering or cancellation rejected           | Refresh available actions; never target a replacement operation                                  |
| Deferred continuation conflict              | Preserve the draft for comparison, refetch requests, and require a complete new batch            |
| Focused stream reset                        | Mark provisional content reset, establish a new snapshot, and keep retained transcript visible   |
| Saved child running but locally unavailable | Show interruption/unavailability with no inferred controls or takeover                           |
| Continuation or cleanup facet fails         | Present the independent facet rather than flattening the entire outcome                          |

## Invariants

01. Project is the only root-Thread grouping; Recent, search, archive, and unresolved navigation remain derived indexes.
02. The selected root Thread opens directly as the one ordinary conversation and composer surface.
03. Project-grouped visibility of a root Thread is derived from its current sticky Project.
04. A focused root Thread route renders one high-water-bound snapshot and one complete descendant live lineage.
05. Creating a Thread and admitting its first prompt remain two App operations; partial success preserves the created Thread and recoverable prompt draft.
06. Retained timeline, saved child display, and provisional live activity remain visually and semantically distinct.
07. One active root operation permits no hidden queued prompt.
08. Prompt-plus-configuration patch remains one atomic App admission command.
09. Every displayed control derives from the latest exact `available_actions` projection.
10. Deferred decisions submit one complete response batch against one exact continuation.
11. Saved child status never proves current-process activity.
12. Live terminal events never substitute for selected continuation, Environment-state, cleanup, or checkpoint facts.
13. Settled activity can fold for clarity, but exact available detail remains linked to the on-demand activity view and is never reconstructed from summary text.
14. Stream reset discards provisional reduction without damaging retained history or unsent drafts.
15. Safe rendering never executes model, tool, source, or custom-event content.
