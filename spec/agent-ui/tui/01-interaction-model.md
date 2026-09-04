# Terminal Interaction Model

## Design Position

The TUI renders detached Agent UI facts as a semantic, progressively disclosed workstation. It keeps execution control separate from activity presentation: `preparing`, `running`, pending decision, and terminal operation facts determine valid actions, while text, reasoning, tool, task, and child events describe what the user can see.

A dropped or unknown live event can reduce presentation quality but cannot change which command is valid. Every state-changing action passes through the exact App command and correlation required by [Runtime, Async Subagents, and Surfaces](../05-runtime-subagents-and-surfaces.md).

## Terminal State

The terminal owns only bounded presentation state:

```python
class TerminalState:
    lifecycle: Literal["starting", "ready", "closing", "failed"]
    mode: Literal["focus", "workbench"]
    launch_project_id: str | None
    project_filter_id: str | None
    focused_thread_id: str | None
    selected_workbench_thread_id: str | None
    overlays: tuple[OverlayState, ...]
    drafts: tuple[DraftState, ...]
    thread_views: tuple[ThreadViewState, ...]
    notifications: tuple[TerminalNotice, ...]
```

This schema is conceptual and is not an App surface or persistence format. `launch_project_id` is the App-resolved Project used for new drafts and the default Workbench Project filter in this TUI lifetime; it does not override an existing Thread's stored Project. `project_filter_id` is either that Project or `None` for All Projects and never changes an existing Thread's stored Project. Drafts, overlay stacks, selection, follow-latest state, expanded blocks, and current-process review acknowledgements may disappear on process exit.

The focused Thread derives one control mode from detached App facts:

| Control mode      | Authoritative fact                                                  | Composer behavior                                                |
| ----------------- | ------------------------------------------------------------------- | ---------------------------------------------------------------- |
| Draft             | No persisted Thread exists                                          | Submit creates the Thread and admits its first prompt            |
| Idle              | Thread has no active root operation and no pending requests         | Submit admits a new prompt                                       |
| Preparing         | Exact receipt is preparing                                          | Input remains a local draft; cancel is available when advertised |
| Running           | Exact receipt has a running stream                                  | Submit offers steering to that receipt                           |
| Awaiting decision | Selected continuation has pending requests                          | Composer draft is preserved and replaced by decision collection  |
| Cancelling        | A local cancellation acknowledgement is pending terminal settlement | New control is disabled; status remains non-terminal             |
| Unavailable       | Required App projection or live recovery is incomplete              | State-changing input is disabled until refetch succeeds          |

`thinking`, `writing`, `using tools`, and similar labels are activity hints. They never become control modes.

## Semantic Timeline

The focused timeline is a sequence of stable bounded blocks:

```python
class TimelineBlock:
    block_id: str
    run_id: str | None
    thread_id: str
    execution_id: str | None
    kind: Literal[
        "user",
        "assistant",
        "reasoning",
        "tool",
        "child",
        "task",
        "notice",
        "failure",
    ]
    status: Literal["provisional", "running", "closed", "failed", "cancelled"]
    version: int
    summary: str | None
    source_text: str | None
    detail_available: bool
```

This conceptual TUI model is derived from retained transcript entries, focused live events, child-execution projections, terminal operation views, and safe App-supplied review projections. It is not serialized as Agent history.

Block identity comes from source Thread, Run, part, tool-call, execution, task, and terminal correlation. The reducer never joins blocks by equal text, approximate timestamp, tool name alone, or screen position.

### Default Disclosure

