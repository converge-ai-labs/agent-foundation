# Harness UI HTTP API

The optional browser server exposes a local App API. Its bundled browser currently implements workbench entry, setup, provider-account/key management, configuration editing, Project readiness and page presence. These endpoints do not imply that browser chat, Host Files, Git or terminal panels are implemented.

This is **not Service Native `/api/v1`**. It has a shared instance access key, process-local operation receipts, and best-effort live subscriptions. Use the [browser-server guide](webui.md) to start/configure the listener and [Python embedding guide](embedding.md) for App ownership.

## Authenticate and discover the contract

Set `HUI_URL` to the listener origin and `HUI_API_KEY` to its configured access key:

```bash
curl --fail-with-body "$HUI_URL/api/status" \
  -H "Authorization: Bearer $HUI_API_KEY"

curl --fail-with-body "$HUI_URL/api/openapi.json" \
  -H "Authorization: Bearer $HUI_API_KEY"
```

The status contract has `api_version: "1"`, package/build information, App status, access mode, and feature flags. The live OpenAPI JSON describes exact request/response models and constraints. Swagger and ReDoc pages are disabled. The [checked schema](../assets/reference/harness-ui-openapi.json) is generated from the source version, not proof of another running version.

Authentication and Host/Origin validation apply at the listener boundary. Use a header-capable HTTP/fetch client. Do not put access keys in API query strings or logs, or confuse model-provider credentials managed under `/api/auth/*` with the listener key. The deliberate dangerous-bypass mode is not a production authentication mechanism.

## Coordinator

`ThreadSummary.role` is `ordinary`, `coordinator`, or `worker`. `coordinator_thread_id` identifies a worker's owner (otherwise null); `auto_followup` is the Coordinator's notification setting (otherwise null). Projects can contain multiple Coordinators and expose no singleton lead fields.

`POST /api/threads/{thread_id}/coordinator` promotes an existing ordinary project-bound root. It requires an accepted Project, an unarchived inactive Thread and no pending decisions. It is idempotent for an already eligible Coordinator, returns `ThreadSummary`, and starts no Run or Model call. Promotion keeps identity, history, title and settings. Workers and children cannot be promoted; there is no demotion or worker adoption API.

`PATCH /api/threads/{thread_id}/coordinator` with `{"auto_followup": false}` pauses that Coordinator's automatic lifecycle notifications; true enables them. Updates are last-write-wins and publish Thread invalidation. Sidekick configuration does not gate promotion or follow-up. Archived Coordinators retain their role and use the ordinary expected-version metadata API for restoration. Coordinators and workers cannot change or clear their Project.

`GET /api/threads/activity` accepts `coordinator_thread_id` to select one owner's workers and `independent_only=true` to exclude workers while retaining ordinary roots and Coordinators. Filters bind cursors and apply before totals and active rows. Unfiltered human search includes all roots. Ownership is assigned only by the Coordinator's collaboration tool, never by public HTTP creation. Human inspection and execution keep the normal root APIs.

The old Project `/lead` routes and fields are removed. Startup migration converts existing lead bindings and ownership once, preserving IDs and history; an old disabled binding becomes a Coordinator with automatic follow-up off. Other saved conversations remain ordinary roots.

## Run-only execution environment

`POST /api/threads/{thread_id}/submit` accepts an optional `environment` patch alongside the prompt or ordered input parts:

```json
{
  "prompt": "Review this project",
  "environment": {
    "environment_profile_id": "environment-sandbox",
    "local_roots": ["/absolute/path/to/code"],
    "environment_bindings": [],
    "default_environment": "workspace"
  }
}
```

Use `environment-native` for Full Control, `environment-sandbox` for Sandbox, or a profile ID from `GET /api/selectors`. Omitted axes use the Thread's saved selections; empty collections clear selections and null clears only `default_environment`. An explicit choice affects this Run without updating the Thread configuration or version. A returned receipt acknowledges admission, not successful environment preparation: inspect the operation for unavailable profiles or runtime failures. Neither case silently falls back to Full Control. Steering rejects this field. Deferred responses retain the suspended continuation's captured environment selections; a later ordinary submit without an override uses the saved default again.

## Device connections and working environments

