# Runtime, Async Subagents, and Surfaces

## Design Position

`AgentUiApp` is the only Agent UI application boundary. It runs configuration, catalog, Project, Thread, Harness, Model, Capability, Plugin, MCP, Environment, subagent, and observation behavior in one process. CLI, WebUI, and model-visible Thread tools are thin adapters over its typed commands and queries.

Agent UI implements the Harness `SubagentOperator` contract as `AgentUiSubagentOperator`. It persists each async child as an ordinary child Thread, runs each delegate or resume as an independent segment, saves exact checkpoints, and returns bounded saved execution views.

Agent UI is not a Python package installer or upgrade supervisor. Already imported extension and Capability code remains fixed for the App lifetime. Background shell processes remain Run-owned and have no cross-Run process manager or wake path.

## Application Ownership

```mermaid
flowchart TB
    CLI[CLI]
    Web[Web adapter and bundled WebUI]
    ThreadCapability[Root Thread capability]

    subgraph App[AgentUiApp]
        Files[Configuration files and CAS]
        Catalogs[Capabilities and extensions]
        Projects[Projects]
        Threads[Thread commands and queries]
        Runs[Run admission and coordination]
        Operator[AgentUiSubagentOperator]
        Environments[Provider adapters and state]
        Live[Live presentation hub]
        Store[SQLite and immutable objects]
    end

    subgraph Runtime[Fresh Run values]
        Composition[Resolved Run composition]
        Model[Model and credentials]
        Features[Capabilities, Plugins, and MCP]
        Adapters[Environment adapters and Run Extensions]
        Harness[Harness Run]
        Observer[HarnessAguiObserver]
    end

    CLI & Web & ThreadCapability --> App
    App --> Runtime
    Harness --> Observer --> Live --> CLI & Web
```

The App owns:

- stable multi-file loading, accepted-generation selection, diagnostics, and expected-digest mutations;
- Capability and three-plane extension catalog projection;
- Project and configured-resource queries;
- Thread creation, metadata and configuration mutation, keyset queries, and transcript projection;
- process-local root admission, receipt correlation, execution, deferred response, waiting, cancellation, and steering;
- immutable Run composition and continuation publication;
- Project-root Environment binding and state lifecycle;
- async child admission, execution, checkpointing, query, wait, steering, cancellation, and linked resume;
- detached presentation, root-lineage live delivery, summary invalidation, and bounded graceful shutdown.

It does not expose database sessions, storage paths as authority, native Models, credentials, Provider objects, Environment adapters, Harness contexts or results, Pydantic AI message objects, tasks, locks, callbacks, or Python exceptions through a surface API.

## App Lifetime

One App lifetime:

1. resolves the config path and bootstrap data-root locator, then configures logging at the executable boundary;
2. opens and migrates local storage without depending on a valid root YAML;
3. loads or restores the last accepted file generation and reports current source diagnostics;
4. initializes Capability and extension catalogs plus release-owned Model, MCP, and Environment adapter integrations;
5. starts bounded configuration change observation;
6. attaches the selected CLI or Web surface;
7. serves commands until shutdown;
8. stops new admissions, requests cancellation after a bounded graceful-drain interval, joins owned tasks, and then closes collaborators.

The shutdown timeout bounds graceful draining before cooperative cancellation. Agent UI retains structured ownership and keeps storage open until its tasks exit, so it does not claim a hard return deadline against arbitrary trusted Python code that ignores cancellation or creates an unbounded shield. Built-in collaborators and extension cleanup paths have their own finite bounds; a process supervisor owns any required hard process-termination deadline.

A source change triggers a stable complete-tree candidate load. Invalid intermediate saves do not replace the accepted generation. Module import starts no task, process, listener, or database connection.

## Root Run Coordination

### Process-local Operations

One App admits at most one root operation for a Thread. Another root submission while that operation is preparing or running is rejected rather than queued. Admission returns a detached receipt immediately; the receipt ID is unpredictable, unique within the App lifetime, and is the exact correlation used by active queries, waits, steering, and cancellation.

A root operation progresses from `preparing` to `running`, then to `completed`, `suspended`, `failed`, or `cancelled`. Preparation failure is `failed`; `completed` or `suspended` requires the corresponding acceptable continuation to be selected. Its view can acquire a Harness Run ID after native stream construction and retains separate Harness execution, continuation-selection, Environment-state, and cleanup facts. The App retains the detached terminal view for the remainder of its lifetime, but persists neither the receipt nor the submitted input. A process restart therefore exposes the Thread and its last selected continuation but no prior operation, wait target, or control authority.

