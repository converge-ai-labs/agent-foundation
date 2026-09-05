# Client Runtime and Data

## Design Position

The browser runtime separates retained App projections, provisional live presentation, route selection, and unsaved user input. No JavaScript store combines these categories into a convenient but ambiguous global object. Each value has one authority, one refresh path, and one failure behavior.

TanStack Query owns bounded HTTP query results. A focused Thread controller owns one subscribe-before-query Thread snapshot plus ordered detailed events for the selected root lineage. TanStack Router owns linkable selection. React feature state owns drafts and dialogs. Commands cross the generated HTTP client and become authoritative only when `AgentUiApp` accepts them.

## HTTP Contract and Generated Client

The Web adapter publishes a versioned OpenAPI document derived from the same strict request and surface projection models used by its routes. Repository generation produces TypeScript operation types, runtime decoders, and low-level request functions consumed by the WebUI. Generated files are mechanical and never manually extended; handwritten feature services compose generated operations without redefining payload fields.

All production requests use same-origin relative `/api` URLs and `cache: "no-store"`. Feature code supplies domain arguments to one client boundary rather than calling `fetch` directly. That boundary adds the current Bearer key, negotiates the supported JSON representation, applies cancellation, validates bounded success bodies, and maps the common error envelope.

A response that fails its generated runtime schema is a protocol error. The browser does not render partial unknown data, guess renamed fields, or silently coerce a stale cached shape. An API version or schema mismatch blocks affected operations and presents reload/restart guidance.

The generated client covers finite queries and commands. The summary and focused stream clients share its authorization and error mapping but own SSE parsing and cursor behavior explicitly.

## Access Bootstrap

The access key can enter the browser through either:

1. the generated-key convenience fragment `#api_key=<percent-encoded-key>` printed by `a13n-ui webui`; or
2. a manual access form for a user-supplied key, a copied generated key, or a new browser tab.

Bootstrap runs before the router and before any request instrumentation. It parses the fragment, rejects duplicate or malformed key fields, writes a replacement history entry with the complete fragment removed, and only then makes the value available to the application. The key is retained in origin-scoped `sessionStorage` for the lifetime of that browser tab. It is never copied into a route, search parameter, query key, query result, DOM attribute, analytics payload, error detail, console message, or application log.

The shell verifies access with a small authenticated App-status query. Outcomes are:

| Outcome                             | Browser behavior                                                                                      |
| ----------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Authenticated success               | Start the summary stream, complete its first synchronization barrier, then mount the intended route   |
| Success with listener bypass active | Do the same without retaining a key and show a persistent unauthenticated-listener indicator          |
| `401`                               | Remove the retained key and show the access form while preserving the intended client route in memory |
| Network or server unavailable       | Keep the key, show reconnect state, and do not misreport it as invalid                                |
| Protocol mismatch                   | Block application mounting and require a matching page/process reload                                 |

An explicit sign-out action clears `sessionStorage`, cancels requests and streams, clears the query client and focused controller, and returns to the access screen. It does not alter the App process or its generated key. Closing one tab has no server effect.

## Client State Ownership

| State                                                                               | Owner                      | Lifetime                            | Update path                                                           |
| ----------------------------------------------------------------------------------- | -------------------------- | ----------------------------------- | --------------------------------------------------------------------- |
| API key                                                                             | Access bootstrap           | Browser tab                         | Fragment or manual entry; cleared on `401` or sign-out                |
| App, Project, Thread, resource, catalog, account, and diagnostic projections        | TanStack Query             | In-memory page runtime              | Query response and explicit invalidation/refetch                      |
| Current route, Settings filters, stable activity selections, and context-panel mode | TanStack Router            | Browser history                     | Typed navigation                                                      |
| Picker search, archive selection, Project scope, and pagination                     | Picker feature             | Mounted shell                       | Local navigation and bounded queries                                  |
| Focus snapshot and detailed live projection                                         | Focused Thread controller  | One mounted root-Thread route       | Subscribe-before-query snapshot then ordered stream reduction         |
| Current composer or new-Thread draft                                                | Conversation shell         | Browser page or explicit discard    | Local user input; retained across Thread switches and Settings return |
| Decision, dialog, and source drafts                                                 | Owning feature component   | Mounted feature or explicit discard | Local user input                                                      |
| Theme preference                                                                    | Design-system bootstrap    | Browser profile                     | Explicit light, dark, or system selection                             |
| Server receipt, version, digest, continuation, and cursor values                    | Owning returned projection | No independent lifetime             | Replaced only by a later App response or stream frame                 |

The query cache is not persisted to `localStorage`, IndexedDB, or a service worker. Resource drafts, message drafts, API projections, operation receipts, stream cursors, and account status likewise receive no browser persistence. The shell retains per-Thread and new-Thread drafts, their revision, new-conversation Project/Agent/Environment selections, pending admission, last receipt, and unknown-outcome status in page memory across navigation; reload, sign-out, or explicit discard removes it. Browser reload reconstructs App projections from the App and never replays an unacknowledged command.