A Device resource describes a reusable connection, not a Run or a filesystem root. Create it through `PUT /api/configuration/sources/devices/build.yaml`; see [Device YAML](environments-and-projects.md#add-device-working-environments). Device credentials are separate from the browser listener key.

| Route                                      | Behavior                                                                                           |
| ------------------------------------------ | -------------------------------------------------------------------------------------------------- |
| `GET /api/devices`                         | Configured IDs, names, and carrier kinds; no connection attempt                                    |
| `GET /api/devices/{device_id}`             | Check availability and read path style, default working directory, and directory-discovery support |
| `GET /api/devices/{device_id}/directories` | Browse one directory level without opening a Run Session                                           |
| `WS /api/devices/{device_id}/connect`      | Native daemon reverse attachment, not a browser interactive channel                                |

Directory queries accept `path`, `offset` (0–1,000,000), and `limit` (1–200, default 100). Omitted `path` uses the Device's default working directory; use returned `next_offset` for another page. Paths are Device-absolute EIP paths, not browser or listener-native paths. Availability and discovery are separate: an online Device may disable discovery, and an unreadable directory does not make it offline. Metadata and browsing do not create Threads or execution Sessions. These routes do not depend on native computer sharing.

The daemon's reverse connection negotiates `eip.v1` and sends its Device bearer credential in the HTTP upgrade request. It does not use browser first-frame authentication. Do not send credentials in query parameters.

Thread creation defaults and configuration patches accept `environment_bindings` and `default_environment` independently of `environment_profile_id`:

```json
{
  "project_id": null,
  "environment_bindings": [
    {"device_id": "device-build", "alias": "build", "working_directory": "/work/repository"}
  ],
  "default_environment": "build"
}
```

Use this body directly for configuration preview, or under `defaults` when creating a Thread. A known absolute directory can be saved while its Device is offline. Each Run captures its selections and prepares fresh execution-owned Sessions; a configured connection is not proof that execution will succeed. `default_environment` chooses relative-path and omitted-cwd routing; it is not a confinement boundary. Native Files, Changes, and Terminal routes continue to operate on the listener Host, not the selected Device.

### Forget offline configuration and repair selections

`DELETE /api/configuration/sources/devices/build.yaml` forgets the local connection resource without contacting the Device or deleting remote files. The same source deletion API removes custom Environment profiles. Built-in profiles are not removable source files. First remove references from current Project/global defaults through ordinary source updates; otherwise candidate configuration validation rejects the deletion.

Existing Thread selections and historical captures are not cascade-edited. Inspect `/api/threads/{thread_id}/configuration`, then explicitly PATCH the next selections with the current `expected_version`. For example, remove a missing Device and replace its default together:

```json
{
  "expected_version": 1,
  "patch": {"environment_bindings": [], "default_environment": "thread-files"}
}
```

For a Thread with selected local roots, `workspace` is another valid replacement. Omitting the replacement while removing its selected default fails rather than silently redirecting future tools. Forgetting configuration or repairing next selections does not rewrite an active capture, saved Run, transcript, or continuation.

## Conversation input navigation

`GET /api/threads/{thread_id}/inputs` returns a continuation-bound, paginated directory of ordinary input turns, excluding steering and hidden system input. Each turn carries its stable input identity, bounded preview, input position, exclusive end position, and an optional recorded final-response position. `limit` is 1–100; follow `next_cursor` to read the directory without transferring tool output.

`GET /api/threads/{thread_id}/transcript` accepts an optional `turn_id` for its initial page. `next_cursor` reads earlier messages and `newer_cursor` reads later messages. Both cursors and `expected_continuation_id` remain bound to the observed history. Refresh after a continuation mismatch; never combine positions from different heads. A page also includes the intersecting `turns` and any `boundary_entries` outside its ordinary page range needed to show the original input and final answer. Deduplicate entries by position. Missing intermediate entries are still paginated history, not evidence that a turn had no process output. A final position comes from successful saved execution, not from the last assistant text or a live text-end event.

## Native terminal

On Linux/macOS, native computer sharing includes a real interactive terminal. Check `features.host_terminal`; Windows returns false and `host_terminal_unavailable`, not a noninteractive substitute. A missing shell is also unavailable. Disabling sharing returns `host_terminal_disabled`. Create with `POST /api/host/terminals` and JSON such as `{"cwd":"/work","rows":24,"columns":80}`. An optional `project_id` must identify an accepted Project. The returned `terminal_id` belongs to this App lifetime; it is not a Thread or Run ID. `cwd` records the initial native directory and never follows later browser navigation.

Connect to `ws(s)://<listener>/api/host/terminals/{terminal_id}/connect?cursor=<last-end>` using the same listener origin. The cursor is optional and contains no credentials. Send `{"api_key":"<instance key>"}` as the first text frame within ten seconds. The Host and exact Origin are checked before acceptance, and authentication precedes resource lookup. Wrong/missing keys close with code 4401; rejected Host/Origin fails the handshake. In dangerous-bypass mode send `{}`. Do not use URL keys, cookies or model-provider credentials.

The generated OpenAPI document's `x-interactive` section references the authentication, command, output and error schemas. After authentication, the server emits `TerminalFrame` objects with a connection-local `participant_id`, current `terminal` view and raw base64 output. Preserve a streaming UTF-8 decoder across adjacent frames; byte positions are not character offsets. Keep `end` as the next reconnect cursor. At most 1 MiB is retained; `gap: true` means bytes are missing or the supplied cursor was ahead, not that a full screen was recovered.

Every connection starts as a viewer. Send a command using the latest observed `control_epoch`:

```json
{"kind":"control","control_epoch":0}
```

A successful claim/takeover increments the epoch and broadcasts the new controller. Once you own epoch 1, input and resize look like:

```json
{"kind":"input","control_epoch":1,"text":"pwd\n"}
{"kind":"resize","control_epoch":1,"rows":40,"columns":120}
```

Input supports control characters such as `\u0003` for Ctrl+C. Use `{"kind":"control","control_epoch":1,"release":true}` to release control. Only the current controller may release it; another viewer may explicitly take over using the current epoch. Stale commands return `host_terminal_control_conflict`. Input is bounded to 16,384 characters per frame; rows/columns are 1–1,000. Backpressure may return `host_terminal_input_failed` after partial delivery. Never retry keystrokes automatically, including after losing the connection or an acknowledgement.

Disconnect removes only that participant and releases its control. Rejoin gets a new participant ID and retained output. Process exit stays inspectable as `exited`; explicit HTTP DELETE closes the PTY, terminates its native session jobs and removes the identity. Deliberately daemonized independent OS sessions are outside this lifetime. Up to 32 sessions, including exited sessions, can exist until closed. App shutdown closes them all; restart retains neither PTYs nor their output. This does not change persisted conversation history.

## Page presence

`features.page_presence` advertises an App-global transient participant directory, including when no Thread is open. `GET /api/presence` returns the current detached directory. Connect to `/api/presence/connect` with the terminal's first-frame authentication. Each connection gets a fresh `participant_id`; two tabs using the same name remain separate participants. Neither names nor colors verify authorship or grant authority.

After authentication, send a `PresenceReport` describing the tab's current focused pane:

```json
{
  "kind": "presence",
  "display_name": "Alice",
  "color": "#112233",
  "foreground": true,
  "focus": {
    "target": {"kind": "conversation", "thread_id": "thread-example"},
    "root_thread_id": "thread-example"
  }
}
```

Targets use semantic identities from existing App projections: workbench `home`/`settings`/`catalog`, a root conversation, Project, configured resource, native file, Git comparison, or terminal. Native paths remain on the server; use the canonical paths returned by Files/Git. The optional containing `root_thread_id` is separate from the focused target. A Changes target uses the exact repository root, optional repository-relative path and comparison. A missing/disabled target stays in the report with `availability: unavailable` and a reason; the server does not navigate to a replacement. Presence inspects availability, not file contents, and never creates a terminal or changes an Agent Environment.

Set `foreground: false` when hidden or unfocused. Report changes immediately and resend the current report at least every 20 seconds, even when unchanged. A connection without a report for 60 seconds closes with 4408 and loses its membership. This measures transport participation, not human or Agent liveness. There are at most 32 live participants and reports are bounded to 16 KiB. Invalid reports return `presence_invalid` without replacing accepted presence.

`PresenceFrame` carries the directory and `same_page_participant_ids` relative to that connection. Matching uses the focused target, not scroll position, layout, or containing Thread. HTTP can supply `participant_id` to obtain the same grouping without changing membership. Snapshots are refreshed on membership changes and periodically (15 seconds) to recheck resource availability. Disconnect removes only presence; reconnect reports a fresh current location, not navigation history. App restart clears the directory. A client forgetting its access key closes its interactive connections.

Page presence is independent of the draft's editor cursors, saved comments and execution observation. Focusing Files does not clear or move a Thread draft. Opening a collaborator's location is an explicit personal navigation action; no follow mode or forced scroll is provided.

## Saved output comments

`features.output_comments` exposes editable human comments on **saved visible assistant text only**. It does not make live or unsaved output durable. Root transcript assistant parts include a nullable `comment_target` and an explicit `text_truncated` flag; truncated display excerpts are not exact-selection sources. Fetch original windows for exact selections instead. User/tool/thinking parts and initial/unsaved history do not. Saved child inspection uses `GET /api/threads/{parent_thread_id}/children/{execution_id}/saved-output`, with up to 20 blocks per page and a source-bound `next_cursor`. Use these detached targets unchanged rather than guessing indices or converting a live event into an object reference.

Publish with `POST /api/threads/{root_thread_id}/comments`:

```json
{
  "comment_id": "comment-0123456789abcdef0123456789abcdef",
  "target": {
    "producing_thread_id": "thread-example",
    "source_id": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "location": {"kind": "root_text", "message": 1, "part": 0}
  },
  "author": {"display_name": "Alice"},
  "body": "Please explain this conclusion."
}
```

Allocate a fresh client identity once per intended comment: `comment-` followed by 16–64 alphanumeric, underscore or hyphen characters (a UUID hex value works). Body and selected quote each allow at most 16,384 characters; display names allow 80. The optional author `participant_id` is unverified correlation and need not remain connected. Omit `selection` to discuss the whole block; otherwise supply `{"start":0,"end":5,"quote":"exact"}` matching the exact source's Unicode code-point range, before Markdown rendering. JavaScript UTF-16/DOM offsets must be translated; ambiguous selections should use whole-block comments. Oversized text is rejected, not truncated into a different anchor.

Acknowledgement follows SQLite commit. Repeating the same identity and canonical publication returns the original record and creation time, including after reconnect or restart. Different content under that identity returns `409 comment_identity_conflict`. A lost response is reconciled by GET or repeating the same identity, never by automatically allocating another. First publication rechecks source selection at commit and returns `409 comment_target_stale` when it changed. A previously commented block remains a valid retained target after later Runs. Posting comments neither admits a Run nor changes Thread metadata/configuration versions, continuation, decisions or model messages.

`GET /api/threads/{root_thread_id}/comments` lists all comments, including those on older sources, with `limit` (1–100, default 20) and an opaque cursor. Ordering is ascending creation time then comment identity by default; `newest_first=true` reverses both for a newest-first discussion. Cursors are bound to the ordering direction. Optional `target` is the JSON-encoded exact target; a cursor is bound to its Thread and filter. GET `.../comments/{comment_id}` reads one publication. Empty history is an empty collection. The realtime summary channel emits best-effort `kind: comment` invalidations after commit; reconcile after a fresh subscription or reset rather than treating its cursor as a durable comment cursor.

POST the target to `/api/threads/{root_thread_id}/saved-output` for the original text. `offset` and `limit` select at most 65,536 Unicode code points; `total_characters` and `next_offset` disclose clipping. This read permits only a currently selected or comment-retained target in that Thread family, not arbitrary immutable objects. A broken source fails explicitly while its comment remains readable. Identical text in a newer continuation is not the same target: show the Thread comment list and original-output view unless exact inline identity is established. Reading original output does not select it for execution.

Comments return `version` (initially 1) and optional `updated_at`. `PATCH .../comments/{comment_id}` accepts only `{"body":"Revised feedback","expected_version":1}`; identity, target, selection and attribution remain fixed. `DELETE .../comments/{comment_id}?expected_version=1` returns 204 after deletion. Stale edits/deletes return `409 comment_version_conflict`. An identical edit retry reconciles the next version; repeated deletion succeeds without resurrecting the record. All trusted instance participants can edit/delete; author names are not access-control identities. Comment bodies are not jointly editable and there are no reply/resolution workflows. `POST /api/threads/{root_thread_id}/comments/{comment_id}/capture` explicitly captures one complete publication (body, selection and attribution) plus its complete original assistant block into a Thread attachment. Supply `?expected_version=<reviewed version>` to reject changes before capture. It returns the existing `ThreadAttachment` with `source.kind: comment_reference`, root/comment IDs and the exact saved target. The additive top-level `comment` metadata holds bounded author/body/quote previews and the captured version. Existing captured bytes stay unchanged after edits or deletion. It requires neither native sharing nor a model-side Capability. Complete UTF-8 content, including attribution, must fit 64 KiB; unavailable sources or oversized content fail without truncation. Capture never modifies the shared composer or admits execution. The browser explicitly selects the returned attachment ID only after success.

Ordinary submit and root steering expand the immutable capture as native text with existing `harness_ui.attachment` metadata. Browsers can render an inspectable comment-reference card instead of the expanded editor text; model content remains the complete capture. Retention, eight-attachment bounds and capture-only clearing are unchanged. Publication or inspection alone is never model input.

## Shared composer

`GET /api/drafts` lists nonempty shared rooms as `{thread_id, draft_id, unsent_since}` without returning text or joining editors. Selected pending/failed attachments count; whitespace and dormant attachment registry entries do not. The timestamp changes only when an empty draft becomes nonempty. A `draft` summary invalidation identifies a Thread whose empty/nonempty membership changed; refetch this index after hints or a summary reset, then use the existing Thread lookup APIs for titles, Projects, and archive state. This index is independent of recent-conversation pagination and disappears on App restart. It is not an execution queue or durable draft storage.

`features.shared_drafts` advertises the backend protocol, not a finished browser editor. Connect to `/api/threads/{thread_id}/draft/connect` using the same first-frame authentication as the terminal. Only root Threads participate; computer sharing is not required. One App owns one in-memory document per participating Thread. Disconnect removes presence, not the document or an executing Run. App close drops drafts and presence; conversation history keeps its existing storage owner.

`x-interactive.draft` describes the JSON envelopes. The server sends `DraftFrame` with a `draft_id`, connection-local `participant_id`, a base64 **Yjs v1 full-state update**, and current participant presence. The document uses two root types: `text` (`Y.Text`, plain text only) and `attachments` (`Y.Map<string>`). Inline map keys use `inline-<UUID>`; their values are existing attachment IDs from that same Thread, or the incomplete states `pending`/`failed`. The text contains opaque `U+FFFC + key + U+FFFC` identity tokens, rendered as atomic filename controls by the browser. Only live token occurrences select inline input; dormant registry entries support undo and remain bounded by the full CRDT-state limit. Missing registry entries and incomplete states block client submission. Tokens never cross the execution-input boundary. Native file/diff captures use those same IDs, not live paths or duplicated bytes. Clients expand token occurrences in authored text order. Legacy map keys without the `inline-` prefix are still selected in sorted-key order after the text; new clients retain this read compatibility. Collaborators inspect each selected handle through `GET /api/threads/{thread_id}/attachments/{attachment_id}/metadata`, which returns the existing `ThreadAttachment` projection without downloading its bytes. It preserves the Thread scope and immutable file/diff provenance; removing a draft selection does not delete retained input files. The server uses pycrdt/Yrs; CRDT item identities and merging are library-owned.

Apply received updates to a local Yjs-compatible replica. Send `{"kind":"sync","draft_id":"...","update_base64":"..."}` with the replica's **complete** update, including dependencies and deletion sets, rather than a state-vector delta. Full updates make offline/rejoin merge and resending editing state independent of a server-side transport log. The server atomically validates the whole merged composer before publishing it. `draft_invalid` leaves server state unchanged; keep rejected local content and surface the error rather than silently dropping selections. Limits are 512 KiB encoded CRDT state, 256 Ki characters plain text, eight selected attachments, and the existing 20 MiB aggregate attachment limit. CRDT history counts toward the state limit; there is no automatic compaction that changes item identities.

Presence uses `{"kind":"presence","draft_id":"...","presence":{"name":"Alice","color":"#112233"}}`. Optional `anchor` and `head` are base64 Y relative positions, not stale character offsets. Names/colors are unverified presentation. On reconnect, merge offline editing state only when the server's `draft_id` matches the previous instance. A different identity means the previous in-memory draft is gone; keep any local recovery as an explicit user choice, not automatic execution or promised restart recovery.

Send is a client action, not a draft endpoint:

1. Synchronize pending edits and observe them in the returned CRDT state.
2. Clone the exact current replica; expand its authored text and live attachment identities into ordered `parts` for ordinary `/submit`.
3. On a positive submission acknowledgement, delete only the text and legacy selections visible in that captured clone and merge its full deletion update back into the live replica. Keep inline registry entries: an uncaptured occurrence pasted by a peer can reuse a key and arrive after acknowledgement. This preserves concurrent inserts and replaced/added selections. Do not clear the current editor by offsets or replace it with an empty document.
4. On rejection or unknown outcome, retain the draft. Never retry submission automatically. Resending a CRDT editing update is not resending an execution request.

Explicit root steering accepts ordered `parts`, or legacy `prompt` and optional `attachment_ids`, using the same attachment preparation and limits as ordinary submission. Images become native image input; ordinary files and binary or larger captures retain readable Thread attachment references. Captured NUL-free UTF-8 file/diff/comment context of at most 64 KiB also supplies attributed inline text. Attachment-only steering is valid, and ordered parts retain their authored order and metadata. A later Host edit cannot change either path's captured bytes or source attribution.

### Ordered input bodies

Root `/submit` and root `/steer` accept `parts` containing strings and `{ "attachment_id": "..." }` references in authored order:

```json
{
  "parts": [
    "Compare ",
    { "attachment_id": "attachment-first" },
    " with ",
    { "attachment_id": "attachment-second" }
  ]
}
```

Stage the bytes through the existing attachment endpoint first. References retain their original Thread-scoped IDs; the App does not duplicate uploads. A repeated ID is a distinct occurrence and counts again toward count/byte limits. The body permits at most 1024 parts and 256 Ki authored text characters in total, subject to the existing attachment limits. Empty input is rejected. Do not combine `parts` with a nonempty `prompt` or `attachment_ids`. Omitting `parts` preserves the legacy prompt-plus-trailing-attachments behavior. No client-supplied media metadata, Host paths, or editor tokens are accepted as references.

Native input content carries the existing `source_id` and `harness_ui.composer.index` presentation metadata. A single attachment can expand into text and binary pieces with the same authored index; displays group those pieces by source/index, not attachment ID alone. Live and retained-history projections preserve this metadata.

## Create a Thread and submit input

First inspect `/api/selectors` and `/api/setup` to confirm usable accepted configuration. Configure an Agent, Model credentials, and Environment profile before running; the following uses their defaults and can invoke model/tool side effects.

```bash
curl --fail-with-body "$HUI_URL/api/threads" \
  -H "Authorization: Bearer $HUI_API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"title":"API conversation"}'
```

Save the returned `thread_id` as `THREAD_ID`:

```bash
curl --fail-with-body "$HUI_URL/api/threads/$THREAD_ID/submit" \
  -H "Authorization: Bearer $HUI_API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"prompt":"Explain this project without changing files."}'
```

Ordinary `/submit` also accepts optional `model_id` and `thinking` for that Run only. Omitted or null thinking inherits the effective Model settings; false explicitly requests Off. Read the Model's `thinking` descriptor from `/api/selectors` for its accepted values, configured-default summary, and disabled reasons rather than assuming every model accepts every level. Invalid or blocked selections are rejected without fallback. Neither override updates sticky Thread configuration, and steering rejects them. Captured configuration exposes a requested-thinking summary separately from current resource defaults.

The returned `RootRunReceipt` has `receipt_id`, `thread_id`, and `submitted_at`. Read `/api/operations/{receipt_id}` until terminal status; there is no root-operation HTTP `wait` endpoint. Preparing/running is not completion. Completed/suspended/failed/cancelled describes the operation; inspect any `outcome.execution`, `outcome.continuation`, and `outcome.environment` separately.

Only one active root operation is allowed per Thread. A second submit is rejected, not queued. There is no Service-style durable acceptance/idempotency contract here. After losing an acknowledgement, read current Thread/root activity before deciding what to do; do not blindly submit the input again. After process restart, old receipts can be unavailable while the saved continuation remains readable.

## Current route map

These are all schema-listed operations; the grouped table preserves method distinctions. Exact field schemas live in OpenAPI.

| Method and route                                                    | Purpose                                                                 |
| ------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `GET /api/status`                                                   | Listener/API/App status                                                 |
| `GET /api/presence`                                                 | Current per-tab directory and optional same-page membership             |
| `POST /api/threads/{thread_id}/comments`                            | Publish or reconcile one immutable saved-output comment                 |
| `GET /api/threads/{thread_id}/comments`                             | Cursor-page comments under the root Thread, optionally by exact target  |
| `GET /api/threads/{thread_id}/comments/{comment_id}`                | Read a scoped committed publication                                     |
| `POST /api/threads/{thread_id}/saved-output`                        | Bounded selected or comment-retained original assistant text            |
| `GET /api/threads/{thread_id}/children/{execution_id}/saved-output` | Parent-scoped saved child text blocks with typed targets                |
| `GET /api/catalog`                                                  | Discovered implementation references, not configured selectors          |
| `GET /api/agents/{agent_id}/tool-proxy`                             | Static Agent-default source grouping                                    |
| `GET /api/auth/accounts/{provider}`                                 | Credential-free account/source inspection (`codex`, `grok`, `copilot`)  |
| `GET /api/auth/accounts/{provider}/sources`                         | Supported saved account/source choices; currently Copilot               |
| `PUT /api/auth/accounts/{provider}/selection`                       | Explicit `{source, account_id}` selection; no client-supplied file path |
| `POST /api/auth/accounts/{provider}/models`                         | Explicit Copilot authenticated catalog; Chat Completions IDs only       |
| `DELETE /api/auth/accounts/{provider}`                              | Logout the compatible account, not cancel login                         |
| `POST /api/threads/configuration-preview`                           | Creation selections with per-axis provenance                            |
| `GET /api/threads/{thread_id}/configuration`                        | Sticky next choices versus actual captured composition                  |
| `GET /api/operations/{receipt_id}/configuration`                    | Exact receipt's captured selection, or null before capture              |
| `GET /api/threads/{thread_id}/context-usage`                        | Last reported request footprint, not accumulated usage                  |
| `GET /api/threads/{thread_id}/usage`                                | Durable observed root/descendant usage                                  |
| `GET /api/threads/{thread_id}/notes`                                | Bounded notes for the selected continuation                             |
| `GET /api/host/files`                                               | Bounded native directory page                                           |
| `GET /api/host/files/metadata`                                      | Native entry metadata, without following the final symlink              |
| `GET /api/host/files/text`                                          | Complete editable UTF-8 or explicit binary/large presentation           |
| `PUT /api/host/files/text`                                          | Create or save text with an observed revision                           |
| `GET /api/host/files/content`                                       | Bounded attachment-only native download                                 |
| `PUT /api/host/files/content`                                       | Bounded raw upload with create/replace preconditions                    |
| `POST /api/host/files/directories`                                  | Create one native directory                                             |
| `POST /api/host/files/move`                                         | Rename/move one observed entry to an absent destination                 |
| `POST /api/host/files/delete`                                       | Deliberate nonrecursive or bounded recursive deletion                   |
| `POST /api/threads/{thread_id}/host-file-captures`                  | Capture reviewed file bytes or lines as Thread input                    |
| `GET /api/host/git/repository`                                      | Discover the actual repository/worktree for a native path               |
| `GET /api/host/git/status`                                          | Read paged index/worktree status, optionally including ignored entries  |
| `GET /api/host/git/diff`                                            | Read one staged, unstaged, or untracked comparison                      |
| `POST /api/threads/{thread_id}/host-git-captures`                   | Capture reviewed patch bytes or lines as Thread input                   |
| `GET /api/host/terminals`                                           | List App-owned native terminals                                         |
| `POST /api/host/terminals`                                          | Create a native interactive PTY                                         |
| `GET /api/host/terminals/{terminal_id}`                             | Inspect session, output bounds and control                              |
| `DELETE /api/host/terminals/{terminal_id}`                          | Close shared session and remove its live identity                       |
| `GET /api/setup`                                                    | Current setup view                                                      |
| `POST /api/setup/preview`                                           | Preview a setup selection                                               |
| `POST /api/setup/apply`                                             | Apply a setup selection                                                 |
| `POST /api/environments/preflight`                                  | Preflight native/sandbox profile for a project path                     |
| `GET /api/auth/keys`                                                | Safe model-provider key metadata                                        |
| `PUT /api/auth/keys`                                                | Store provider credentials                                              |
| `DELETE /api/auth/keys/{reference}`                                 | Remove a provider key reference                                         |
| `POST /api/auth/logins`                                             | Start provider login                                                    |
| `GET /api/auth/logins/{session_id}`                                 | Read provider login progress                                            |
| `DELETE /api/auth/logins/{session_id}`                              | Cancel/remove the selected login session                                |
| `GET /api/configuration/sources`                                    | Accepted source metadata                                                |
| `GET /api/configuration/sources/{relative_path}`                    | Accepted source content where available                                 |
| `PUT /api/configuration/sources/{relative_path}`                    | Validate and publish source replacement                                 |
| `DELETE /api/configuration/sources/{relative_path}`                 | Validate and remove a non-root source                                   |
| `POST /api/configuration/validate`                                  | Validate source replacement without publication                         |
| `POST /api/threads/preview`                                         | Resolve new Thread selections without creating one                      |
| `PATCH /api/threads/{thread_id}/configuration`                      | Versioned exact configuration change                                    |
| `GET /api/threads/{thread_id}/project-defaults`                     | Preview the selected Project's configured defaults                      |
| `POST /api/threads/{thread_id}/project-defaults`                    | Apply reviewed defaults with version/digest checks                      |
| `GET /api/threads/{thread_id}/project-environments`                 | Preview complete Project environment replacement                        |
| `POST /api/threads/{thread_id}/project-environments`                | Apply only the four reviewed environment axes                           |
| `GET /api/projects`                                                 | Available Projects and creation defaults                                |
| `GET /api/selectors`                                                | Configuration selection options                                         |
| `GET /api/threads`                                                  | Query/page Threads                                                      |
| `GET /api/threads/activity`                                         | Navigation activity and pending summaries                               |
| `POST /api/threads/lookup`                                          | Bounded root summaries by identity, including saved completion markers  |
| `GET /api/threads/{thread_id}/tasks`                                | Selected Working State task projection                                  |
| `GET /api/threads/{thread_id}/children`                             | Parent-scoped child listing or exact query                              |
| `GET /api/threads/{thread_id}/children/wait`                        | Bounded child wait or poll                                              |
| `GET /api/threads/{thread_id}/children/{execution_id}/review`       | Bounded child inspection                                                |
| `POST /api/threads/{thread_id}/children/{execution_id}/steer`       | Enqueue child steering text                                             |
| `POST /api/threads/{thread_id}/children/{execution_id}/cancel`      | Request child cancellation                                              |
| `POST /api/threads`                                                 | Create using optional defaults/title                                    |
| `GET /api/threads/{thread_id}`                                      | Detail, continuation, available actions                                 |
| `GET /api/threads/{thread_id}/transcript`                           | Bounded retained transcript                                             |
| `PATCH /api/threads/{thread_id}/metadata`                           | Versioned title/archive change                                          |
| `POST /api/threads/{thread_id}/attachments`                         | Stage raw bytes with a filename                                         |
| `GET /api/threads/{thread_id}/attachments/{attachment_id}`          | Download a scoped attachment                                            |
| `GET /api/threads/{thread_id}/attachments/{attachment_id}/metadata` | Inspect its name, size, media type and immutable capture source         |
| `POST /api/threads/{thread_id}/submit`                              | Submit ordinary prompt and attachment IDs                               |
| `GET /api/threads/{thread_id}/decisions`                            | Exact pending-decision projection                                       |
| `POST /api/threads/{thread_id}/decisions`                           | Respond to the complete pending set                                     |
| `GET /api/operations/{receipt_id}`                                  | Query exact process-local operation                                     |
| `POST /api/operations/{receipt_id}/steer`                           | Add steering text                                                       |
| `POST /api/operations/{receipt_id}/cancel`                          | Request cancellation                                                    |
| `WS /api/realtime/connect`                                          | Multiplexed summary and focused observation channels                    |

`GET /api/openapi.json`, `/healthz`, `/readyz`, and static navigation/assets are additional non-schema-listed boundaries. Serving an application shell at a recognized browser route does not implement that screen. `features.host_files` is true only when the App was opened with native sharing enabled. `features.host_git` is true when sharing is enabled and a Git executable is discoverable. `features.host_terminal` reports native POSIX terminal availability. `features.shared_drafts` reports the in-memory shared composer protocol. `features.page_presence` and `features.output_comments` report transient page awareness and durable saved-output comments, independently of native sharing. These backend features do not imply browser panels exist.

## Skill catalogs and references

`POST /api/threads/skills-preview` accepts `NewThreadDefaults` and returns a `SkillCatalogView` without creating a Thread. `GET /api/threads/{thread_id}/skills` returns the idle Thread's next-Run catalog or the active Run's pinned catalog. `POST /api/threads/{thread_id}/skills` accepts an `EnvironmentSelectionPatch` to preview Run-only local-root choices without changing the Thread; an active Run still returns its pinned catalog. Items expose `item_id`, `name`, `description`, `source_id`, and `logical_path`; the response includes `catalog_id` and `context_kind`.

Submit and root steer accept an optional `skill_references` array (at most 512 entries), each with `catalog_id`, `item_id`, and `name`. The App validates these references before admission. Old-catalog references resolve by name; references claiming the applicable catalog must match its item identity. Missing, ambiguous, or duplicate references reject input. Keep `$name` in the ordinary prompt or ordered text parts; the references do not expand Skill bytes or grant permissions. Omitting this field preserves existing input behavior.

## Native Git Changes

Git Changes is read-only and uses the same computer-sharing gate as Files. Paths identify the server/container checkout, not the Agent's selected Environment. Disabling sharing returns `403 host_git_disabled`, including for direct App calls. A missing executable returns `503 host_git_unavailable` without disabling Files; repository errors are not reported as a clean worktree.

1. Discover with `GET /api/host/git/repository?path=<absolute-native-path>`. A file or directory resolves through Git to its worktree. The reply has `state: repository`, `not_repository`, or `bare`; a repository includes `root`, `git_dir`, `common_dir`, `head_oid` and `branch`. Detached HEAD has no branch; an unborn branch has no HEAD object ID. Linked worktrees, nested repositories and submodules keep their own identities.
2. Read `GET /api/host/git/status?path=...&include_ignored=false`. Entries have repository-relative `path`, optional `original_path`, separate `index_status`/`worktree_status`, `kind` and optional submodule flags. A file may have changes on both axes. Ignored directories may be summarized by Git. Pages default to 200 entries, allow at most 500 and reject scans above 10000. Further pages require `offset` and the preceding `expected_revision`; conflicts restart the listing. The status revision covers status records, not every file's bytes.
3. Read `GET /api/host/git/diff?repository_path=...&path=<relative-file>&comparison=unstaged`. `staged` compares observed HEAD (or the empty tree) to index; `unstaged` compares index to worktree; `untracked` explicitly compares a new file. Paths are literal, not patterns; directory/ignored selection belongs in Files. The reply supplies identity, index and diff revisions, and `text`, `binary` or `unchanged` presentation. Only text presentation supplies a UTF-8 patch; conflicts retain Git's combined/unmerged form. An optional `expected_revision` rejects a changed comparison.
4. Capture with `POST /api/threads/{thread_id}/host-git-captures`. The JSON body has `repository_path`, relative `path`, `comparison`, required `expected_revision`, and optionally both `start_line` and `end_line` as inclusive one-based **patch** lines. The server recomputes and checks the reviewed diff before staging it. The response uses the same `attachment`/`prompt_text` shape as file capture. Binary and unchanged previews are rejected; use Files when selecting binary file bytes.

Captured source metadata includes `kind: git_diff`, Host location, repository/Git directory, selected/original paths, comparison, HEAD, index/diff revisions and range. Send its attachment ID through ordinary `/submit`; the saved bytes and source remain stable after later edits or index/branch changes. Captures up to 64 KiB supply attributed inline text; larger patches remain retained attachments. Steering accepts the same attachment ID and preserves its captured bytes and provenance. Existing file provenance and ordinary attachments remain compatible.

Queries are fresh on demand, without a Git database or watcher. Refresh after file mutations, on returning to Changes, or explicitly to observe other people's and processes' edits. Git state is shared checkout state, not per-Run attribution. Each subprocess is limited to 15 seconds, 2 MiB stdout and 64 KiB stderr; overflow returns `413 host_git_too_large`, timeout returns `504 host_git_timeout`, and cancellation reaps the process. No partial patch is presented as complete. The API disables external diff/textconv helpers and optional index refresh writes. It never stages, commits, discards, switches branches or runs remote operations.

## Native Host Files

Start with `a13n-harness-ui webui`. Computer sharing is enabled by default and exposes the server OS account's file authority to admitted instance clients, independently of Agent Environment selection. Use `--no-share-computer` to disable it. In Docker it means the container and its mounts, not the browser machine. Authentication bypass does not override the sharing selection. Disabled sharing returns `403 host_files_disabled` from native operations, including direct App calls before filesystem access. Project roots from `/api/projects` are navigation starts, not a jail; Files also works outside Git or any Project.

Read `GET /api/host/files?path=<absolute-path>` for a directory, or `/api/host/files/metadata?path=...` for entry metadata. Directory pages default to 200 entries (maximum 500), reject scans over 10000 entries, and return `next_offset`. For subsequent pages, pass both `offset` and the previous `directory.revision`; a conflict requires starting a fresh listing. Metadata describes the final symlink itself; text reads and browsing return the resolved target and its revision.

Read `/api/host/files/text?path=...` before editing. `presentation: text` supplies complete NUL-free UTF-8 within 512 KiB; `binary` and `too_large` supply no editable text. Save with `PUT /api/host/files/text` and a JSON body containing `path`, `text`, and the observed `expected_revision`. Omitting the revision is **create only**, not last-write-wins. A stale save returns `409 host_files_conflict` without discarding the client's buffer. Saving a symlink requires explicitly selecting its resolved target. Atomic replacement of a hard-linked file changes only the selected directory entry; other aliases keep their original bytes. Raw upload uses `PUT /api/host/files/content?path=...&expected_revision=...` and octet-stream bytes under a 10 MiB limit; omit the revision only for a new file. Downloads use the corresponding GET, optionally pinning `expected_revision`, and always have attachment disposition and octet-stream content type. Larger files require another native workflow.

Directory creation accepts `{"path":"/absolute/new-directory"}` with an existing parent. Move accepts `path`, `destination`, and the source's `expected_revision`, atomically refuses an existing destination (including concurrent creation), rejects cross-device moves, and does not implement implicit copy/delete. A platform/filesystem without no-replace move support returns `host_files_unsupported` rather than risking overwrite. Delete accepts `path`, `expected_revision`, and optional `recursive: true`; otherwise directories must be empty. Recursive preflight bounds the operation to 10000 entries and 128 directory levels. Symlinks are removed as entries, not followed. A later `host_files_partial_failure` reports completed removals; refresh rather than retrying the original tree deletion blindly.

Native revisions are opaque OS metadata observations, not content hashes or historical versions. File reads check for changes while capturing bytes; saves recheck before atomic replacement. External processes can still race a final precondition check and native mutation. File operations are not an OS-wide transaction. Permission failures return `403 host_files_permission_denied`; missing paths return 404 and oversized operations return 413. Started filesystem work is not abandoned by a disconnected request, and a lost response can mean the mutation completed. No mutation is automatically replayed.

### Select reviewed file content for input

`POST /api/threads/{thread_id}/host-file-captures` accepts `path`, the reviewed target's `expected_revision`, and optionally both `start_line` and `end_line` (inclusive, one-based). The reply contains an ordinary `attachment` with its `source` (Host location, requested/resolved paths, revision, and range), and `prompt_text` when the captured text fits 64 KiB. The source file is read **at selection time**, not at Send.

Pass that `attachment.attachment_id` in the ordinary `/submit` body's `attachment_ids`. Small-text captures add their attributed content inline to model input; binary and larger-text captures remain retained attachments rather than pretending to be inline text. Existing attachment count, total-input limits, Thread scope, and scratch/retention rules apply. Normal uploaded text attachments without captured source provenance retain their existing behavior.

Root steering accepts ordered `parts` or `prompt` plus optional `attachment_ids`; the App prepares images, ordinary files, and captured context exactly as for ordinary submission. Selected content is never silently omitted. Child steering remains `prompt` only. Shared editing is provided by the separate [composer protocol](#shared-composer).

## Inspect configuration and working state

`POST /api/threads/configuration-preview` accepts the same creation defaults as `/api/threads/preview` and additionally returns per-axis `provenance`. The winning source is `explicit`, `project`, `agent`, `global`, or `builtin`; explicit null Project and empty lists retain their meaning. Existing `/preview` remains compatible. In `GET /api/threads/{thread_id}/configuration`, `next_run` contains sticky selections with `thread` provenance: those IDs do not preserve historical inheritance. Current default equality is not evidence that an existing Thread inherited that source.

The inspection includes current accepted generation, next Agent model/capability IDs, and a static Tool Proxy view based on the Thread's selected MCP/plugin IDs. This is configuration inspection, not an execution-readiness check. `/api/agents/{agent_id}/tool-proxy` separately shows **Agent-default** membership. `/api/catalog` lists discovered implementation keys, while `/api/selectors` lists configured selectable resources. Dormant grouping never activates a source, and neither view connects to MCP.

`captured` is an allowlisted projection of an actual published Run composition. `capture_source: active_operation` identifies its exact receipt/Run; null capture means that operation has not published a composition yet, not that the previous Run's capture applies. Without active work, `selected_continuation` reads the saved continuation's composition and includes that continuation ID. `/api/operations/{receipt_id}/configuration` inspects the exact receipt while this process retains it. Changes to resource content or sticky selections do not alter an earlier capture. Instructions, credentials, model/native configuration payloads, MCP transports and dependency import paths are omitted; `omitted_fields` identifies these categories. At most 100 immediate child selection summaries are returned with `omitted_children`; this is not an executable recipe or recursive child graph.

`/api/threads/{thread_id}/context-usage` reports the last retained root request footprint, not live token counting. `/usage` returns observed root, descendant and combined usage with existing unknown-cost/omission fields; it is not the context size. `/notes` reports bounded selected-continuation notes and accepts `expected_continuation_id` to reject a stale view. `/api/auth/accounts/{provider}` returns credential-free compatible Codex/Grok status. DELETE logs out that account through its existing store and returns whether an entry was removed; it does not cancel a pending `/api/auth/logins/{session_id}` session or cancel running execution.

## Configure Projects and Threads

Configuration source queries describe the **accepted generation**, which may differ from invalid or newly edited files on disk. `GET /api/configuration/sources` lists relative paths, resource IDs, digests, and editability; the path-specific GET includes source text when available. MCP source text is withheld (`content: null`, `content_available: false`) because it can contain literal credentials. Do not save that null as a replacement. Account credential stores are not configuration sources.

Create or replace an approved source with `PUT /api/configuration/sources/{relative_path}` and `{"content":"..."}`. `POST /api/configuration/validate?path=projects/work.yaml` accepts the same body for validation only; it publishes nothing. Both validate the resulting complete candidate, so replacement can repair a malformed source and deletion can remove an invalid unused source. Other invalid files still block publication. Root deletion, traversal, symlink sources, and arbitrary Host paths are not allowed.

Source saves use last-write-wins, **not** an expected source digest. Validation does not reserve the file. The publication response distinguishes the source digest written from the subsequent generation digest; another editor may write later. A failed/disconnected response is not proof that no file was written. Inspect current status and accepted sources before retrying.

Preview creation without allocating a Thread:

```bash
curl --fail-with-body "$HUI_URL/api/threads/preview" \
  -H "Authorization: Bearer $HUI_API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"project_id":null}'
```

This request body is `NewThreadDefaults` directly, whereas Thread creation nests it under `defaults`. The preview resolves the exact Agent, Environment, Plugin, Run Extension, and MCP selections from current configuration; creation resolves again rather than reserving that preview. Null Project suppresses the global Project default. Project defaults are shown by `/api/projects`; existing Thread selections are shown in Thread detail.

`PATCH /api/threads/{thread_id}/configuration` accepts `expected_version` and `patch`. Supported patch fields are `project_id`, `agent_id`, `default_model_id`, `local_roots`, `environment_profile_id`, `environment_bindings`, `default_environment`, `harness_plugin_ids`, `environment_run_extension_ids`, and `mcp_server_ids`. Omission preserves the saved value, `project_id: null` clears the Project, and an empty list selects none. Changing Agent does not implicitly replace other saved axes. These root-Thread commands do not modify child Threads or an already captured Run.

To replace only local roots, local profile, remote bindings, and default environment, use GET/POST `/api/threads/{thread_id}/project-environments`. Its preview includes the effective complete replacement, and apply uses the returned `expected_version` and `defaults_digest`. Project edits do not otherwise change saved environment selections. Paths are references, never filesystem copies.

To apply the selected Project's broader configured defaults:

1. GET `/api/threads/{thread_id}/project-defaults` and inspect `current`, `replacement`, and `patch`.
2. POST to the same path with the returned `expected_version` and `defaults_digest`.
3. On 409, refresh the preview rather than automatically accepting newer values.

Only Project-specified axes are applied, not all lower-priority creation defaults. An empty combination has no apply action. This operation uses Thread concurrency checks; it does not change source-file last-write-wins semantics.

## Metadata, decisions, and attachments

Metadata PATCH accepts `expected_version` plus `patch`; use the Thread's current `metadata_version`. Omitting a title preserves it; null clears it. A supplied `archived` must be boolean, and archiving requires no active root operation. Configuration version and continuation ID are different preconditions.

Decision responses carry `expected_continuation_id` and every selected request exactly once. Question, approval, and external-result kinds must match their pending contract. Do not replace a suspended continuation with an ordinary prompt or submit only the answers convenient to the current UI.

Attachment upload uses raw bytes with a `name` query parameter, not multipart form data. The contract advertises `application/octet-stream`; preserve the returned attachment ID and use it only within its Thread. Limits are 10 MiB each, eight per input, and 20 MiB combined. Downloads return bytes with attachment disposition, not JSON. Root steering accepts the same ordered parts or legacy prompt/attachment fields as submission. Child steering uses the text-only `SteerRequest` (`prompt`) and rejects attachment fields rather than ignoring them.

## Navigate and inspect child work

- `GET /api/threads/activity` returns navigation summaries, pending counts, current activity, and retained terminal outcomes. It accepts `project_id`, `query`, `include_archived`, `cursor`, and `limit`.
- `POST /api/threads/lookup` accepts `{"thread_ids":["thread-..."]}` with 1–100 IDs (1–80 characters each). It returns a `ThreadPage` of matching root summaries, including archived roots and unavailable Project references, without pagination or continuation hydration. Missing IDs and child Threads are omitted; duplicate IDs coalesce. This read does not change metadata, navigation order, or execution.
- Root summaries include optional `completion: {version, run_id, continuation_id, completed_at}` for the latest successfully saved root Run. It survives App restart; failures, cancellation, and subsequent checkpoints retain it. Existing databases start without historical markers. Transcript pages carry `completion_version` from the exact Thread snapshot used to load that transcript, or zero before any marked success. Clients implementing personal read state must acknowledge that rendered version, not a newer summary version; the server has no per-user acknowledgement endpoint.
- `GET /api/threads/{thread_id}/tasks` returns the selected task projection. Tasks and decisions accept `expected_continuation_id`; a mismatch returns a conflict rather than mixing snapshots.
- `GET /api/threads/{thread_id}/children` lists children under that exact parent. Supply `execution_id` for a specific child, or `cursor` and `limit` for a page.
- `GET /api/threads/{thread_id}/children/wait` uses the same scope and selectors plus `timeout_seconds` (0–60), waiting only through the existing child operator.
- `GET /api/threads/{thread_id}/children/{execution_id}/review` returns bounded child inspection.
- POST to the child's `/steer` with `{"prompt":"..."}`, or `/cancel`, controls only that execution under the specified parent. A control acknowledgment is not terminal completion.

Use the existing root operation GET for polling and exact-receipt steering/cancellation. These operations introduce no alternate execution coordinator or persistent work queue.

## Observe realtime channels and recover gaps

Connect to `/api/realtime/connect` over WebSocket and authenticate with the first JSON message `{"api_key":"<instance key>"}`. Keep credentials out of URLs. Subscribe with a unique channel ID:

```json
{"version":1,"kind":"subscribe","channel":"focused-root","stream":"focus","root_thread_id":"<thread ID>","after":null}
```

For summary hints, use `stream: "summary"` and omit `root_thread_id`. At most twelve channels share one connection. Unsubscribe with `{"version":1,"kind":"unsubscribe","channel":"focused-root"}`. Answer a version-1 `ping` with `{"version":1,"kind":"pong"}`. Server observation envelopes carry `version: 1`, the channel ID, and the observation `frame`. There is no SSE fallback; clients use this protocol.

Observation frames are JSON objects. A focused stream without `after` begins with `kind: "snapshot"`. If `snapshot.root_stream` is present, the snapshot cursor is null and `kind: "root_stream"` batches follow, each containing at most 16 indexed events from that exact Run's existing Stream Protocol observer. Apply these once to the Run's provisional display, then store the `resume_cursor` from `kind: "ready"`. If interrupted before ready, discard the incomplete bootstrap and open a fresh watch. Without root replay the initial snapshot already carries a cursor. Following `kind: "event"` frames carry later live events and their cursors.

The root prefix includes only events published at the snapshot cutover, not later observations. Observer indexes are not the live hub's global sequence. Fetch saved transcript independently, bound to the selected continuation, and replace provisional output when that continuation advances. `recent_events` is only incomplete diagnostic context: do not append it again beside history or root replay. Child inspection uses the existing compact closed-activity projection; reconnect does not expose unfinished child activity.

Store cursors only after applying their frames; reconnect using the opaque `after` field in a new subscription command. A valid cursor assumes the client retained its display. A newly loaded page needs a fresh bootstrap instead. This is not the Service Run stream's `Last-Event-ID` contract.

The summary channel begins with `kind: "open"` and emits `kind: "invalidation"`; reconcile affected resources instead of interpreting invalidation as a full resource. An open with `resumed: true` replays missed hints without requiring a full refresh; `resumed: false` requires initial reconciliation. Batch dirty Thread IDs through `POST /api/threads/activity/lookup` (up to 100 IDs) to update loaded navigation rows. Activity pagination returns the active collection on the first page only. A settled `root_operation` event may additionally include `notice: {receipt_id, status, brief}` with Host status `completed`, `failed`, or `suspended` and an actual plain-text preview of at most 320 characters. Notice delivery follows continuation selection and is best effort, not durable delivery. Deduplicate notices by event epoch and receipt ID. A fresh subscription or stream reset is not a reason to notify about historical completions. Focus and summary cursors are distinct and bound to scope/epoch. Sparse sequences are valid; do not demand contiguous global numbering.

A `kind: "reset"` frame requires refetch and a fresh subscription. The live buffer is bounded and process-local. `watch_thread` provides subscribe-before-query cutover, not transactional durable replay. Disconnect stops observation, not execution. Each channel resumes independently; a root Run switch replaces only its focused channel, not the socket or unrelated observations.

## Errors and versions

App errors use `{"error":{"code":"...","message":"..."}}`. Typical mappings are 400 for invalid App requests, 409 for conflicts/stale versions/preflight requirements, 413 for oversized bodies, 404 for unavailable resources/receipts, and 503 for App not ready/stopping. Query validation can return FastAPI's 422 validation response. Listener authentication/Origin/Host rejection uses 401/403/400 respectively.

Handle code and current state, not text matching. A timeout or disconnected client does not establish whether a mutation took effect. Check `/api/status` and schema compatibility before assuming source documentation matches a deployed listener.