Cancellation is accepted against the exact receipt during both preparation and Harness execution. During preparation it cancels the App-owned operation scope; once a stream exists it also requests cancellation from that exact stream. Steering is available only while the receipt names the current running stream. A stale receipt can neither steer nor cancel a later Run on the same Thread. Wait observes the operation's state transition and has no effect on execution.

For an admitted prompt or deferred response, the App:

01. loads the root Thread, optional patch, required expected configuration version for a non-empty patch, and selected continuation;
02. validates and commits the sticky configuration update when present;
03. validates that the selected continuation accepts the input kind and, for a deferred response, still matches the caller's expected continuation ID;
04. captures the current accepted generation and selected Project roots;
05. resolves the exact Agent graph, Capabilities, tool visibility, Harness Plugins, MCP servers, Environment Provider, and Environment Run Extensions;
06. publishes the immutable resolved Run composition;
07. creates fresh native collaborators; each subscription-backed Model request resolves and refreshes its compatible OAuth credential when needed;
08. starts one Harness stream and observer from the selected `HarnessState`;
09. forwards public live events best effort;
10. finalizes Environment adapters and publishes changed state;
11. publishes and compare-and-selects an acceptable complete or suspended continuation;
12. selects a detached terminal operation outcome with independent execution, continuation, Environment-state, and cleanup facts.

No database transaction spans file I/O, catalog import, native construction, or steps 7 through 11. If capture fails, an already committed explicit Thread patch remains the Thread's desired next state and the operation reports why it could not start.

### Root Deferred Response

A suspended root continuation exposes a bounded detached request list. Each item has its tool-call ID, request kind, tool name, JSON arguments, and presentation-safe metadata. An ordinary prompt cannot skip a selected deferred request set.

A response command names the exact selected continuation and supplies exactly one response for every pending item. Approval responses approve, optionally with replacement JSON arguments, or deny with a bounded message. External-call responses supply a JSON result or a bounded denial. The App rejects stale continuation IDs, missing or additional IDs, duplicate IDs, kind mismatches, and values that cannot be represented by the corresponding native deferred result.

Only the App reconstructs the native deferred request and result batch. A response is a new root operation with fresh Run composition and collaborators; it does not reuse the suspended operation's runtime objects. The selected suspended continuation remains current unless the response operation publishes and compare-and-selects another acceptable continuation.

## Agent UI Subagent Operator

### Admission and Identity

The Harness resolves the selected child roster entry before calling the operator. The operator creates a child Thread whose discriminated Agent-resource or Markdown-subagent source comes from that entry. Project, Environment profile, and Environment Run Extensions initialize from the parent Run capture. Agent-resource children use their own Plugin and MCP defaults when present; Markdown children inherit the parent capture's exact Plugin and MCP lists.

`delegate`:

1. verifies parent Thread and Run correlation;
2. creates the child Thread and exact sticky configuration;
3. captures and publishes the child Run composition;
4. commits segment zero as `running` before returning its execution ID;
5. starts the segment under App ownership.

A surface can update the retained child Thread through the ordinary required-version configuration command before resume. `resume_subagent` then loads that Host-authorized sticky configuration, requires a selected terminal child checkpoint, increments `segment_index`, captures the new composition, commits the execution, and starts it. The new composition need not equal either the one that produced the prior checkpoint or the current parent roster definition. Harness still requires the same stable roster name and current plan context. Agent UI reapplies that roster edge's Identity policy to the selected replacement definition and intersects the plan usage ceiling with the replacement's own limits; the model cannot select the replacement Agent source.

An accepted child execution can outlive the parent Run. Parent closure never cancels it by implication. App shutdown is the process-local ownership boundary.

### Child Run Construction

Each segment receives:

- the child Thread's resolved Agent-resource or Markdown-subagent graph and Capabilities; a Markdown source resolves its explicit inheritance from the parent Run authorizing that linked admission;
- fresh Model, Harness Plugin, and MCP collaborators;
- captured Project roots and Environment profile selection;
- fresh Provider runtimes, Environment adapters, and Environment Run Extensions;
- the child Thread's newly published empty `initial_state` for delegate or selected child checkpoint state for resume;
- one `HarnessAguiObserver` bound to the child Thread and Run.

No parent `AgentContext`, entered Environment facade, live state coordinator, Model client, task, callback, or shell-process authority crosses into the child.

### Observation and Checkpointing

