# Runtime, Async Subagents, and Surfaces

## Design Position

`HarnessUiApp` is the only Harness UI application boundary. It runs configuration, catalog, Project, Thread, Harness, Model, Capability, Plugin, MCP, Environment, subagent, and observation behavior in one process. The full-terminal CLI, collaborative WebUI, and explicitly enabled embedding tools are thin adapters over its typed commands and queries.

Harness UI implements the Harness `SubagentOperator` contract as `HarnessUiSubagentOperator`. It persists each async child as an ordinary child Thread, runs each delegate or resume as an independent segment, saves exact checkpoints, and returns bounded saved execution views.

Harness UI is not a Python package installer or upgrade supervisor. Already imported extension and Capability code remains fixed for the App lifetime. Agent Shell references and observations are Run-local. Provider state owns Agent command recovery; Harness UI adds no separate Agent process manager or wake path. Independently enabled [Host terminals](webui/02-host-computer-sharing.md#host-terminal) are human-facing App resources, not recovered Agent Shell references.

## Application Ownership

```mermaid
flowchart TB
    CLI[CLI]
    Embedding[Embedding adapter]
    ThreadCapability[Root Thread capability]

    subgraph App[HarnessUiApp]
        Files[Validated configuration files]
        Catalogs[Capabilities and extensions]
        Projects[Projects]
        Threads[Thread commands and queries]
        Runs[Run admission and coordination]
        Operator[HarnessUiSubagentOperator]
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

    CLI & Embedding & ThreadCapability --> App
    App --> Runtime
    Harness --> Observer --> Live --> CLI & Embedding
```

The App owns:

- stable multi-file and installed Content Plugin loading, accepted-generation selection, diagnostics, and validated source mutations;
- Capability and three-plane runtime extension catalog projection;
- Project, configured-resource, and Environment-profile queries, including the two release-owned execution modes;
- Thread creation, metadata and configuration mutation, Project-filtered keyset queries, and transcript projection;
- process-local root admission, receipt correlation, execution, deferred response, waiting, cancellation, and steering;
- immutable Run composition and continuation publication;
- in-memory shared browser drafts, page/focus and editor presence, with frontend Send using existing root admission;
- publication, bounded queries, and referenced saved-output reads for durable human comments;
- explicitly enabled native Host files, Git projections, and human PTY lifetimes;
- Project-root Environment binding and state lifecycle;
- async child admission, execution, checkpointing, query, wait, steering, cancellation, and linked resume;
- detached presentation, root-lineage live delivery, summary invalidation, and bounded graceful shutdown.

It does not expose database sessions, storage paths as authority, native Models, credentials, Provider objects, Environment adapters, Harness contexts or results, Pydantic AI message objects, runtime task or lock objects, callbacks, or Python exceptions through a surface API.

## App Lifetime

One App lifetime:

1. resolves the config path and bootstrap data-root locator, then configures logging at the executable boundary;
2. opens and migrates local storage without depending on a valid root YAML;
3. loads or restores the last accepted file-and-Content-Plugin generation and reports current source diagnostics;
4. initializes Capability and extension catalogs plus release-owned Model, MCP, and Environment adapter integrations;
5. starts bounded configuration change observation;
6. attaches the CLI, WebUI, or embedding adapter;
7. serves commands until shutdown;
8. stops new admissions, requests cancellation after a bounded graceful-drain interval, joins owned tasks, and then closes collaborators.

The shutdown timeout bounds graceful draining before cooperative cancellation. Harness UI retains structured ownership and keeps storage open until its tasks exit, so it does not claim a hard return deadline against arbitrary trusted Python code that ignores cancellation or creates an unbounded shield. Built-in collaborators and extension cleanup paths have their own finite bounds; a process supervisor owns any required hard process-termination deadline.

When `process.pricing_auto_update` is enabled, the App owns Pydantic AI's background price updater for its lifespan. Startup does not wait for the first download; upstream downloads happen immediately and hourly with last-good fallback. Root, async-child, and resumed-child construction capture the current Harness catalog off the event loop and bind it through the public build API. Already constructed Agents and emitted usage remain unchanged under the [Harness cost contract](../a13n-harness/12-events-observability-and-usage.md#cost-calculation). Price availability is diagnostic, not an App-readiness dependency. Shutdown releases this App's updater ownership without waiting for an in-flight download or clearing process-global prices. There is no App-owned second scheduler, disk price cache, or historical cost revaluation.

A source change triggers a stable complete-tree candidate load. Invalid intermediate saves do not replace the accepted generation. Module import starts no task, process, listener, or database connection.

## Observation

`open_harness_ui_app()` resolves one observation selection for the App lifetime. Its optional `instrumentation` argument accepts the existing `HarnessInstrumentation`, explicit `None` to disable UI/Harness observation, or the default `"environment"` selection. Root, resumed-root, and async-child reconstruction use that same captured selection. Harness and Pydantic AI retain their [single instrumentation ownership](../a13n-harness/19-observation-model.md); the App creates no duplicate model/tool spans or telemetry-derived continuation authority.

Environment selection retains the Harness `A13N_HARNESS_TRACE_LEVEL`, `A13N_HARNESS_TRACE_CONTENT`, and `A13N_HARNESS_METRICS` policy. Signals default to off. When tracing selects an unconfigured global proxy and `OTEL_TRACES_EXPORTER=otlp`, the App creates a private SDK tracer provider with an OTLP HTTP/protobuf exporter; `OTEL_SDK_DISABLED=true` prevents automatic setup. The App neither installs a process-global provider nor reconfigures an existing one. Automatic tracing supports `OTEL_TRACES_EXPORTER=none` or `otlp`; its protocol is `http/protobuf`. Standard OTel resource, sampling, batching and HTTP exporter environment settings remain SDK-owned. The default service name is `a13n-harness-ui`, overridable through standard resource configuration. Metrics require an externally configured or supplied meter provider; trace setup does not implicitly add metrics export.

The App closes only its own provider, after owned operations and collaborators finish. Export is best effort, and shutdown draining runs off the event loop under the App shutdown waiting budget. Export/shutdown failure does not replace operation results or checkpoint selection. An exceeded waiting budget does not promise that a blocking exporter thread stopped. Explicit or preconfigured providers remain the embedding Host's flush/shutdown responsibility, including a Logfire SDK Host.

One `harness_ui.root` span covers processing an admitted root receipt, including preparation before Harness entry, Harness stream consumption, continuation saving, and terminal receipt publication. Its correlation fields include the Thread, receipt and available Run IDs. It records the final application operation status, so a successful Harness result with failed continuation selection is not reported as a successful application operation. Rejections before receipt admission do not create that operation span.

One `harness_ui.subagent` span covers an accepted async execution segment from task entry through checkpoint/result publication and task cleanup. It starts a new trace linked to the valid inherited dispatch context; child preparation before task admission remains in the caller's context. Normal cancellation remains cancellation, not failure. A later root operation or child resume starts another bounded operation; a Thread is not a lifetime-long trace. Disabling UI tracing does not attach or replace another instrumentor's current context.

The App uses the shared Harness automatic enrichment for both App-owned and externally supplied providers. Each operation supplies neutral observation name, session and UI/kind labels. The session is the operation's actual Thread, never an encoded role or a root-family session. Bounded `root_thread_id`, `parent_thread_id`, `subagent_role`, `execution_id`, `segment_index`, and `resumed_from_execution_id` metadata comes from the existing Thread/composition/execution facts when available. These fields reach Harness and native Agent/model/tool descendants as neutral attributes and filterable Langfuse aliases; native content and usage fields remain unchanged. Root-family queries use `root_thread_id` across separate sessions. A resumed child keeps its Thread/session but starts a new execution segment and trace. Durable execution lineage does not imply a recoverable previous trace link: only valid inherited dispatch context supplies an OTel Link.

The shared trace-local propagation never copies a parent's type, terminal status, cost or payload onto descendants, and a new logical Run replaces its own identity. Operation roots follow the shared Harness bounded input/output policy: `none` omits bodies, while `standard` and `full` permit the current submitted prompt or deferred response and final result, at most 8 KiB per direction, with capture/truncation diagnostics. Native media is descriptive only. Output can remain visible after a saving failure; operation status, not output presence, describes application success. Credentials, raw exceptions, complete history and checkpoint bodies are never copied into diagnostic roots. Model/tool content retains its upstream information boundary.

Root execution adds only two coarse Host phases: `harness_ui.prepare` for configuration/state loading, reconstruction and Environment plan preparation; and `harness_ui.finalize` for Environment finalization and continuation selection. They are siblings of `harness.run`; no additional execute wrapper duplicates native execution. Fixed step labels locate failures, continuation status reports saving, and caught finalization errors mark the phase as failed. No stage is fabricated if execution never enters it. Child preparation before task admission remains outside the child trace.

Preparation additionally records prompt/deferred input kind and whether configuration mutation was requested. Its local result reports continuation loading, deferred resume, Capability count and Environment readiness. Finalization reports the actual continuation-selection status, Environment finalization, cleanup-error count and returned Harness status. These bounded structural fields and phase status have filterable metadata aliases; `standard` and `full` expose the phase-local result as bounded output under the shared Harness policy, while `none` omits output bodies. They never copy configuration bodies, checkpoint state or the root answer into phase output. Preparation failure retains reached metadata without inventing successful preparation; caught save/cleanup failure retains the real finalization result and failed status.

Each operation root summarizes available skills and observed reads only from its own Thread's consumed skill events, using the shared 16-name bounds and read-count semantics. Actual reads retain native tool observations; no duplicate skill-load span or cross-Thread usage claim is created.

Externally configured providers retain their own export and lifecycle policy; automatic enrichment does not install processors on them. The legacy optional `HarnessUiSpanProcessor` remains available for limited local-parent propagation on independently instrumented SDK spans, but UI/Harness spans do not require it. A Logfire exporter consumes the same hierarchy, not another Agent instrumentor or a transport through Langfuse. UI spans do not require a Langfuse client, trace-query backend, or backend URL detection.

Each recording UI operation also receives one root-only configuration summary after Agent reconstruction. It projects the captured composition generation, Thread configuration version, package prompt revision, Agent/model IDs, a fixed allowlist of numeric/boolean model settings and recognized reasoning/service-tier values, effective Capability IDs, selected plugin/MCP IDs, tool allowlist mode, immediate roster names, Environment profile/provider/adapter keys, and Run Extension IDs. A child segment records its own composition, including resumed selections. Request-local model fields still describe later runtime resolution.

This summary uses `a13n.ui.configuration` and the root observation's `configuration` metadata alias. It is not part of descendant correlation propagation or continuation authority. Lists contain at most 16 entries with total and omitted counts; the serialized summary is at most 8 KiB, retaining counts instead of long lists when needed. Instructions, global guidance, credential values or references, headers, arbitrary request bodies, endpoint URLs, native paths, and raw plugin/MCP/Environment configuration are excluded. Summary construction is skipped when the operation is disabled or non-recording, and failure never changes execution. `trace_content=none` still permits these bounded structural diagnostics, not content capture.

## Root Run Coordination

Trusted Agent reconstruction enables the existing [Harness model-attempt recovery](../a13n-harness/06-execution-context-and-lifecycle.md#model-attempt-recovery) for every root and child definition. The budget is five consecutive failed attempts, including the first, with the default cancellation-aware equal-jitter backoff. An accepted, complete primary model response resets the budget and backoff; partial output and successful auxiliary requests do not. Permanent and unclassified failures do not trigger automatic recovery. A recovery continues from normalized in-memory history inside the same logical Run; it does not resubmit the user operation, restart the Thread, or directly replay tools. Normal cancellation, tool failures, usage limits, deferred boundaries, and other Harness recovery exclusions remain unchanged. Exhaustion ends the operation with `model_recovery_exhausted`; Harness UI does not add another automatic retry loop.

### Long-text Input Files

The App applies the captured `input.long_text_threshold_chars` policy to authored root text from all submission surfaces and human root steering. Each eligible plain string or visible native `TextContent` block becomes one retained UTF-8 `text/plain` attachment, preserving exactly the text accepted by the App. Other text and media keep their ordering. Hidden context, existing attachment descriptions, tool results, child notifications, and previously selected history are not automatically converted.

Conversion requires an enabled built-in `view` tool under the captured Agent Capability and tool selections, plus a readable `thread-files` mount in the entered Environment. If file reading is disabled or unavailable by policy, the original text remains inline with an explicit skipped-conversion notice; conversion never enables tools or broadens Environment permissions. Failure to prepare the selected mount or save the file fails initial execution, or rejects that steering operation, rather than publishing a reference to missing content.

Files are retained before their references enter native input or the steering queue. Generated text files share the submitted-attachment count and byte limits. Model input contains only a file reference, character count, and instructions to read the file before answering, with no original-text preview or summary. The reference uses the current Environment's aggregate file path. The `harness_ui` metadata retains attachment identity, relative mount path, character count, and a `long_text` marker; existing source metadata remains intact. Full original text is not copied into metadata. Transcripts expose the reference and attachment handle, and surfaces retrieve original content through App attachment reads rather than expanding it back into model history. Conversation list excerpts use the attachment name for file-only input.

The [Thread file lifecycle](03-local-storage-and-recovery.md#thread-files-and-automatic-scratch-cleanup) applies unchanged across subsequent Runs, restarts, and scratch pruning. Retaining a file is not durable execution acceptance. A failed or cancelled operation can leave an unreferenced retained file. Steering preparation stays bound to its exact receipt and stream and cannot retarget a later Run. Reading the file can add tool-result content to model history; this feature does not compact those results or guarantee a smaller context for tasks requiring the entire text.

### Process-local Operations

Ordinary TUI and WebUI prompt submissions append a fixed XML-wrapped presentation hint after input validation and attachment preparation. The hint describes the submitting interface for that input's response: WebUI renders static Mermaid fenced blocks, while TUI displays their source and prefers terminal-readable explanations. Explicit user format requests and file output formats remain unchanged. The hint is model-visible `TextContent` with `display=False`, retained with ordinary input history but hidden from conversation display and excluded from authored long-text conversion. It is not user-configurable, does not change tool permissions or sticky Thread configuration, and adds no HTTP request field. Steering, deferred responses, internal messages, and ordinary programmatic submissions do not append this hint.

Root prompt admission accepts native Harness `RunInputValue`: text or a sequence of Pydantic AI `UserContent`, including `BinaryContent`. This is a process-local input contract, not a serialized transport schema or permission to return native message objects in projections. The App normalizes and detaches mutable input before asynchronous admission. Image-only input is valid; empty or whitespace-only input is not. Native Harness messages and selected continuation checkpoints retain submitted content through the existing lifecycle. Provider capability failures are explicit; adapters do not silently discard content or substitute Models. The [CLI contract](07-interactive-cli.md#multimodal-drafts) owns terminal acquisition and recovery presentation. The App additionally accepts `ComposerInput` (an ordered `parts` tuple of expanded authored text, `ComposerAttachment` values containing detached byte uploads plus presentation labels, and `ComposerAttachmentReference` values identifying already-staged Thread bytes, with an optional input source ID), or native input with previously staged Thread-scoped attachment IDs. The App resolves composer parts in order into existing native input content, tagging the source and part index in content metadata for surface-specific projection; it never accepts editor tokens as attachment identities. Ordered references reuse their existing identity without re-staging bytes; repeated references remain distinct authored occurrences and count toward input limits. App stage/read operations return detached attachment metadata and bounded bytes rather than transport- or terminal-specific file objects. These are reusable process-local contracts; HTTP owns its serialized DTOs. All surfaces share the limits of eight attachments per input, 10 MiB per attachment, and 20 MiB in aggregate. Images undergo shared format/size validation and become native `BinaryContent`; ordinary files become descriptive `TextContent` with a relative `thread-files` mount reference. Attachment identity, original name, media type, size, and relative path travel in the `harness_ui` content metadata namespace and remain available in transcript projections. The App validates Thread existence and scopes every handle to that Thread.

Explicit saved-comment capture uses the same retained-input owner: a complete published comment and original assistant output become bounded UTF-8 text with comment provenance in the existing attachment metadata. This is not a native Host file operation and does not require computer sharing. [Output comments](webui/05-output-comments.md#relationship-to-agent-execution) owns its selection and presentation contract. Captured feedback remains inline model text, not authored long-text externalization. Root steering reuses ordinary retained-input preparation for text, ordered composer parts, and Thread-scoped attachment IDs. Images remain native `BinaryContent`; ordinary uploads and binary or larger captures remain retained file references. Image-only steering is valid. Attachment limits, source attribution, authored order, and content metadata are identical to ordinary submission; no steering-specific text-only or 64 KiB eligibility restriction applies.

`touch_thread(thread_id)` and authenticated `POST /api/threads/{thread_id}/touch` explicitly advance root [navigation recency](03-local-storage-and-recovery.md#navigation-recency) and return a refreshed Thread summary. They publish the ordinary Thread invalidation hint without changing conversation content or starting a Run. Surface prompt/deferred-response admission and accepted human steering apply this recency policy internally; browsers do not need a second touch request and do not touch on navigation or refetch.

One App admits at most one root operation for a Thread. Another root submission while that operation is preparing or running is rejected rather than queued. Admission returns a detached receipt immediately; the receipt ID is unpredictable, unique within the App lifetime, and is the exact correlation used by active queries, waits, steering, and cancellation.

A root operation progresses from `preparing` to `running`, then to `completed`, `suspended`, `failed`, or `cancelled`. Preparation failure is `failed`; `completed` or `suspended` requires the corresponding acceptable continuation to be selected. Its view can acquire a Harness Run ID after native stream construction and retains separate Harness execution, continuation-selection, Environment-state, and cleanup facts. The App retains the detached terminal view for the remainder of its lifetime, but does not persist the receipt or a replayable root-input queue. Retained attachments follow the separate [Thread file lifecycle](03-local-storage-and-recovery.md#thread-files-and-automatic-scratch-cleanup); authored conversation input is recovered through selected checkpoints, not through the receipt. A process restart therefore exposes the Thread and its last selected continuation but no prior operation, wait target, or control authority.

Cancellation is accepted against the exact receipt during both preparation and Harness execution. During preparation it cancels the App-owned operation scope; once a stream exists it also requests cancellation from that exact stream. Steering is available only while the receipt names the current running stream. A stale receipt can neither steer nor cancel a later Run on the same Thread. Wait observes the operation's state transition and has no effect on execution.

Steering without explicit Skill references does not load a Skill catalog or revalidate the accepted configuration generation. A catalog already pinned to the active Run is returned and used for Skill-reference validation without first reading current configuration. Project renames and an unreadable newer shared generation therefore do not block these live controls; plain steering remains independent of catalog availability. Empty Skill-reference validation is a no-op on every App surface. These rules do not bypass exact-receipt checks, attachment validation, or the stored-state checks required for a new Run.

For an admitted prompt or deferred response, the App:

01. loads the root Thread, optional patch, required expected configuration version for a non-empty patch, and selected continuation;
02. validates and commits the sticky configuration update when present;
03. validates that the selected continuation accepts the input kind and, for a deferred response, still matches the caller's expected continuation ID;
04. captures the current accepted generation and selected Project roots;
05. resolves the exact Agent graph, Capabilities, tool visibility, Harness Plugins, Content Plugin snapshot, MCP servers, Environment Provider, and Environment Run Extensions;
06. publishes the immutable resolved Run composition;
07. creates fresh native collaborators; each subscription-backed Model request resolves and refreshes its compatible OAuth credential when needed;
08. starts one Harness stream and observer from the selected `HarnessState`;
09. saves complete root model-request checkpoints after input consumption and context transformation, advances its expected continuation after each successful selection, and forwards public live events best effort;
10. finalizes Environment adapters and publishes changed state;
11. publishes and compare-and-selects any available valid terminal checkpoint, including failed or interrupted execution, under the [continuation policy](03-local-storage-and-recovery.md#run-composition-and-continuation);
12. selects a detached terminal operation outcome with independent execution, continuation, Environment-state, and cleanup facts.

No database transaction spans file I/O, catalog import, native construction, model execution, or event delivery. Checkpoint selection uses a separate short transaction; it does not hold a session while the Run continues. If capture fails, an already committed explicit Thread patch remains the Thread's desired next state and the operation reports why it could not start.

### Root Deferred Response

A suspended root continuation exposes a bounded detached request list. Each item has its tool-call ID, request kind, tool name, JSON arguments, presentation-safe metadata, and a discriminated safe presentation when Harness UI recognizes the public request contract. The standard presentations distinguish structured `ask_user_question`, function-tool approval, and generic external result or denial. An unrecognized request retains the generic bounded representation; a surface never infers a specialized decision contract from display text. An ordinary prompt cannot skip a selected deferred request set.

Decision projections disclose omitted arguments and metadata independently. `override_allowed` is false when the retained Harness approval envelope binds the original tool and arguments, or when arguments are omitted; it is not inferred from the model-visible tool name. Surfaces do not offer approval for omitted arguments. Missing review assessments alone do not prohibit a decision on otherwise inspectable arguments. These presentation capabilities do not replace Harness authorization checks.

A response command names the exact selected continuation and supplies exactly one response for every pending item. Approval responses approve, optionally with replacement JSON arguments, or deny with a bounded message. External-call responses supply a JSON result or a bounded denial. The App rejects stale continuation IDs, missing or additional IDs, duplicate IDs, kind mismatches, and values that cannot be represented by the corresponding native deferred result.

Only the App reconstructs the native deferred request and result batch. A response is a new root operation with fresh Run composition and collaborators; it does not reuse the suspended operation's runtime objects. The selected suspended continuation remains current unless the response operation publishes and compare-and-selects another acceptable continuation.

### WebUI Interaction Deadlines

A WebUI-mode App arms one process-local deadline for the complete deferred request batch after a root operation successfully selects a suspended continuation. It uses `tools.interaction_timeout_seconds` from that operation's captured configuration. All browser participants share the same deadline; reading, editing, refreshing, navigating away, or disconnecting neither restarts nor cancels it. CLI interactions retain their [per-question terminal policy](07-interactive-cli.md#waiting-and-cancellation); embedded and one-shot Apps do not arm WebUI timers implicitly.

On expiry, the App submits one complete response against the exact continuation through ordinary root admission. Every approval is denied; every external result is explicitly failed. Structured questions report that no answer or approval was received and instruct the Agent not to repeat the question, continuing with reasonable assumptions where possible. Unsubmitted browser drafts are not responses and are discarded as input to this continuation. Timeout never fabricates answers, execution, or permission.

Human response and expiry share the root admission boundary. A matching response admitted before expiry disarms the timer; one arriving after expiry is rejected with `thread_interaction_expired`, even if the timer has not run yet. An admitted timeout response is never automatically retried after preparation, execution, or save failure. A stale continuation cannot resume newer work. Archiving or stopping the App disarms outstanding waits without submitting a timeout. Deadlines are not persisted or rearmed from old checkpoints after restart; retained unanswered requests remain available for explicit response.

`DecisionBatchView` exposes nullable `expires_at` and `server_time` timestamps. A null expiry means no automatic timeout is armed in this process. Browser countdowns are advisory and use server time to avoid participant clock skew; reaching zero refetches state and disables the expiring form, without submitting anything. Existing root-operation and Thread invalidations deliver the authoritative outcome. A countdown or admission receipt never establishes successful continuation saving.

## Harness UI Subagent Operator

### Admission and Identity

The Harness resolves the selected child roster entry before calling the operator. The operator creates a child Thread whose discriminated Agent-resource or Markdown-subagent source comes from that entry. Project, Environment profile, and Environment Run Extensions initialize from the parent Run capture. Agent-resource children use their own Plugin and MCP defaults when present; Markdown children inherit the parent capture's exact Plugin and MCP lists.

`delegate`:

1. verifies parent Thread and Run correlation;
2. creates the child Thread and exact sticky configuration;
3. captures and publishes the child Run composition;
4. commits segment zero as `running` before returning its execution ID;
5. starts the segment under App ownership.

A surface can update the retained child Thread through the ordinary required-version configuration command before resume. `resume_subagent` then loads that Host-authorized sticky configuration, requires a selected terminal child checkpoint, increments `segment_index`, captures the new composition, commits the execution, and starts it. The new composition need not equal either the one that produced the prior checkpoint or the current parent roster definition. Harness still requires the same stable roster name and current plan context. Harness UI reapplies that roster edge's Identity policy to the selected replacement definition and intersects the plan usage ceiling with the replacement's own limits; the model cannot select the replacement Agent source.

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

The operator consumes ordered public Harness stream items and compacts them into the bounded display owned by [Local Storage and Recovery](03-local-storage-and-recovery.md#compact-child-display). Nonterminal public events, including incremental text, reasoning, and tool arguments/results, are published as provisional live observations without waiting for message or tool closure. The saved compact display still records closed activity; partial live delivery is neither a saved output target nor execution completion. Terminal success is acknowledged only after Environment cleanup, terminal checkpoint publication, and atomic head selection.

Harness UI performs no automatic parent wake Run after child completion. A connected surface receives live completion, and a later parent Run reconciles through `subagent_info` or `wait_subagent` against saved heads.

### Deferred Requests

A child suspension is not forwarded to the user or parent Thread. The operator supplies the complete denial/no-response values required by the exact request set and continues through normal Harness continuation. The continuation uses a fresh child `run_id` and observer inside the same segment. Failure to continue safely produces explicit child failure.

### Queries and Control

The public execution view contains execution, root lineage, parent Thread, child Thread, child Run, segment, opaque composition identity, persisted status, bounded failure, resumability, and current-process control availability. Child list pages omit activity payloads and do not decode saved native checkpoints. Inspecting one execution loads its compact activity lazily; a locally active execution uses the in-memory display instead of first loading a checkpoint it would discard. Persisted status and local activity are separate fields: a saved `running` fact can be locally `active` or `unavailable`. Available actions are derived from the exact local segment and never inferred from the saved status alone. The view contains no raw unbounded output or private state.

Steering, cancellation, and bounded live waiting operate only when the current App owns the segment in its process-local registry. A saved `running` value without a matching local runtime is inspectable but grants no control. Execution references are scoped to their originating parent relationship, and a stale execution ID cannot control a later segment.

Child waits default to 30 seconds and accept finite non-negative timeouts, capped at 180 seconds. A longer request waits for at most 180 seconds rather than failing. The model-facing `wait_subagent` schema requires a positive timeout when supplied; Host queries also allow zero for polling. Completion can return earlier, and expiry returns the current execution projection without cancelling the child.

At model-facing subagent entry points, unavailable or out-of-scope execution IDs and requests to resume a non-resumable execution produce native tool failures so the parent Agent can correct its request. Host surface calls retain typed application errors. Corrupt stored authority, invalid parent/plan bindings, programming defects, and cancellation are not converted into ordinary tool failures.

### Loss and Retention

Orderly shutdown requests cancellation for locally owned segments. A durably completed cancellation becomes `cancelled`; a segment that cannot reach a terminal checkpoint becomes `lost`. Abrupt loss can leave a saved `running` head. Another App does not infer liveness, replay, or take over it.

Linked resume is permitted from a successful terminal execution whose exact selected checkpoint remains resumable, whose stable child roster name still resolves in the current parent, and whose current Host policy authorizes the operation. Compatibility applies to `HarnessState` schema and current selected component state, not equality with the prior Agent definition or Run composition.

## Detached Surface Contracts

All Thread, root-operation, child-execution, and presentation command results, query results, and stream items are strict, frozen, bounded, serializable values. A returned collection is immutable and every nested mutable payload is copied. Opaque digests or IDs can correlate a later command, but a surface never receives an immutable-object path, object kind, or storage read authority.

A root-Thread summary query accepts one Project selector: all root Threads, one exact Project ID, or unresolved Project references. The all selector includes every root Thread even when its current Project no longer resolves; an exact selector matches the current sticky Project ID; the unresolved selector matches Threads whose current Project ID is absent from the accepted generation. It also accepts an explicit archived selector and optional normalized search text. Search matches a Unicode-casefolded literal substring of Thread ID, explicit title, first-input excerpt, latest-input excerpt, or latest-reply excerpt; empty search is omission. Matching happens before keyset pagination across the entire selected scope, not just loaded rows. A query can also select an exact set of Project IDs for current-directory membership, including an empty set. It uses either one Project ID or a Project-ID set, never both. The default sort remains metadata update time; activity sort uses saved conversation activity, falling back to creation time for empty Threads. Cursor identity includes search, Project selection, archive selection, and sort. These selectors change presentation membership only and never mutate Thread metadata or configuration.

Thread queries provide:

- a summary containing identity, parent identity, metadata version, explicit title, saved conversation excerpts and activity time, archive state, timestamps, sticky configuration, selected-continuation state, and current-process root activity;
- a navigation summary containing the root summary plus pending-decision kind and count, current-process terminal outcome when retained, child persisted-status counts, the current-process `active` or `unavailable` partition of persisted-running children, latest safe activity and time, and exact available actions;
- a detail containing the summary, selected continuation ID, deferred request projection, and available actions;
- a transcript page of typed presentation entries rather than serialized native Pydantic AI messages;
- a bounded Working State task summary and page projected from the selected continuation without exposing the raw Capability namespace;
- a child execution page whose durable status and current-process control availability are distinct.

A root-Thread summary page computes its bounded summaries without requiring the surface to issue one detail or child query per returned root Thread. Child persisted-status counts cover `running`, `succeeded`, `failed`, `cancelled`, and `lost` across the root's descendant tree. Separate `active` and `unavailable` counts partition only persisted-running children and are not added to the persisted total. The page excludes archived Threads unless requested and contains no durable read or acknowledgement state. A surface may rank or acknowledge current-process completion locally, but that presentation state does not mutate Thread or execution truth.

A current-directory Project query accepts one absolute local directory and applies the [first-root matching contract](04-projects-threads-and-environments.md#current-directory-resolution). It returns a discriminated selected, unmatched, or ambiguous projection; a selected result contains the detached Project summary used for new-Thread creation. It does not create a Project, return filesystem authority, or reorder the configured roots.

The Environment-profile query returns the two release-owned modes first, followed by accepted custom profiles. Each item is a detached value:

```python
class EnvironmentProfileSummary:
    profile_id: str
    name: str
    mode: Literal["full-control", "sandbox", "custom"]
    description: str
    provider_key: str
    release_owned: bool
    canonical_host_paths: bool
```

The release-owned descriptions state their material authority difference: Full Control commands have ambient Host-user filesystem and network access, while Sandbox commands require Local Envd filesystem/process containment and denied networking. `canonical_host_paths` describes aggregate path presentation only and never implies Full Control. Surfaces select and persist `profile_id`; display names and mode labels do not become Thread identity.

Configuration-source queries expose an accepted-generation view for the root YAML or an approved immediate resource source. The conceptual projection includes `relative_path`, `resource_kind`, `resource_ids`, `source_digest`, `generation_digest`, `writable`, `content_available`, and nullable `content`. It does not expose the current invalid on-disk candidate or promise candidate diagnostics; MCP bodies are unavailable (`content_available: false`, `content: null`). A repair caller supplies complete replacement content through the validated mutation operation.

`relative_path` is an App-approved configuration-tree identity, not a caller-selected filesystem path. The view grants no directory traversal, arbitrary file read, immutable-object access, or write authority. Create, update, and delete use the validated last-write-wins contract owned by [Configuration and Resource Catalog](01-configuration-and-resource-catalog.md#file-mutation-and-last-write-wins).

Thread and transcript pages use opaque keyset cursors bound to the query shape and deterministic sort key. Root-Thread summary pages additionally bind the Project, archived, and search selectors. Thread ordering is descending `(updated_at, thread_id)`. Transcript entries always appear in ascending immutable message position within a page; a focused initial query returns the latest bounded page and a backward cursor pages older entries for prepend. A transcript cursor binds the Thread, selected continuation, direction, and boundary, so a changed continuation fails or resets rather than combining histories. Transcript pages expose entry cursors (`next_cursor`, `newer_cursor`) separately from turn-navigation cursors (`earlier_turns_cursor`, `later_turns_cursor`). Turn cursors start before the earliest visible turn's input or after the latest visible turn's end, skipping unloaded execution within already visible turns; absent turn metadata uses the entry boundaries. Null means that direction is exhausted. Execution readers use entry cursors independently and stop at their owning turn's input boundary. A newer insertion does not shift unaffected entries across an existing page boundary. Updating a Thread can move it across that boundary, so summary invalidation prompts a fresh first-page query. Invalid, mismatched, or expired cursors fail explicitly. Project recency is aggregated over all associated non-archived Threads in storage rather than a bounded Thread page.

A bounded Project-path completion query searches only logical paths beneath the selected Project roots and returns the mount label, logical relative path, kind, and display value. It does not return file bytes, follow paths outside the configured roots, mutate Project configuration, or grant filesystem authority. Surfaces use it for completion only; the selected Agent's tools retain file-read authority.

`thread_tasks` binds to one selected continuation and carries the authoritative task-state version, bounded task entries, and explicit completeness or omitted-count information. Each task entry contains only its stable ID, version, subject, active form, status, owner, and dependency IDs. An expected continuation mismatch is rejected. This saved inspection contract remains distinct from current work observations; task inspection does not gate the focused stream's first snapshot.

`thread_notes` returns complete note keys and values from one selected continuation, bounded to 256 entries and 256 KiB of UTF-8 content with total and omitted counts. An expected continuation mismatch is rejected. Surfaces do not reconstruct note mutations from tool arguments or read the raw Working State namespace.

### Current Thread Work

`thread_work` and `GET /api/threads/{thread_id}/work` return a detached current work observation. The default response contains task status counts and one active task, note availability and count, and independently owned child status/activity counts. Optional `include=tasks` and `include=notes` add bounded details using the same task and note projections as saved inspection. Child detail, output, and pagination remain on the existing child APIs. Work observation grants no mutation authority.

The response identifies the Thread, App epoch and invalidation boundary, live Run and run-local revision when present, the selected saved continuation, and the live Run's restored baseline continuation. `source=live` means a complete owner-published work observation, not durable state. `source=saved` means the selected continuation supplies the values; `source=unavailable` is distinct from an available empty state. Task-state versions and run-local note versions describe their own owners. Provider tasks are explicitly last-observed or unavailable, never an embedded authoritative snapshot. Child counts combine persisted status with process-local availability independently; the response is not an atomic transaction across owners.

Harness publishes the complete restored work baseline before model execution and before mutations, even if no restored task changes. The App installs each observation before publishing a small `thread_work` summary invalidation containing affected sections and the Run/revision. Notes are readable immediately after owner commit, without waiting for a checkpoint. Checkpoints advance the saved fallback without replacing live state. Terminal cleanup removes only the matching Run's live observation after continuation selection settles; a failed save never promotes unsaved observations to durable state. Child admission, terminal status, and activity cleanup likewise publish only after their respective state is readable. Restart discards live observations and reads saved state.

The WebUI observes lightweight work summaries while inspection popovers are closed and loads details lazily. Work inspection and context details share one query owner independent of transcript focus, history cutover, or `showLive`. Task events remain transcript activity, not a second task directory. Refreshes coalesce; hints arriving during a request require a follow-up read rather than allowing a delayed earlier-Run response to become current. Failed or stale reads retain prior content with explicit provenance instead of displaying empty current state. Reconnect, reset, and foreground reconciliation use the existing summary transport and query cache, not high-frequency global polling. Pagination restarts from its owning first page when its source changes.

Independent Sidekick roots observe only their own work. Asynchronous child private tasks and notes never merge into the parent; explicit inline shared task cells continue to notify their owning root without sharing child notes. No related-Thread collaboration panel or separate Sidekick refresh path is implied.

### Review Projections

A review projection is either embedded in a deferred request or queried through exact correlation. Deferred review names the selected continuation and request ID; retained tool review names the selected continuation, transcript position, and tool-call ID; process-local live review names the exact receipt, Run, and tool-call ID. The detached value identifies its lifecycle and review kind and can contain safe arguments, managed effect and resource summaries, bounded diff or confirmed file-change detail, and explicit truncation, omission, or unavailability facts. Harness UI never reads current mutable Project files to reconstruct a historical or pre-approval diff. Detail that existed only in a dropped live event or a prior App process is explicitly unavailable. A review value grants no approval, filesystem, or tool-execution authority. Review summary and content text accept the selected source without a second display-length validation cap; a long result does not make the review query fail. Source-level activity windows, transcript page budgets, and explicit truncation or omission facts still apply. Readers control the visual extent through disclosure and scrolling rather than shortening the returned review text.

Thread title and archive state share a metadata head independent from sticky configuration. A metadata mutation supplies its exact expected metadata version and changes title, archive state, or both atomically. Archiving an active root Thread is rejected. Unarchiving is valid, and a title can be explicitly cleared. Child metadata is managed only through parent-scoped child operations.

Failures are presentation-safe structured values with bounded code, message, details, and retry hint. Root operation outcomes contain scalar or JSON output, status, usage projection, continuation selection, Environment-state publication summaries, and cleanup failures; they never contain a native `HarnessRunResult`, `HarnessState`, deferred request object, or `Exception`.

### Configuration Sources and Defaults

The App exposes accepted configuration source metadata and individual source views, creation configuration preview, exact root-Thread configuration mutation, and Project-default preview/apply. Source views identify their accepted generation and do not claim to be the current files on disk. MCP source bodies are unavailable rather than an editable redacted replacement; literal credentials and compatible account stores are not exposed through these queries. Only approved immediate source paths support replacement or deletion, using the configuration owner's last-write-wins contract. A mutation response reports the completed source action and subsequently accepted generation separately from the submitted source digest; it does not promise those bytes remain current after another editor writes.

Thread creation previews use the same per-axis resolution as creation and pre-Thread Skill queries. They allocate no persistent Thread. Explicit root-Thread patches support Project selection and clearing as well as Agent, Environment profile, Plugin, Run Extension, and MCP selections; omitted axes retain exact saved values. Child configuration remains parent-scoped. Project-default apply uses the version and default-combination digest described by [Projects and Threads](04-projects-threads-and-environments.md#project-creation-configuration), not a source-file write condition.

## Host-only Thread Collaboration Capability

`ThreadCollaborationCapability` is an App-injected, root-only optional embedding capability. It exposes bounded cross-Thread collaboration, not storage access, process supervision, or durable scheduling. The public resource catalog cannot select it. The existing embedding selector `host_mode="webui"` opts into this capability; it does not start a WebUI or HTTP server. The stock interactive and one-shot CLI use terminal mode and do not inject it. Children never receive it.

The tools are conceptually:

```python
list_threads(query=None, cursor=None, limit=20, project_id=None, include_archived=False)
get_thread(thread_id=None, history_cursor=None, history_limit=50)
list_projects(query=None, cursor=None, limit=20)
get_project(project_id)
list_agents(query=None, cursor=None, limit=20)
list_models(query=None, cursor=None, limit=20)
create_thread(prompt, title=None, agent_id=None, project_id="current", model_id=None)
run_thread(thread_id, prompt, model_id=None)
steer_thread(thread_id, message)
send_thread_message(thread_id, message)
```

Listing and search cover the server's accepted root Threads with bounded retained history and process-local activity, optionally filtered by Project and including archived history. `get_thread()` defaults to the calling root. Its `current_run` identifies that Run's captured Project, roots, Agent, Model, and generation; the detached Thread configuration remains the next-Run selection, not evidence that a running Environment changed. Target inspection also exposes `next_model_id` and the available captured Model separately, including when inspecting another root.

Resource discovery reads the App's accepted configuration, independent of Sidekick. Project, Agent and Model lists support case-insensitive name/ID search and pages of 1–100 entries. Cursors bind to kind, normalized query and accepted generation; changing any of them requires a new listing. Project summaries identify root count and authored default Agent; `get_project` returns roots and authored creation defaults without entering an Environment. Agent summaries expose ID, name, Model ID and selected Capability IDs. Model summaries expose ID, name and route. These views contain no instructions, authentication, raw Model settings, MCP configuration or credentials, and do not claim connection readiness. Agent IDs are root resource selectors, not child roster names.

`create_thread` creates an independent root, then admits its first prompt. `project_id="current"` selects the source Thread's Project, null selects no Project, and a configured Project ID selects that Project. With Sidekick disabled and neither Project nor Agent explicitly selected, creation retains the source's sticky selections, including `default_model_id`. Enabled Sidekick creation applies the captured defaults described below. Selecting a Project or Agent uses normal creation-default resolution for the selected Project/Agent instead of copying the source's environment or MCP selections. An optional `model_id` uses the existing per-operation Model override for the first Run. `run_thread` accepts the same override for a later explicit turn; neither changes the Agent resource or sticky Thread configuration. It accepts no new Project definitions, roots, credentials, tool grants, or raw configuration. Creation and prompt admission are separate: if admission fails after creation, the response identifies the created Thread rather than silently creating another on retry.

The root instructions directly identify the calling Thread and its captured Project ID and roots; no discovery call is needed to identify the caller. The initial task prompt identifies the requesting Thread and captured Project, gives an explicit `send_thread_message(thread_id=<requester>, message=...)` return address, and tells the worker to ask for clarification or decisions, report blockers, and send final findings, changes and validation. The requester can answer through the same tool targeting the worker. Sending does not synchronously wait for a reply, and acknowledgement-only loops remain prohibited. This context is saved with ordinary input at normal continuation boundaries, not as child execution lineage or a separate delivery store. Independent roots retain `parent_thread_id=null`.

`run_thread` accepts only another root, retains its sticky configuration, and returns the exact admission receipt without waiting for terminal completion. `create_thread` likewise returns the new identity and receipt. Callers inspect subsequent activity through `get_thread`; admission is not success. These nonblocking tools do not build a chain of waiting root Runs or a durable queue. Repeating a mutation is not idempotent; a caller must reconcile known identities and receipts rather than retry an uncertain write blindly.

`send_thread_message` attributes its message to the calling root. For an active target, it selects one exact receipt and attempts steering; preparation, completion races or rejected steering never fall through into a new Run. For an idle target without pending decisions, it attempts ordinary prompt admission once, resolving the target Thread's default Model rather than the caller's Sidekick preferences. The same target-owned precedence applies to later `run_thread`, WebUI Send/Retry, and terminal resume; active steering never changes the captured Model. Concurrent admission can reject that attempt; no automatic retry occurs. Child or archived targets are rejected. Responses identify `mode=steer` with the exact control result, or `mode=run` with the admission receipt. Acceptance is not proof of processing or saved delivery. There is no offline inbox, durable acceptance, completion callback, or automatic acknowledgement loop.

`steer_thread` resolves the target's current receipt once and controls that exact receipt; it never falls through to a replacement Run. Same-source recursive run, steer or message delivery is rejected. A child uses the existing parent-scoped delegation controls, not cross-root tools. Scope checks bind every tool invocation to its source root. Tool results remain bounded detached values with safe errors and explicit pending, unavailable, or failed status.

The capability calls the same in-memory `HarnessUiApp` services as other adapters. There is no localhost HTTP client, IPC bridge, agent daemon, or supervisor. App shutdown remains the execution boundary; restart does not reacquire old receipts or automatically replay input.

### Sidekick instructions

The optional root `webui.sidekick` mapping selects collaboration preferences, not another execution system. Its optional `agent` inherits the calling Run's Agent when omitted/null; its optional `model` initializes the new independent Thread's durable `default_model_id`. The Host applies these captured defaults during generic `create_thread`, even when the model supplies no Agent or Model arguments. An explicit `agent_id` wins over the configured Sidekick Agent; otherwise creation selects that Agent or inherits the calling Run's captured Agent, independently of destination Project defaults. Null/omitted `model` stores null, following the selected Agent's current Model rather than freezing it. An explicit create `model_id` overrides only the first Run and does not replace the saved default. An empty mapping enables inherited Agent selection with a null default Model. Both preferences are frozen in the Run composition and exposed in captured configuration inspection. The existing WebUI root collaboration capability instructs the Agent to use subagents for parallel research, exploration, and other bounded tasks whose results belong in the current conversation. If no suitable subagent is available, that work stays in the current Thread rather than falling back to Sidekick creation. Separate Threads are reserved for coordination work that needs human attention, decisions, or follow-up in its own conversation. For that work, the instructions specify the corresponding generic `create_thread` arguments, bounded task/context, result inspection and reporting. A Thread already executing a request is instructed to complete and report rather than recursively delegate the same task. Other accepted Agent and Model IDs remain selectable.

Sidekick is disabled when its mapping is absent or null. This removes its conditional instructions and creation defaults; all generic collaboration and discovery tools remain available subject to normal tool visibility policy. Configuration changes affect future Run captures and their new Threads, not active requests or existing Thread defaults. Existing Threads are not retroactively assigned Sidekick preferences. Terminal Runs and delegated children receive no WebUI collaboration capability or Sidekick instructions. There is no additional Skill, task launcher, startup execution, or supervisor.

## Live Presentation

Root transcript pages and entry reads project the selected continuation's [independent display history](03-local-storage-and-recovery.md#run-composition-and-continuation), falling back to native history for older objects. Display positions remain separate from model-history positions after context replacement. Context summaries expose their operation identities consistently in saved metadata and live observations so clients can preserve disclosure state without text-based matching.

Saved transcript tool results preserve the native `ToolReturnPart.outcome` (`success`, `failed`, `denied`, or `interrupted`) as an optional projection field. Absence remains compatible with older projections and non-result parts; clients do not infer success solely from arbitrary result content. Retry parts retain their distinct kind. This presentation projection does not change persisted history.

Live input uses the [Stream Protocol input mapping](../a13n-stream-protocol/00-overview.md#standard-event-conversion), not a second terminal echo of submitted text. Transcript parts retain the same `ContentMetadata` projection from native `TextContent`. CLI history and live rendering hide parts marked `display: false`; absence of the flag remains visible for compatibility. HTTP transcript and event projections retain this metadata, and browser rendering respects the same display flag without changing saved history. Filtering affects presentation only, never the continuation or persisted message history. Event type strings use AG-UI wire values such as `TEXT_MESSAGE_CONTENT`, not Python Enum representations.

Each App lifetime has an opaque detailed-live epoch and one global monotonic event sequence. Each root or child Harness Run has one observer. A detailed live event identifies the root Thread, immediate parent Thread when present, producing Thread, Run kind, Run ID, and child execution ID when present. A subscription focused on one root receives that root's entire descendant lineage; child events are not hidden merely because their producing Thread ID differs from the root.

The global sequence is event identity, not a per-subscriber delivery count. Events for unrelated root lineages can occur between two values delivered to one focused subscription, so that subscription observes a sparse strictly increasing subsequence. A numerical jump never proves loss. Only the hub's subscriber-gap detection, an invalid or expired cursor, or an epoch change requires reset.

The detailed live hub performs bounded best-effort fan-out and retains small independent root-lineage rings. Each ring is bounded; running roots and subscribed roots are pinned, with a separate bounded allowance for idle roots. A noisy root does not consume another retained root's replay window. Evicting a root's ring can require a reset for that root; unscoped native observation retains a separate bounded global tail. A slow subscriber can lose events and a reconnect outside the retained range receives an explicit reset requirement. Epoch mismatch likewise requires a new snapshot. A slow or disconnected subscriber never blocks execution, state publication, or continuation selection.

A focused watch establishes a subscribe-before-query boundary:

1. read the selected history identity, then install the exact root-lineage subscriber and atomically capture its epoch, `cutover_sequence`, and the root observer's published prefix;
2. query only detached Thread and root-operation projections, then recheck the selected identity; require a fresh watch if it changed across cutover, including when no observer existed at subscription. Child and task inspection use independent lazy queries and never gate the first focused snapshot;
3. when present, describe `root_stream` by its exact Run, original base continuation, and finite observer event count; the selected history must match that base or a successfully selected checkpoint whose native marker is inside the captured prefix. Otherwise require a fresh watch rather than mixing histories. Mid-Run saves never rewrite the original Run base;
4. attach `recent_events`, the newest available root-lineage ring tail at or below cutover, bounded to 128 KiB of encoded events and intended only as incomplete diagnostic context; and
5. return the snapshot, bounded root observer replay batches, and subscription, whose subsequent delivery contains only matching events strictly after cutover.

The snapshot is not a transaction across execution, SQLite, and the live hub. Its detached activity projections can be newer than cutover; they are separate facts, not a complete materialized event fold. Root replay uses the existing Stream Protocol observer, not another event accumulator or database. Observer indexes are Run-local positions, distinct from the hub's global sequence. Only events already published at cutover belong to the replay; observation that precedes publication is delivered later through the subscription. Replay uses the same bounded payload projection and explicit omission markers as live delivery.

`recent_events` is not an alternative source to append beside root replay and saved history. Child inspection remains a bounded compact projection of closed activities through parent-scoped queries; reconnect does not expose unfinished child activities or bypass child publication boundaries. Consumers render retained history independently, apply the root replay once, and refetch authoritative Thread/receipt projections for controls rather than deriving acceptance from a replayed lifecycle event. When the selected continuation advances, replace the provisional root display with saved history instead of appending both. A concurrent continuation change fails or resets a retained query instead of combining histories from unrelated continuations.

The hub references only the existing observer for the latest root Run per Thread. Successful continuation selection releases that reference; an already-open replay may retain its finite prefix until delivery closes. Unsaved terminal output has bounded process-local inspection retention. App restart restores selected history, not observers, subscriptions, or old root control. There is no new durable replay storage.

Subscription installation and retained replay are atomic within the owning hub, so events after cutover remain buffered even while snapshot queries run. Ring loss or a subscriber gap requests reset; it never invents historical output. Closing or cancelling delivery releases the registered subscriber even under task cancellation, without cancelling the producing Run.

A separate lightweight App-wide stream emits bounded invalidation hints for configuration, catalog, Project, Thread metadata/configuration/continuation, root operation, child execution, and committed output-comment changes. Each hint identifies only the affected summary scope needed for refetch. It carries no transcript or checkpoint payload and is not durable truth. A surface that misses hints refetches its summaries.

Retained transcript comes from selected continuations and child compact checkpoints. Current-process activity is merged only for presentation and never written back as continuation truth outside its owning checkpoint path.

### Collaboration and Comment Delivery

[Collaborative conversations](webui/01-collaborative-conversations.md) owns App-wide page participation, same-page membership, and Thread-scoped composer synchronization. Presence and drafts are separate transient values; a Thread-focused channel reset does not recreate their browser state. Interactive delivery validates instance access before registering presence or joining a draft. A page target grants no new resource or execution authority.

[Output comments](webui/05-output-comments.md) use ordinary App publication and bounded query operations with a separate durable completion boundary. Saved text projections expose typed opaque comment targets, not generic object-store references. Publication resolves only supported saved assistant text in the supplied Thread scope; a published comment provides bounded access to its original target without selecting that source for execution. Neither browser nor HTTP adapter reads SQLite or immutable files directly.

A comment invalidation is emitted only after commit and carries the affected Thread/comment scope, not an AG-UI assistant message or full discussion history. Reconnect refetches durable comments independently of detailed Run replay and draft synchronization. Live presence snapshots describe participation, not durable comment coverage. No cross-channel arrival order authorizes clearing an input, navigating another participant, or treating a comment as root input.

## CLI

The [interactive CLI contract](07-interactive-cli.md) owns input, commands, setup choices, rendering, startup, and local session selection. CLI code consumes this App boundary and contains no provider, persistence, or execution authority. One-shot mode uses the same exact-first-root Project selection and detached model overrides as interactive mode. Help/version do not open the App.

The commands, detached projections, live subscriptions, source-digest mutations, and expected-continuation decisions are shared with the explicitly selected WebUI adapter. Interactive mode never implicitly starts an HTTP listener.

## WebUI

The WebUI server exposes authenticated App commands, queries, HTTP snapshots and multiplexed realtime observation, and interactive collaboration/terminal connections, and serves the bundled project-centric workbench. The foreground server process owns one WebUI-mode `HarnessUiApp` and all process-local execution, even while no browser is connected. The server calls the App directly in memory; it does not forward work to a separate daemon or use process-to-process communication. The adapter owns only process-bound listener access, HTTP serialization and status mapping, static-asset delivery, and stream delivery. It does not expose an alternate configuration, authorization, or Thread model.

### HTTP Startup and Access

`a13n-harness-ui webui` binds to the IPv4 loopback address `127.0.0.1` by default. Bare `a13n-harness-ui` selects the interactive CLI and never starts an HTTP listener. `--host <ip>` explicitly selects another IPv4 or IPv6 bind address for `webui`; choosing a non-loopback address does not implicitly weaken authentication.

Key selection uses explicit `--apikey <key>`, then `A13N_HARNESS_UI_API_KEY`, then a newly generated unpredictable high-entropy key for that App process. An explicitly supplied value must be non-empty. Before accepting requests, dedicated startup stdout shows the ordinary browser URL and, only for a generated key, the key and a convenience URL carrying it in a percent-encoded `#api_key=...` fragment. URL fragments never enter an HTTP request. Supplied keys are not echoed and receive no startup URL containing them. Server-resolved keys do not enter the accepted configuration tree, SQLite, immutable objects, application diagnostics, ordinary logs, browser HTML, or static assets. Restarting without an explicit key rotates it. The browser's explicit same-origin key retention is owned by [browser authentication](webui/00-overview.md#authentication-in-the-browser), not server configuration persistence.

The generated default avoids a reusable secret in command history and process arguments. A user who selects direct `--apikey <key>` input accepts that the invoking shell or operating system can expose command arguments; terminal output warns about that boundary without echoing the value.

Every HTTP `/api` request authenticates with `Authorization: Bearer <api-key>` before invoking an App command or query. Interactive WebSocket connections also authenticate before exposing application content, subscribing to a shared draft, or creating/attaching a PTY; possession of an unverified connection alone grants no operation authority. The long-lived instance key is not carried in a request URL. HTML and immutable static assets can be fetched without the key, but they expose neither application data nor the key, and no HTTP endpoint returns it. A missing or incorrect key receives an authentication failure without revealing whether another key was close or valid.

`--dangerous-skip-permissions` explicitly disables API-key authentication for the complete listener lifetime and emits a prominent startup warning. It is valid for loopback and non-loopback binds, so the user owns this override. Combining it with a key supplied by CLI or environment is a startup error. It bypasses Web access authentication only: it neither enables native computer sharing nor changes Agent approvals or Environment permissions.

`--api-key` and `--dangerously-bypass-permission` are compatibility spellings of the corresponding canonical options, not independent settings or alternate access schemes. Conflicting repeated option values are rejected.

The WebUI command defaults to [native computer sharing](webui/02-host-computer-sharing.md), independently of authentication and Agent permissions. `--no-share-computer` makes the native backend operations unavailable; `--share-computer` explicitly selects the default. The authenticated status projection reports this enablement so the browser distinguishes disabled sharing from an unavailable operation.

Bind address, API-key selection, the dangerous bypass, and native computer-sharing enablement are executable-bound Web-surface inputs rather than desired-resource configuration. All authenticated application behavior still uses the same `HarnessUiApp` configuration, commands, queries, receipts, and live hubs as the CLI; the HTTP adapter cannot introduce surface-only business settings.

The adapter validates the explicit Host authority and, when present, the exact same-origin Origin before App access. JSON request bodies are bounded to 1 MiB; raw Thread attachment and native Host file uploads have a separate 10 MiB limit. Strict validation errors omit input values. Static assets use a self-only Content Security Policy without `unsafe-eval`. The adapter is a same-origin boundary. It does not enable credentialed cross-origin browser access or permissive CORS. All admitted collaborators share the same instance authority. A non-loopback bind is a trusted-team plain-HTTP listener, not tenant or account isolation; startup identifies the exposure, and trusted-network or TLS termination requirements belong outside Harness UI. Host and same-origin checks also apply to interactive connections, including when key authentication is explicitly bypassed.

The foreground listener reports startup, readiness, API response outcomes and shutdown progress at the configured process log level and format. Readiness is reported only after successful startup, separately from the pre-start login URL. Request diagnostics use route templates and do not include keys, bodies, query values or native paths. Failed requests add their application error code, a fixed safe explanation when known, and available generated Thread/receipt/Run correlation identifiers. Dynamic exception messages are not copied into logs; response envelopes and HTTP status semantics remain unchanged. Ordinary lifecycle/API messages use INFO; successful probes/assets and detailed App cleanup stages use DEBUG. Slow shutdown remains visibly pending rather than claiming completion.

On normal listener shutdown, realtime WebSocket delivery ends before connection draining, so an attached browser does not hold shutdown until the transport timeout. This delivery stop neither closes storage early nor separately cancels a Run. The existing ASGI lifespan remains the sole App owner and performs the [App lifetime](#app-lifetime) cleanup after transport draining. Closing one browser connection has no effect on App lifetime. A finished transport drain is not a hard deadline for trusted Python cleanup.

### HTTP Adapter Contract

Finite `/api` routes map strict request documents to one `HarnessUiApp` command or query and return strict detached JSON projections. A common bounded error envelope preserves safe App error code and message; detailed conflict recovery refetches the owning versioned projection. HTTP status mapping does not reinterpret App lifecycle or retry semantics. No route exposes a database session, internal storage-object reference as authority, native Harness value, Python exception, or resolved credential. Native filesystem access exists only through the explicitly enabled Host operations; conversation and configuration routes do not acquire arbitrary file authority by sharing the listener.

The adapter publishes a versioned OpenAPI document derived from its strict request and projection models. Its authenticated status projection identifies the API schema, App status, listener bind address, and whether listener access uses an API key or the explicit dangerous bypass. Repository generation retains an OpenAPI snapshot for contract drift checks. The browser consumes the public API and treats incompatible or unavailable operations as explicit failures rather than inventing server behavior.

Configuration inspection distinguishes sticky next-Run Thread selections from an exact captured composition. While a root operation is active, its own composition publication supplies the captured reference; before that publication the capture is explicitly unavailable, never substituted with the previous selected continuation. An exact receipt can inspect its capture while retained by this App. Without an active root operation, selected-continuation inspection uses that continuation's stored composition. Neither path reconstructs historical configuration from current desired resources.

These detached projections allowlist identity, model/capability/source selections, Environment identity, tool selections, and captured Tool Proxy grouping. They omit credentials, transport settings, arbitrary configuration payloads, instructions, and internal object access. Immediate child summaries are bounded to 100 with an omitted count; they are not a recursive executable recipe. Static Agent Tool Proxy inspection uses Agent defaults, whereas next-Run Thread inspection uses its exact selected source IDs. Neither static view contacts MCP servers or claims tool readiness.

The implementation catalog is distinct from configured resource selectors. Account inspection exposes only the existing credential-free account projection; logout removes the selected compatible account entry and is not cancellation of a pending login. Thread usage, last reported context footprint, and notes retain their existing owners and explicit omission/continuation semantics.

The HTTP adapter exposes the existing [interactive Skill catalog and reference contract](02b-environment-skill-sources.md#interactive-skill-references) through `POST /api/threads/skills-preview` with `NewThreadDefaults` and `GET /api/threads/{thread_id}/skills`. Preview creates no Thread. Submit and root steer accept an optional bounded `skill_references` list and pass it to the App for validation; the adapter neither reads Skill bytes nor changes catalog ownership.

The adapter exposes two logical observation streams, multiplexed on the browser's authenticated realtime connection:

1. one App-wide summary stream carries the summary hub's epoch, sequence, and invalidation hints, with an optional bounded terminal root-operation notice;
2. one focused root-Thread stream either opens a fresh App focused watch or resumes retained delivery for an existing watch cursor. A fresh watch emits its detached snapshot, any finite root-stream replay batches and ready boundary, then only later detailed events from that subscription. A valid resumable cursor emits only events after that cursor; an unavailable cursor emits an explicit reset.

The App's bounded root lookup and `POST /api/threads/lookup` accept one to one hundred Thread IDs and return detached Thread summaries, including their [saved completion markers](03-local-storage-and-recovery.md#saved-root-completions). Duplicate IDs are coalesced; missing and child IDs are omitted. Archived roots and unavailable Project references are included independently of navigation filters and pagination. This read does not hydrate continuation objects, touch navigation, or establish browser interest. The returned total counts matched roots and has no next cursor. Browsers batch their local interest set to reconcile completions after startup, reconnect, or restored foreground attention; summary events are refetch hints rather than recovery authority.

A terminal `root_operation` invalidation may include `notice` with the exact `receipt_id`, Host status (`completed`, `failed`, or `suspended`), and a plain-text `brief` of at most 320 characters. The notice is published only after the root receipt settles, including continuation selection. Its brief previews actual final assistant prose, deferred questions/approval reasons, or the user-facing failure; absent suitable content uses a generic status description. It performs no model call and includes neither full output nor tool-argument dumps. Cancellation, child progress, and nonterminal root transitions carry no notice. A failed continuation selection cannot produce a completed notice even when the model returned output.

Notices share the summary hub's bounded process-local retention and resume cursor; they are not a durable inbox or a delivery guarantee. Opening a fresh subscription starts at the current boundary. Cursor expiry or an App epoch change requires ordinary reconciliation, not synthetic historical completion alerts. Consumers deduplicate by epoch and receipt identity; notification projection or delivery failure never changes execution or continuation publication. Existing clients can ignore the optional field and continue refetching affected resources.

Focused frames use the following conceptual JSON union; the adapter's OpenAPI document owns the serialized schema:

```python
class FocusSnapshotFrame:
    kind: Literal["snapshot"]
    snapshot: ThreadFocusSnapshot
    resume_cursor: str | None


class FocusReplayFrame:
    kind: Literal["root_stream"]
    run_id: str
    events: tuple[RootStreamEvent, ...]  # At most 16 bounded events, with observer indexes.


class FocusReadyFrame:
    kind: Literal["ready"]
    resume_cursor: str


class FocusEventFrame:
    kind: Literal["event"]
    event: LiveEvent
    resume_cursor: str


class FocusResetFrame:
    kind: Literal["reset"]
    reason: str
```

A fresh focused stream captures only the history identity before subscribing; it queries the snapshot after installing its subscription. When the snapshot includes `root_stream`, its resume cursor is null: apply the indexed replay batches to that Run's provisional display, and retain a cursor only after the `ready` frame. If bootstrap is interrupted before ready, discard the incomplete bootstrap and open a fresh watch. Without root replay the initial snapshot already carries the cutover cursor. Normal live event frames carry an opaque `resume_cursor`, encoding stream kind, exact root lineage when applicable, epoch, and sequence. A client reconnect passes it in the subscription command's bounded `after` field without decoding the cursor. Summary `open` and `invalidation` frames carry the same cursor concept; reset frames carry a reason and no reusable cursor. An invalid encoding, wrong kind, wrong lineage, future sequence, or expired epoch receives explicit reset semantics. That cursor is bound to the focused stream kind and exact root lineage and cannot be reused for another root. A valid resume does not emit a replacement snapshot; the hub establishes retained replay and following live delivery as one cursor continuation, so no matching event can fall between them. It replays retained matching events after the cursor and then follows the same lineage. An epoch change, expired cursor, or subscriber gap returns reset semantics rather than invented replay. Because the global sequence is sparse after root-lineage filtering, a numerical jump alone is valid and never causes reset. Unsubscribing or disconnecting closes observation only and never cancels the producing root or child execution. Draft synchronization does not replace execution-observation semantics.

### Multiplexed Realtime Delivery

The browser owns one `/api/realtime/connect` WebSocket per authenticated access lifetime for the summary channel and bounded focused-root channels. It uses the existing first-frame `InteractiveAuthentication` contract, after Host/Origin validation. Credentials never appear in URLs or subscription commands. HTTP retains snapshots, history pagination, commands, and files. PTY, draft CRDT, and presence retain their independent protocols and state owners.

After authentication, strict version-1 commands subscribe or unsubscribe a client-generated `channel` identity, or acknowledge heartbeat with `pong`. A subscription names `summary` or `focus`, an exact root ID only for focus, and an optional opaque `after` cursor. Version-1 output wraps the existing frame union with that channel identity; heartbeat is a separate `ping`. The generated OpenAPI interactive extension owns the serialized schema. Each connection admits one channel per root and one summary channel. Process-local preparing, running, and cancelling roots have reserved capacity; at most twelve other channels, including summary, share the idle allowance. Idle capacity is rechecked on admission and heartbeat so completed roots do not accumulate indefinitely; overflow releases the oldest idle observations. Invalid commands close that connection explicitly; duplicate or over-budget admission resets only the requested channel.

Each channel has independent replay, cutover, gap, and reset semantics. Replacing a channel cancels only its observation and clients ignore late frames from obsolete channel identities. A normal root Run switch acquires a new focused snapshot and finite replay on a replacement channel, without physically reconnecting or resetting summary and other roots. No cross-channel ordering is promised. Closing the socket releases its subscriptions, never producing Runs.

A valid summary resume emits `open` with `resumed: true` and then missed hints; it does not request a global HTTP refresh. Fresh open uses `resumed: false`. Epoch change, cursor expiration, and subscriber overflow reset only the affected channel; clients discard that cursor and reconcile that scope before following again. The server sends periodic heartbeats, bounds input inactivity and socket-send waits, and never waits for delivery inside execution publication. A stalled physical socket eventually closes; its channels subsequently resume independently with bounded reconnect backoff. Listener shutdown closes realtime delivery before App cleanup.

Thread hints are coalesced into one-to-one-hundred-ID `POST /api/threads/activity/lookup` requests returning detached navigation rows through the same projection as activity pages, without continuation hydration. A content-only change replaces that Thread's loaded rows rather than refetching every Project and page. Changes affecting membership, navigation order, archive state, or search reconcile only affected collections. If a lookup fails, clients reconcile the observed collections once because membership is unknown. Search remains server-owned. Activity pagination includes the active collection only on the first page, not every historical page. Followed-result reconciliation looks up dirty IDs; fresh subscriptions, explicit recovery, and restored foreground attention can reconcile the complete interest set. These transient caches and hints confer no execution authority and add no durable token log.

Every authenticated JSON and OpenAPI response uses `Cache-Control: no-store`. Recognized browser navigation paths continue to serve `index.html` with mandatory revalidation on direct load, refresh, and package replacement; the browser resolves the corresponding workbench view without starting an Agent operation merely because a route was loaded. Content-hashed JavaScript, CSS, font, icon, editor, and worker assets use long-lived immutable caching. Asset misses, unknown `/api` routes, and unknown health routes remain explicit HTTP failures and never fall back to browser HTML.

The [WebUI contracts](webui/README.md) own browser navigation, configuration presentation, shared prompting, native human interaction, and bundled distribution. They do not add a second root execution lifecycle.

Source views expose current digests as read/provenance facts. Configuration-file saves and setup apply require no expected source or generation digest and use the [last-write-wins file boundary](01-configuration-and-resource-catalog.md#file-mutation-and-last-write-wins), including when a manual or API edit intervenes. Project definition paths enter through validated resource mutations. Separately enabled Host Files operates on native paths under the [computer-sharing boundary](webui/02-host-computer-sharing.md); direct source edits still require a later valid generation before changing runtime configuration.

Thread attachment transport uses authenticated `POST /api/threads/{thread_id}/attachments?name=...` with a bounded raw byte body, and `GET /api/threads/{thread_id}/attachments/{attachment_id}` for a non-inline, no-store download. The stage response supplies the handle and metadata. The submit JSON accepts `prompt` and up to eight `attachment_ids`, including attachment-only submission. It also accepts an optional configured `model_id`, mapped to the App's existing single-Run Model override; omission follows the Agent binding. The override does not mutate resources or sticky Thread configuration and is rejected on steering requests. Submit also accepts an optional `environment_profile_id` for a built-in or configured profile, following the [Run-only Environment selection and deferred-resumption rules](04-projects-threads-and-environments.md#configuration-patch-and-run-admission). This selection is also rejected on steering; the existing selector catalog and captured Run inspection expose its available choices and effective value. The selector catalog exposes configured Model IDs, names, and routes without credentials or a connectivity claim. Failed validation is explicit and never silently drops an attachment. The attachment transport does not accept native paths as an input source or duplicate storage, image validation, input conversion, or pruning policy. The shared browser composer reuses this attachment boundary rather than inventing browser-owned attachment authority. Native file and Git diff capture stage reviewed bytes with the [computer-sharing provenance](webui/02-host-computer-sharing.md#captured-diff-context) through this same owner. Git repository discovery, status and selected diffs are App-owned read-only observations, not Agent Environment operations or another repository state store.

## Failure and Shutdown Semantics

| Condition                                    | Outcome                                                                                                        |
| -------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Invalid source candidate                     | Previous accepted generation remains active; diagnostics identify the source                                   |
| Concurrent App or explicit-import file write | Last write to the selected source path wins; no source-version conflict                                        |
| Listener bind or access-option failure       | Web startup fails before accepting requests                                                                    |
| Missing or incorrect Web API key             | Request or stream connection is rejected before invoking `HarnessUiApp`                                        |
| Root composition or credential failure       | Run fails before model dispatch; prior continuation remains selected                                           |
| Root process loss                            | Receipts, active input, and partial output disappear; prior continuation remains selected                      |
| Child admission persistence fails            | Delegate or resume is rejected before acceptance                                                               |
| Child terminal persistence fails             | Execution is not reported as succeeded                                                                         |
| Provider or extension cleanup fails          | Failure is reported independently; known state and continuation publication still proceed                      |
| App graceful-drain timeout expires           | Remaining local tasks receive cooperative cancellation; saved nonterminal facts do not become invented success |

## Invariants

01. `HarnessUiApp` is the only local application boundary.
02. Direct editing, setup, internal workspace bootstrap, and explicit imports converge on one desired-resource generation through App-owned validation and last-write-wins file publication.
03. Surface values are detached and never expose native runtime or storage authority.
04. Thread metadata and Thread configuration use independent compare-and-select heads; active Run compositions are immutable.
05. Root receipts and controls are exact and process-local; they are not durable work acceptance.
06. A selected deferred request set can be continued only by an exact complete response batch.
07. Root and child Runs use fresh native collaborators.
08. A child can resume with a different current composition while retaining the same Harness Thread history.
09. Saved nonterminal status never proves liveness or authorizes takeover.
10. Root-lineage live delivery and summary invalidation never become continuation authority.
11. Adapters cannot use workspace selection or history navigation to bypass immutable Project identity or expected-digest file authority.
12. The stock executable starts no HTTP listener and exposes no implicit network authorization boundary.
13. Summary subscriptions carry invalidations; detailed live events remain root-lineage scoped and bounded.
14. Terminal presentation has no execution authority; future transports reuse the detached App boundary.
15. Shutdown is bounded and does not invent completion.

### Thread Usage Projection

`App.thread_usage` returns detached observed usage for a root Thread family, including bounded model and recent-Run breakdowns. Its persistence, deduplication, attribution, and coverage semantics are owned by [Observed Thread Usage](03-local-storage-and-recovery.md#observed-thread-usage). This read-only projection does not control active Runs or reconstruct invoices from conversation history.