| Block                 | Default presentation                 | Detail presentation                                         |
| --------------------- | ------------------------------------ | ----------------------------------------------------------- |
| User                  | Complete bounded prompt              | Referenced paths and submission status                      |
| Assistant             | Streaming or closed Markdown         | Copy and block navigation actions                           |
| Reasoning             | Collapsed one-line status or summary | Bounded scrollable plaintext when policy exposes it         |
| Read/search/list tool | One compact row                      | Arguments, result summary, duration, and safe output        |
| Shell tool            | Command and current status           | Bounded output with follow/pause and terminal status        |
| Edit/write tool       | Paths and change count               | App-supplied unified or split diff when available           |
| Child execution       | Name, task, and status               | Child activity, latest output, and exact available controls |
| Task                  | Current task counts and changed item | Task graph and bounded detail                               |
| Failure               | Expanded safe summary                | Structured safe details and retry hint                      |
| Notice                | One bounded row                      | Structured source detail when useful                        |

Routine successful tools collapse after the enclosing turn closes. Failed, denied, interrupted, or still-running tools remain expanded enough to explain the state. Tool result bodies, large diffs, task graphs, and child detail mount only when selected.

Encrypted reasoning is never rendered. When reasoning content is not exposed by policy, the TUI may show only a neutral activity label backed by a live lifecycle hint; it never synthesizes or summarizes hidden reasoning.

### Turn Folding

While a turn runs, its reasoning, tools, task updates, and child launches remain individually visible. After a successful closed boundary, the TUI may fold intermediate activity into a summary such as `5 tools · 2 tasks · 1 child`, while retaining the final assistant answer. A failed turn without a final answer remains expanded.

Counts, duration, tokens, cost, context percentage, and completion status appear only when the detached source supplies the required complete numerator, denominator, and terminal fact. The TUI never estimates a context percentage from unrelated usage.

## Live Presentation and Reconciliation

A focused watch starts before its snapshot is assembled. The snapshot supplies the selected continuation and child state; while later events remain buffered, the TUI loads the latest bounded retained transcript page through a cursor bound to that continuation. It renders those values, then consumes only later events from the same epoch and cutover sequence.

Live events update provisional blocks. The projection scheduler may coalesce consecutive compatible text or reasoning deltas, but it preserves semantic boundaries for:

- user input;
- text or reasoning start and end;
- tool completion and result;
- child admission and terminal state;
- task mutation;
- decision suspension;
- root terminal outcome;
- failure or cancellation.

After a root operation settles, the controller applies the terminal operation view and reloads the selected retained projection when continuation selection succeeded. It replaces correlated provisional blocks with closed blocks and independently presents:

- Harness execution status;
- continuation publication and selection status;
- Environment-state publication summary;
- cleanup failures;
- any output omitted by surface bounds.

A visually complete live answer is not presented as safely resumable until continuation selection is confirmed. When continuation selection fails, the answer remains visible as unretained current-process output with a prominent warning.

A live gap, expired cursor, or changed epoch pauses provisional updates, keeps the composer in the control mode supported by current App facts, and establishes a fresh focused snapshot. It does not clear the timeline or infer a terminal result.

## History and Reading Position

The initial Focus load combines the focused snapshot with the latest bounded transcript page queried against the snapshot's selected continuation. A transcript-query failure resets only that retained-page load and does not change the focused watch epoch, cutover, or App truth. Entries within each page remain in chronological order. Older pages load backward and prepend without changing the selected semantic block or its top-line viewport anchor.

The timeline follows new output only while the viewport is at its tail. Scrolling upward disables follow-latest and shows a visible pending-line count. An explicit action returns to the latest block. New output never steals the reading position merely because the Thread is focused.

Mounted timeline blocks remain bounded around the active turn and viewport. Evicted retained blocks can be loaded again from App projections. Eviction never removes open blocks, the active decision, or the anchor required to preserve reading position.

## Composer

The composer is multiline and owns one bounded local draft per recently visited root Thread plus one new-Thread draft. Draft state includes text, cursor, exact Project-path and Skill references, and editor revision. Structured references contain no eagerly loaded Project or Skill file bytes or credential objects, but arbitrary user-authored draft text is potentially sensitive.

Default editing behavior is:

| Input               | Action                                                                                                     |
| ------------------- | ---------------------------------------------------------------------------------------------------------- |
| `Enter`             | Submit the current context-valid action                                                                    |
| `Alt+Enter`         | Insert a newline using a terminal-portable modified-key path                                               |
| `Shift+Enter`       | Insert a newline when the terminal reports the modifier distinctly                                         |
| `Ctrl+P`            | Open the command palette                                                                                   |
| `Ctrl+O`            | Perform the context-valid Focus/Workbench toggle                                                           |
| `Ctrl+N`            | Start a new draft Thread                                                                                   |
| `Ctrl+C`            | Request cancellation of the focused active root operation; otherwise close the top cancellable interaction |
| `Esc`               | Close the top overlay or leave block focus without cancelling a Run                                        |
| `Tab` / `Shift+Tab` | Move through visible panes or decision controls                                                            |

`Ctrl+O` follows an explicit routing rule:

| Current state                                  | Action                                                                      |
| ---------------------------------------------- | --------------------------------------------------------------------------- |
| Focus                                          | Open Workbench and retain the current Thread as the previous focus          |
| Workbench with an accessible previous focus    | Return to that Thread without changing the selected Workbench row           |
| Workbench without an accessible previous focus | The action is hidden or disabled; `Enter` explicitly opens the selected row |

Opening a selected Workbench row always makes that row the focused Thread. It does not overload the return-to-previous-focus action.

The footer always shows the effective context-sensitive actions, so the user does not need to memorize bindings. An external editor action resolves `$VISUAL` and then `$EDITOR`, parses the selected value into an executable and arguments without an intermediary shell, and reports the action as unavailable when neither variable supplies a usable command. It creates an owner-private temporary file outside Project roots and the Agent UI data root, writes only the bounded current draft, suspends full-screen terminal presentation, launches the editor with the ordinary terminal attached, waits without blocking App-owned execution, reads a bounded result, and restores the TUI. The original draft remains intact when editor launch or result reading fails. The action never logs the file content or path and performs best-effort deletion after normal or exceptional editor exit. It does not expose Agent UI storage or Project file authority. App work can continue while the editor owns the terminal; after resuming, the TUI reconciles current App projections before presenting new activity as complete.

### Submission Semantics

When idle, non-blank input is a new prompt. The composer clears only after `submit_thread()` returns an exact receipt. Admission rejection restores the complete draft and references.

When running, non-blank input is labeled **Steer current run** and is offered only to the exact current receipt. The composer clears only after an accepted steering acknowledgement. A race with Run completion restores the draft and explains that no steering input was accepted; it never silently becomes a new turn.

During preparation or cancellation, ordinary input remains a draft and is not queued. When awaiting a decision, ordinary prompt submission is disabled until the selected request set is answered or denied.

### Project Path and Skill References

Typing `@` opens a bounded fuzzy completion over logical paths beneath the focused Thread's stored Project roots or, for a new draft, the launch-resolved Project roots. Project-path completion is supplied through an App-owned safe query; the TUI does not crawl roots through an independent filesystem path.

Selecting a Project result inserts an unambiguous logical path reference into the prompt. It does not eagerly attach file bytes, change or reorder the Project, grant a new mount, or bypass the Agent's file tools. Duplicate root-relative names include their mount label, paths outside the selected Project do not appear, and the selected Agent decides whether it needs to read the path.

Typing `$` at a token boundary opens a bounded fuzzy completion over the effective conflict-resolved Skill catalog. `/skills` opens the same read-only picker without inserting anything until the user selects one result. The App supplies the catalog projection through the same Environment-routed source composition used by the selected Agent: a draft or idle Thread uses its effective next-Run configuration, while active steering uses only the current Run's frozen catalog. When the selected Agent does not include the Skills Capability or no effective catalog is available, the picker explains that state and offers no fabricated candidates.