The operator consumes ordered public Harness stream items and compacts them into the bounded display owned by [Local Storage and Recovery](03-local-storage-and-recovery.md#compact-child-display). Closed activity is published live only at closed boundaries. Terminal success is acknowledged only after Environment cleanup, terminal checkpoint publication, and atomic head selection.

Agent UI performs no automatic parent wake Run after child completion. A connected surface receives live completion, and a later parent Run reconciles through `subagent_info` or `wait_subagent` against saved heads.

### Deferred Requests

A child suspension is not forwarded to the user or parent Thread. The operator supplies the complete denial/no-response values required by the exact request set and continues through normal Harness continuation. The continuation uses a fresh child `run_id` and observer inside the same segment. Failure to continue safely produces explicit child failure.

### Queries and Control

The public execution view contains execution, root lineage, parent Thread, child Thread, child Run, segment, opaque composition identity, persisted status, bounded failure, resumability, compact activity, and current-process control availability. Persisted status and local activity are separate fields: a saved `running` fact can be locally `active` or `unavailable`. Available actions are derived from the exact local segment and never inferred from the saved status alone. The view contains no raw unbounded output or private state.

Steering, cancellation, and bounded live waiting operate only when the current App owns the segment in its process-local registry. A saved `running` value without a matching local runtime is inspectable but grants no control. Execution references are scoped to their originating parent relationship, and a stale execution ID cannot control a later segment.

### Loss and Retention

Orderly shutdown requests cancellation for locally owned segments. A durably completed cancellation becomes `cancelled`; a segment that cannot reach a terminal checkpoint becomes `lost`. Abrupt loss can leave a saved `running` head. Another App does not infer liveness, replay, or take over it.

Linked resume is permitted from a successful terminal execution whose exact selected checkpoint remains resumable, whose stable child roster name still resolves in the current parent, and whose current Host policy authorizes the operation. Compatibility applies to `HarnessState` schema and current selected component state, not equality with the prior Agent definition or Run composition.

## Detached Surface Contracts

All Thread, root-operation, child-execution, and presentation command results, query results, and stream items are strict, frozen, bounded, serializable values. A returned collection is immutable and every nested mutable payload is copied. Opaque digests or IDs can correlate a later command, but a surface never receives an immutable-object path, object kind, or storage read authority.

Thread queries provide:

- a summary containing identity, parent identity, metadata version, title, archive state, timestamps, sticky configuration, selected-continuation state, and current-process root activity;
- a detail containing the summary, selected continuation ID, deferred request projection, and available actions;
- a transcript page of typed presentation entries rather than serialized native Pydantic AI messages;
- a child execution page whose durable status and current-process control availability are distinct.

Thread and transcript pages use opaque keyset cursors bound to the query shape and deterministic sort key. Thread ordering is descending `(updated_at, thread_id)`; transcript ordering follows immutable message position. A newer insertion does not shift unaffected entries across an existing page boundary. Updating a Thread can move it across that boundary, so summary invalidation prompts a fresh first-page query. Invalid, mismatched, or expired cursors fail explicitly. Project recency is aggregated over all associated non-archived Threads in storage rather than a bounded Thread page.

Thread title and archive state share a metadata head independent from sticky configuration. A metadata mutation supplies its exact expected metadata version and changes title, archive state, or both atomically. Archiving an active root Thread is rejected. Unarchiving is valid, and a title can be explicitly cleared. Child metadata is managed only through parent-scoped child operations.

Failures are presentation-safe structured values with bounded code, message, details, and retry hint. Root operation outcomes contain scalar or JSON output, status, usage projection, continuation selection, Environment-state publication summaries, and cleanup failures; they never contain a native `HarnessRunResult`, `HarnessState`, deferred request object, or `Exception`.

## Root-only Thread Capability

The resolved root Agent receives one Agent UI-owned Toolset with operations conceptually equivalent to:

```python
list_threads(query: str | None = None, cursor: str | None = None, limit: int = 20)
get_thread(thread_id: str, history_cursor: str | None = None, history_limit: int = 50)
run_thread(thread_id: str, prompt: str)
steer_thread(thread_id: str, message: str)
```

`run_thread` accepts only a root Thread, uses that target's sticky configuration, and accepts no Project roots or configuration changes from model arguments. It submits through the same receipt boundary and waits for the detached terminal outcome. Async children continue through `resume_subagent`, which retains the current parent roster and delegation ceilings. `steer_thread` resolves the target's current receipt once and controls that exact receipt; it never falls through to a replacement Run. Same-active-Thread recursive run or steer is rejected.

The Toolset appears only on root invocation. Children cannot obtain it through Markdown, Capability selection, Plugin contribution, or tool visibility.

## Live Presentation

Each App lifetime has an opaque live epoch and monotonic sequence. Each root or child Harness Run has one observer. A detailed live event identifies the root Thread, immediate parent Thread when present, producing Thread, Run kind, Run ID, and child execution ID when present. A subscription focused on one root receives that root's entire descendant lineage; child events are not hidden merely because their producing Thread ID differs from the root.

The detailed live hub performs bounded best-effort fan-out and retains only a small in-memory ring. A slow subscriber can lose events and a reconnect outside the retained range receives an explicit reset requirement. Epoch mismatch likewise requires a new snapshot. A slow or disconnected subscriber never blocks execution, state publication, or continuation selection.

A focused watch establishes its subscription and sequence cutover before assembling detached Thread, child, deferred, and local-activity projections. Events after the cutover remain buffered while the snapshot is read. The returned snapshot names the epoch and cutover sequence; the surface renders it, then consumes only later events from that subscription. This prevents a state transition between query and subscription from disappearing, without treating the live ring as durable history.

A separate lightweight App-wide stream emits bounded invalidation hints for configuration, catalog, Project, Thread metadata/configuration/continuation, root operation, and child execution changes. Each hint identifies only the affected summary scope needed for refetch. It carries no transcript or checkpoint payload and is not durable truth. A surface that misses hints refetches its summaries.

Retained transcript comes from selected continuations and child compact checkpoints. Current-process activity is merged only for presentation and never written back as continuation truth outside its owning checkpoint path.

## CLI

The CLI supports direct execution and focused validation or management without requiring exhaustive CRUD commands:

```text
a13n-ui
  run ...
  config validate
  config show
  config import-subagents ...
  project ...
  thread ...
  doctor
  web
```

Editing the root YAML, resource YAML, or canonical Markdown directly is a complete management path. Commands that mutate files consume expected source digests and use the same no-clobber boundary as WebUI operations.

Subagent import offers Claude Code, Cursor, and Codex detection, preview, dry-run, and explicit apply. It writes canonical Markdown only and never modifies source-product files.

Model account commands inspect compatible Codex or Grok login status and can start an explicit login, reauthentication, account switch, or shared logout under [Model Authentication and Compatible Account Stores](02a-model-authentication-and-account-stores.md). Existing usable product login is preferred over another browser flow.

## WebUI

The bundled WebUI uses one loopback HTTP/SSE adapter over detached App commands and queries. It supports:

- source-tree and validation diagnostics;
- Capability and extension discovery;
- expected-digest resource editing;
- Project, Agent, Plugin, Environment, MCP, and global-default management;
- sticky per-Thread selection and toggles;
- root and child interaction, inspection, cancellation, steering, and live updates.

Every editable response includes its current source digest. A stale mutation conflicts instead of knowingly replacing a newer manual or browser edit. The browser receives no native filesystem capability or arbitrary Host path API; Project paths enter only through validated resource mutations.

Unknown API or health routes do not fall back to browser HTML. The loopback adapter does not introduce a second authorization or Thread model.

## Failure and Shutdown Semantics

| Condition                              | Outcome                                                                                                        |
| -------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Invalid source candidate               | Previous accepted generation remains active; diagnostics identify the source                                   |
| Stale WebUI or CLI mutation            | Mutation conflicts and returns the current source digest                                                       |
| Root composition or credential failure | Run fails before model dispatch; prior continuation remains selected                                           |
| Root process loss                      | Receipts, active input, and partial output disappear; prior continuation remains selected                      |
| Child admission persistence fails      | Delegate or resume is rejected before acceptance                                                               |
| Child terminal persistence fails       | Execution is not reported as succeeded                                                                         |
| Provider or extension cleanup fails    | Failure is reported independently; known state and continuation publication still proceed                      |
| App graceful-drain timeout expires     | Remaining local tasks receive cooperative cancellation; saved nonterminal facts do not become invented success |

## Invariants

01. `AgentUiApp` is the only local application boundary.
02. File editing, CLI, WebUI, and Thread tools converge on the same configuration and App operations.
03. Surface values are detached and never expose native runtime or storage authority.
04. Thread metadata and configuration use independent compare-and-select heads; active Run compositions are immutable.
05. Root receipts and controls are exact and process-local; they are not durable work acceptance.
06. A selected deferred request set can be continued only by an exact complete response batch.
07. Root and child Runs use fresh native collaborators.
08. A child can resume with a different current composition while retaining the same Harness Thread history.
09. Saved nonterminal status never proves liveness or authorizes takeover.
10. Root-lineage live delivery and summary invalidation never become continuation authority.
11. The browser cannot bypass Project or expected-digest file authority.
12. Shutdown is bounded and does not invent completion.