## Query Model

Query keys are canonical tuples based on operation kind and normalized request parameters. They never include secrets or display-only state. Representative families are:

```text
app status
accepted configuration and source diagnostics
catalog references
Projects
recent, Project-scoped, and unresolved-Project root-Thread pages by normalized filter and cursor
root Thread detail by Thread ID
transcript pages by Thread ID, exact selected continuation, direction, and cursor
child execution pages by root or parent scope and cursor
resource source by kind and ID
compatible account status by provider
```

List pagination preserves the App's opaque keyset cursor. The browser neither decodes cursors nor synthesizes offsets. A Project-scoped key includes the exact Project ID; Recent and unresolved-Project keys name their independent derived query families. Ordinary data queries invalidate on relevant summary events. An open picker preserves its filter, scope, and loaded navigation pages: explicit reopen or filter change restarts from the first page, while Load more preserves the opaque cursor. Navigation rows are not execution authority. A detail invalidation can refetch one identity without flushing unrelated families.

A transcript request starts only when the focused snapshot names a selected continuation and carries that exact continuation ID as an expected precondition. Its query key includes Thread ID, selected continuation ID, direction, and normalized page shape. When the focused snapshot or authoritative detail reports a different continuation, the browser removes the complete prior transcript pagination and starts a new latest-page query; it never appends or reuses pages across continuations.

Query defaults favor freshness over background churn:

- mounting a route fetches missing or invalidated authoritative projections;
- window focus can refetch finite summaries but never starts a command;
- finite idempotent queries can retry a small bounded number of transport failures;
- validation, authentication, conflict, missing-resource, and protocol failures are not retried automatically;
- leaving a route aborts irrelevant in-flight queries where the transport permits it.

## Command Model

Commands are explicit user actions. The browser does not automatically retry message submission, deferred response, source mutation, deletion, metadata/configuration update, account change, import apply, steering, or cancellation after an ambiguous transport failure.

A command button enters a local submitting state that prevents duplicate activation. On a definitive success, the returned projection updates or invalidates the exact query families named by that operation. On a definitive App error, the local draft remains available when safe. On a transport failure after dispatch may have occurred, the browser labels the result unknown, preserves that fact across route changes, and refetches the relevant authoritative projection. Another submit requires explicit user acknowledgement after reviewing current state. A schema failure after a mutation may also leave its outcome unknown; decoding failure never proves non-dispatch.

Compare-and-select commands always use the exact precondition from the projection or source snapshot that created the draft:

- resource mutations use source digest;
- Thread metadata uses metadata version;
- Thread configuration uses configuration version;
- deferred response uses selected continuation ID;
- root control uses receipt ID;
- child control uses execution ID and parent scope.

The browser never increments a version optimistically or substitutes a newer precondition into an older draft. Conflict resolution is an explicit rebase flow owned by the corresponding feature.

A submission acknowledgement consumes only the exact draft revision captured at dispatch. Text entered while the request is pending is not cleared or overwritten. Create/admit outcomes transfer to the created Thread without replacing newer new-Thread text. A completed request may navigate to the created Thread only while its originating route instance remains selected. Sign-out clears all tab-local state; late results from the old access session cannot repopulate it.

An HTTP success containing `accepted=false` is a rejected control, not accepted input. Steering rejection preserves the prompt and never falls through to a new Run or replacement receipt. Setup publication temporarily blocks route navigation and warns on unload; a lost apply response requires file inspection and a new preview, not automatic replay.

## Summary Stream

One App-wide summary SSE connection exists after access succeeds. It sends a typed stream-open frame containing the current epoch and cursor, followed by bounded invalidation frames. Each invalidation maps to an exact query family or identity. The client schedules invalidations through TanStack Query and coalesces a burst affecting the same key.

The first stream-open frame, every connection without a resumable cursor, and every reset establish a subscribe-before-query barrier. After accepting that frame, the client cancels in-flight summary queries, invalidates every summary family, and refetches active summaries before marking them fresh. Initial route queries start only after the first barrier. This prevents a query response observed before subscription from remaining current after its invalidation was missed.

Summary frames do not modify cached values directly. Missing frames, an expired cursor, changed epoch, malformed data, or an explicit reset frame cause the client to repeat the full barrier and establish a new stream. A disconnected summary stream displays a quiet stale-data indicator but does not block commands whose current projections remain usable; their App preconditions still decide acceptance.

The summary stream reconnects with the opaque `resume_cursor` returned on its last accepted frame, passed as `after`; the browser does not decode or construct it. Reconnect uses a capped retry delay; authentication and schema failures require explicit recovery rather than aggressive reconnect. Authentication and protocol failures do not enter the reconnect loop.

## Focused Thread Stream