Selecting a Skill inserts a visible `$<skill-name>` marker and records the exact catalog identity as a typed composer reference. An exact manually typed marker becomes a Skill reference only after App resolution; unresolved dollar-prefixed text remains ordinary user input. A resolved reference is an explicit current-input request to use that Skill. Before prompt admission or steering acceptance, the App verifies every typed reference against the applicable effective or frozen catalog; an unavailable or stale reference rejects that action and preserves the complete draft for correction.

A Skill reference does not eagerly attach `SKILL.md` bytes, change Skill source precedence, narrow the complete run-frozen catalog, suppress implicit use of another available Skill, mutate Agent or Thread configuration, grant Environment access, or install a package. The selected Agent still activates and reads the Skill through ordinary Skills Capability routing and Environment file operations. Several Skill references can coexist in one input. Skill names never become dynamic slash commands, and `@` remains limited to Project logical paths rather than sharing an ambiguous namespace with Skills, Agents, MCP resources, or eager file attachments.

## Commands and Palette

The command palette is the complete discovery surface for secondary actions. Slash commands provide concise aliases for frequent terminal-local actions:

| Command        | Behavior                                                                                                                                                                                                                         |
| -------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/new`         | Start a new terminal-local root-Thread draft under the launch Project. Nothing is persisted until its first prompt is submitted successfully, and the action is unavailable when no launch Project was resolved.                 |
| `/workbench`   | Enter the persistent Workbench top-level mode under the current terminal-local Project filter or All Projects. It is not a Focus toggle and does not cancel or otherwise change App-owned work.                                  |
| `/threads`     | Open the transient searchable root-Thread picker with an explicit Project/All Projects toggle. Selecting a result opens that Thread in Focus; dismissing the picker restores the underlying mode and local presentation state.   |
| `/skills`      | Open the read-only effective Skill picker used by `$` completion. Selecting a result inserts one exact Skill reference into the composer; the command does not edit sources, install packages, or change Thread configuration.   |
| `/status`      | Open a read-only status surface for the App, focused Thread, operation, selected continuation, accepted configuration, and live delivery. It performs no refresh, mutation, or execution action merely by being opened.          |
| `/details`     | Toggle the terminal-local default disclosure of tool details in the focused timeline. It changes presentation only and does not alter tool execution, retention, or policy.                                                      |
| `/thinking`    | Toggle the terminal-local default disclosure of reasoning content that the App is permitted to expose. It never enables reasoning, changes Model effort or Agent configuration, or reveals encrypted or policy-hidden reasoning. |
| `/agent`       | Open the accepted Agent selector for a new draft or the current Thread. A persisted change uses the exact Thread configuration version, and a change made during an active Run applies only to the next Run.                     |
| `/environment` | Open the accepted Environment profile selector, including the release-owned Full Control and Sandbox choices. It changes draft or sticky Thread selection only and never mutates the Environment captured by an active Run.      |
| `/extensions`  | Inspect and select accepted Harness Plugins, Environment Run Extensions, and MCP servers for a draft or Thread. It exposes no package installation, resource authoring, or source mutation action.                               |
| `/editor`      | Suspend full-screen presentation and edit the bounded current composer draft through `$VISUAL` or `$EDITOR`. Returning from the editor restores the TUI and preserves the original draft if launch or result reading fails.      |
| `/cancel`      | Request cancellation against the exact eligible focused root receipt. It does not infer a target from recent activity, cancel child work independently, or turn cancellation acknowledgement into a terminal result.             |
| `/archive`     | Archive an idle focused root Thread through metadata compare-and-select. It does not delete retained history and reports a conflict instead of overwriting newer metadata.                                                       |
| `/help`        | Open the context-aware binding, command, and input-routing reference. Closing it restores the underlying mode, draft, selection, and reading position.                                                                           |
| `/exit`        | Begin explicit TUI and App shutdown. When process-local work is active, the command explains that the work cannot continue after App exit and requires confirmation before interruption.                                         |

A recognized slash command runs on the terminal control plane and is not sent to the model. An unrecognized slash-prefixed string remains ordinary prompt or steering text. Built-in commands cannot be shadowed by Agent or resource configuration. The command registry is a fixed TUI control surface and does not dynamically import Skill names, MCP prompts, or configuration Markdown into the slash namespace.

Thread configuration presents the stored Project as read-only context. Selectors list only accepted Agents, Environment profiles, Harness Plugins, Environment Run Extensions, and MCP servers and use the current exact Thread configuration version. The Environment selector presents the release-owned **Full Control** and **Sandbox** entries from the App projection before custom profiles and shows their authority descriptions; it never relabels Full Control as “Native” or infers safety from canonical Host path presentation. Moving a Thread to another Project remains a WebUI operation; the TUI never exposes Project or root mutation. A conflict refetches and displays the newer state instead of overwriting it. Changing a supported sticky selection during a Run is clearly labeled **applies to the next Run**; it cannot mutate the captured active composition. Direct Model selection is absent because the Agent resource owns its Model. The selector can show the Model resolved by the selected Agent as read-only context.

Selectors and status views can expose accepted-generation diagnostics, installed-catalog availability, and the App-approved configuration source location. They do not expose resource create, duplicate, source-edit, delete, import, package-install, or package-upgrade actions. The TUI never turns an unavailable selection into an inline resource editor; the user resolves desired-resource or Skill changes through direct files, the CLI, or the WebUI and then reloads or refreshes the App-owned catalog.

## Decisions

A pending decision is owned by the exact selected suspended continuation. The App supplies a detached discriminated presentation for:

- structured `ask_user_question` requests;
- function-tool approval, including safe arguments and any available review data;
- generic external tool result or denial.

The TUI never infers the request kind from display text alone and never imports native deferred request objects.

### Structured Questions

One deferred external call can contain one to four questions. The TUI presents one question at a time, supports single selection, multiple selection, and free text under the supplied schema, and shows progress through the complete set. Moving backward preserves prior answers.

The last confirmation validates every required answer. The controller then constructs the one response item for that external request. Several pending deferred requests remain one ordered decision sequence and are all answered before App submission.

### Approvals

A simple approval shows the tool, effect summary, resources, arguments, and these explicit outcomes:

- approve with original arguments;
- approve with an App-supported argument override;
- deny with an optional bounded reason.

An edit or broad operation opens Review. A diff is shown only when the App supplies a safe bounded review projection. The TUI does not open or mutate Project files to manufacture pre-approval truth. Missing review data falls back to the exact safe arguments and scope rather than a fabricated diff.

No command, default focus, timeout, exit path, or render failure approves a request automatically. Escape leaves the decision pending; an explicit deny action supplies denial.

### Complete Batch

The TUI accumulates local responses to every pending request, then calls `respond_thread()` once with:

- the exact selected continuation ID;
- exactly one correctly typed response per pending request ID;
- no additional request ID.

A stale-continuation or completeness conflict preserves the user's local answer draft separately, reloads the current requests, and requires explicit review before any resubmission. The TUI does not map answers onto a changed request set by position or text.

## Tool and Change Review

Tool rows use stable public tool-call correlation. Start, arguments, result, extra semantic events, failure, and timing are merged only when their identifiers agree.

The detail viewer supports:

- formatted bounded JSON arguments and results;
- selectable and copyable text;
- shell-output follow and pause without pausing the process;
- file-change summaries from confirmed filesystem events;
- unified diff at every width;
- side-by-side diff only when width and source line lengths remain usable;
- safe omission markers when App limits removed content.

Diff display is presentation only. Apply, rollback, or edit actions appear only when a separately owned App command provides those semantics. The TUI does not turn a visual diff into an implicit filesystem transaction.

## Tasks and Child Work

Working State tasks appear in the inspector and as compact timeline updates. The TUI preserves task status, owner, dependencies, and authoritative version supplied by the Harness/App projection. It does not reinterpret tasks as an executable plan or add plan approval semantics.

Async child executions appear under their immediate parent and roll up under the root Thread in Workbench. A child row distinguishes:

- persisted status: `running`, `succeeded`, `failed`, `cancelled`, or `lost`;
- current-process local status: `active` or `unavailable`;
- exact available actions.

A saved `running` child without local authority is labeled **unavailable**, never **still running**. Steering and cancellation target only the exact child execution ID and are shown only when the App advertises those actions. Opening child detail never creates an independently continuable root surface.

## Workbench Attention

Workbench ranks non-archived root Threads by these presentation groups:

1. selected continuations awaiting a decision;
2. current-process root failure, cancellation, or lost/failed child work requiring inspection;
3. current-process completed work not yet acknowledged in this TUI lifetime;
4. active root or child work;
5. idle Threads.

Within one group, rows order by descending relevant update time and stable Thread ID. The launch Project ID is the default App query filter; All Projects omits that filter. The TUI never expands this into another grouping model. Project appears as compact row context when several Projects can be present.

Acknowledged completion is terminal-local presentation state. It is lost on restart and never written into Thread metadata or continuation state. Pending decisions and durable child terminal facts remain visible after restart because their owning App projections retain them.

A Workbench row contains only bounded summary data: title, Project, Agent, Environment, age, root activity, pending-decision summary, child roll-up, latest safe activity, and available actions. Selecting a row loads bounded preview detail. Only opening Focus establishes the full root-lineage watch and timeline.

Context-sensitive Workbench input follows the same rules as Focus:

- idle Thread: submit a new prompt;
- running Thread: steer the exact active receipt;
- awaiting Thread: answer the exact decision sequence;
- new-work composer: create another root Thread under the launch-resolved Project and submit its first prompt; disable creation when no Project was resolved, including in an All Projects fallback with no launch Project.

Workbench owns no queued delivery. A dispatch can remain on Workbench or open Focus as two explicit actions, not as a modifier-dependent hidden distinction.

## Failure and Conflict Presentation

| Condition                       | Presentation behavior                                                           |
| ------------------------------- | ------------------------------------------------------------------------------- |
| Prompt admission rejected       | Restore draft, remove or mark provisional echo, show safe error                 |
| Steering rejected               | Restore draft; never submit it as a later prompt automatically                  |
| Cancellation acknowledged       | Show cancelling until terminal operation fact arrives                           |
| Continuation selection failed   | Preserve visible output as unretained and disable false resume claims           |
| Thread configuration conflict   | Refetch current version and keep the user's proposed patch for comparison       |
| Deferred response conflict      | Refetch exact request set and require explicit review                           |
| Live cursor gap or epoch change | Show degraded-live state and establish a new focused snapshot                   |
| Unknown custom live event       | Render a bounded neutral notice or omit presentation; do not fail execution     |
| Omitted or truncated payload    | Display the omission explicitly; never imply the complete value was shown       |
| Rendering failure               | Isolate the block, log at the executable boundary, and keep App truth unchanged |

## Invariants

01. Control modes derive from App authority, not observed activity labels.
02. Timeline blocks use exact correlation and remain presentation-only.
03. Live content is provisional until terminal and retained reconciliation.
04. History opens at the latest page and prepends older pages without losing the reading anchor.
05. Prompt, steering, cancellation, and deferred response are distinct intents.
06. The composer clears only after the relevant App boundary acknowledges acceptance.
07. No input is silently queued for a later turn.
08. Decisions submit one exact complete response batch and never auto-approve.
09. Tool, diff, task, and child detail use progressive disclosure and bounded App data.
10. Workbench attention is useful presentation, not another scheduler or durable state machine.
11. The Project/All Projects filter changes terminal queries only and never mutates Thread configuration.
12. `$` references exact App-resolved Skills for the current input without changing Skill sources, catalog selection, Thread configuration, or Environment authority.