Exactly one selected root Thread route owns a focused stream. Picker collections and Settings routes own none. Opening the selected route starts one authenticated fetch-based SSE request and consumes the frame union owned by the [HTTP Adapter Contract](../05-runtime-subagents-and-surfaces.md#http-adapter-contract). Conversation, activity, and contextual detail reuse that mounted controller; changing only those selections never opens a competing watch. Moving to another Thread aborts the prior focused stream before the destination establishes its fresh watch, while the App-owned Run continues independently.

The first frame of a fresh focused watch is exactly one snapshot. Its epoch and `cutover_sequence` initialize the focused controller. `recent_events` supplies at most 128 KiB of encoded events from the retained process-local ring at or below cutover. This is an explicitly bounded provisional tail, not complete current output or saved history. A switch cannot recover events already evicted from that ring. Later event frames must use that epoch and a strictly increasing global sequence greater than the cutover. Because unrelated root lineages share that global sequence, numerical gaps are expected and do not indicate loss. Duplicate frames at or below the accepted sequence are ignored. An explicit reset, changed epoch, malformed payload, or sequence regression discards the complete provisional layer and opens a new focused watch; the browser never guesses missed events from a number gap.

Each snapshot/event frame carries an opaque `resume_cursor`. A transport reconnect passes it as `after` to request events after the last accepted cursor, bound to the exact focused root lineage and stream shape. If retained replay is available, the response emits only later event frames and the same controller continues without accepting a replacement snapshot. If the cursor is unavailable, the adapter returns reset semantics and the client discards that controller before opening a fresh watch for another subscribe-before-query snapshot. Route changes abort the previous connection and discard its controller; events arriving after abort cannot update the newly selected Thread.

## Focused Reduction and Reconciliation

The focused controller contains:

- the detached snapshot;
- the last accepted epoch and sequence;
- provisional root activity grouped by exact Run and receipt correlation;
- provisional child activity grouped by exact execution and Run correlation;
- open and closed text, reasoning, tool, custom, and lifecycle blocks;
- explicit stream connection and reset status.

One pure reducer validates and folds standard AG-UI and namespaced `CUSTOM` events. It never mutates TanStack Query data. Unknown event types remain visible as safe generic activity when their bounded envelope validates; they do not acquire custom execution semantics.

Live content is visually distinguished from retained transcript and saved child checkpoints. Terminal live events do not by themselves prove continuation selection, Environment-state publication, child checkpoint commit, or operation completion. A root-operation or child-execution terminal invalidation triggers authoritative detail, transcript, and child refetch. The controller keeps the provisional layer until the replacement retained projection accounts for it or a new snapshot resets it, preventing a visible blank between terminal event and persistence.

High-frequency deltas can be batched to one render per animation frame. Ordering is preserved before batching, and controls use the most recent exact App projection rather than a delayed visual block.

## Multiple Tabs and Processes

Each tab owns an independent query cache, streams, drafts, and `sessionStorage` key. No BroadcastChannel, shared worker, browser leader election, or cross-tab command queue coordinates tabs. Multiple tabs and multiple Agent UI processes meet through App and storage compare-and-select behavior only.

A summary event observed in one tab does not prove another tab received it. A stale tab refetches or receives an explicit conflict. The WebUI never weakens an expected digest or version because it believes it is the only browser.

## Failure Semantics

| Failure                                                 | Browser outcome                                                                                    |
| ------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| API key rejected                                        | Clear key, cancel client activity, and return to access without classifying the App as unavailable |
| App unavailable or restarting                           | Preserve access material and intended route; show reconnect state without replaying commands       |
| Finite query transport failure                          | Show stale or empty error state and permit explicit retry                                          |
| Mutating request has unknown outcome                    | Refetch authoritative state before another user-confirmed attempt                                  |
| Compare-and-select conflict                             | Preserve the draft and enter the owning explicit rebase flow                                       |
| Summary cursor reset                                    | Invalidate summary query families and reconnect from a new epoch                                   |
| Focused cursor reset, epoch change, or invalid sequence | Discard provisional live state and establish another subscribe-before-query focused snapshot       |
| Runtime schema mismatch                                 | Reject the body or frame and show protocol incompatibility; never partially render it              |
| Route changes during a request or stream                | Abort old work and prevent late completion from updating the new route                             |

## Invariants

01. Retained query data, focused live data, routes, and drafts never share one ambiguous global authority.
02. Generated HTTP types and runtime schemas derive from the adapter contract; feature code does not redefine wire DTOs.
03. The API key leaves the fragment before router startup and remains tab-scoped in `sessionStorage` only.
04. No browser persistence can replay a command or claim a durable receipt after reload.
05. Commands with side effects are never retried automatically.
06. Exact App preconditions are passed through unchanged and are never advanced optimistically.
07. Summary frames invalidate; they do not patch domain values.
08. Focused frames reduce only after one subscribe-before-query snapshot and one verified sequence cutover.
09. Sparse global sequence values are valid after lineage filtering; only explicit reset or invalid cursor/epoch semantics discard provisional state.
10. Conversation, activity, and contextual detail for one root Thread share one focused controller and never maintain competing detailed subscriptions.
11. Live terminal presentation is reconciled against retained App projections before it is treated as closed truth.
