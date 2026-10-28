---
title: Harness UI HTTP API
sidebarTitle: HTTP API
description: Run a conversation over HTTP, handle decisions, and add configuration, file, or realtime integrations.
---

Use this API to drive a running Harness UI instance from your own client. Start with one conversation, then add the controls your client needs. The listener uses the same App as the TUI and [Python embedding](embedding.md); Service has a separate managed API under `/api/v1`.

| Build this                                        | Start here                                                                                              |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| A script that sends a prompt and reads the result | [First conversation](#create-a-thread-and-submit-input)                                                 |
| A client that handles approvals and attachments   | [Decisions and attachments](#metadata-decisions-and-attachments)                                        |
| A configuration editor                            | [Inspect](#inspect-configuration-and-working-state), then [save](#configure-projects-and-threads)       |
| A streaming conversation view                     | [History](#conversation-input-navigation), then [realtime](#observe-realtime-channels-and-recover-gaps) |
| A collaborative editor or native workbench        | [Shared composer](#shared-composer), [Files](#native-host-files), or [Terminal](#native-terminal)       |

## Authenticate and discover the contract

1. Complete [setup](setup.md) and confirm that the chosen Agent can answer a prompt.

2. Start the listener. For a conversation-only integration, keep native Host panels disabled:

   ```bash
   a13n-harness-ui webui --no-share-computer
   ```

3. Keep that process running. In another Bash terminal, set its printed origin and enter its instance key without putting the key in shell history:

   ```bash
   export HUI_URL=http://127.0.0.1:8000
   read -r -s -p 'Harness UI instance key: ' HUI_API_KEY
   printf '\n'
   export HUI_API_KEY
   ```

   Replace the example origin with the actual listener URL. Use the instance key, not a model-provider credential.

4. Check access and retrieve the running contract:

   ```bash
   curl --fail-with-body "$HUI_URL/api/status" \
     -H "Authorization: Bearer $HUI_API_KEY"
   curl --fail-with-body "$HUI_URL/api/openapi.json" \
     -H "Authorization: Bearer $HUI_API_KEY"
   ```

A successful status response includes `api_version: "1"`, build information, App status, access mode, and feature flags. Use the live OpenAPI document for exact fields and constraints. The [generated schema](/reference/harness-ui-openapi.json) describes this source version; Swagger and ReDoc pages are disabled.

Owner API requests use an `Authorization` header. The listener also validates `Host` and same-origin `Origin` headers. Keep keys out of query strings and logs. `--dangerous-skip-permissions` disables listener authentication, so it is unsuitable as an authentication solution. Instance-key clients share workbench authority; scoped guest access is described under [sharing](#subscription-accounts-and-sharing).

## Create a Thread and submit input

Save this as `conversation.sh`. It requires Bash, curl, jq, the two environment variables above, and usable configured defaults. The script creates one Thread, submits once, polls its receipt, and reads saved history. The prompt still runs with the selected Agent's actual permissions.

```bash title="conversation.sh"
#!/usr/bin/env bash
set -euo pipefail
: "${HUI_URL:?Set the listener origin}"
: "${HUI_API_KEY:?Set the instance key}"

api() {
  local path=$1
  shift
  curl --silent --show-error --fail-with-body \
    -H "Authorization: Bearer $HUI_API_KEY" \
    "$HUI_URL$path" "$@"
}

api /api/setup | jq .
api /api/selectors | jq .
thread=$(api /api/threads -H 'Content-Type: application/json' \
  --data '{"title":"API conversation"}')
thread_id=$(jq -er '.thread_id' <<<"$thread")
printf 'Thread: %s\n' "$thread_id"

receipt=$(api "/api/threads/$thread_id/submit" \
  -H 'Content-Type: application/json' \
  --data '{"parts":["Explain this project without changing files."]}')
receipt_id=$(jq -er '.receipt_id' <<<"$receipt")
printf 'Receipt: %s\n' "$receipt_id"

while true; do
  operation=$(api "/api/operations/$receipt_id")
  status=$(jq -er '.status' <<<"$operation")
  case "$status" in
    preparing|running) sleep 1 ;;
    completed|suspended|failed|cancelled) break ;;
    *) printf 'Unexpected status: %s\n' "$status" >&2; exit 1 ;;
  esac
done

jq '{status, outcome, failure}' <<<"$operation"
api "/api/threads/$thread_id/transcript" | jq .
if [[ "$status" == suspended ]]; then
  api "/api/threads/$thread_id/decisions" | jq .
fi
```

Run `bash conversation.sh`. A receipt acknowledges admission. `preparing` and `running` mean work is still active. At a terminal status, inspect these separate results:

| Field                  | What to check                                                           |
| ---------------------- | ----------------------------------------------------------------------- |
| `outcome.execution`    | Execution status, output, omitted-output flag, and failure              |
| `outcome.continuation` | Whether the next continuation was selected successfully                 |
| `outcome.environment`  | Environment publication and cleanup results                             |
| `failure`              | An operation-level failure, including failures before an outcome exists |

To continue, submit another `parts` body to the same Thread and poll the new receipt. There is no root-operation HTTP wait endpoint. Each Thread admits one active root operation; a second submit is rejected rather than queued.

Retain both IDs in your client. After an uncertain submit response, inspect `GET /api/threads/{thread_id}` and its root activity before taking another action. Submission has no durable idempotency guarantee. Receipts belong to the App process and can disappear after restart, while saved conversation state remains available. Stopping the polling script stops observation, not the server's Run.

### Change a running operation

Send `{"parts":["Focus on the configuration files."]}` to `POST /api/operations/{receipt_id}/steer`, or request cancellation with `POST /api/operations/{receipt_id}/cancel`. Poll the same operation afterward. An accepted control request is not a completed cancellation or rollback.

Root and child steering use global `input.steering_mode`: `immediate` by default, or `queued` for the next safe boundary. There is no per-message mode. Supported tool calls can continue across immediate steering. Inspect exact-Thread statuses through `GET /api/threads/{thread_id}/tool-executions?offset=0&limit=20` (`limit` 1–100). Cancel one through `POST /api/threads/{thread_id}/tool-executions/{execution_id}/cancel`. These process-local status records neither expose nor consume private results. See [tool execution controls](webui.md#tool-executions-and-immediate-steering).

## Metadata, decisions, and attachments

### Answer a suspended operation

1. Read `GET /api/threads/{thread_id}/decisions`. A non-null batch supplies `continuation_id`, `requests`, and any interaction deadline.
2. Collect answers for the displayed batch. Match each `request_id` and its `kind`; use the request's question choices or external-result contract.
3. POST a `DecisionResponseBatch` to the same route. Copy the current continuation ID into `expected_continuation_id`.
4. Poll the returned **new** receipt. On a stale-continuation conflict, reread the decisions before asking for or submitting another answer.

For a batch containing one approval, the body looks like this; replace both placeholders with the observed values and choose the approval deliberately:

```json
{
  "expected_continuation_id": "<current continuation ID>",
  "responses": [
    {"kind": "approval", "request_id": "<pending request ID>", "approved": true}
  ]
}
```

Question responses use `kind: "question"` with `answers` and/or free-text `response`. External results use `kind: "external"` with `result`, or `denied: true` and `denial_message`. IDs must be unique and belong to the selected pending batch. The API permits omissions: omitted approvals are denied, and omitted external requests, including questions, fail. An ordinary prompt also continues with these unanswered-request effects. Collect the intended answers before resuming.

### Upload an attachment

Stage raw bytes before submitting a reference. Use `name` as a query parameter, not multipart form data:

```bash
curl --fail-with-body "$HUI_URL/api/threads/$THREAD_ID/attachments?name=notes.txt" \
  -H "Authorization: Bearer $HUI_API_KEY" \
  -H 'Content-Type: application/octet-stream' \
  --data-binary @notes.txt
```

Set `THREAD_ID` to an existing Thread ID. Keep the returned `attachment_id` for the [ordered input body](#ordered-input-bodies). Limits are 10 MiB per file, eight occurrences per input, and 20 MiB combined. Repeating an attachment ID counts again. Metadata reads use `/attachments/{attachment_id}/metadata`; downloads use `/attachments/{attachment_id}` and return bytes with attachment disposition. IDs belong to the staging Thread.

### Rename or archive a Thread

PATCH `/api/threads/{thread_id}/metadata` with the current `metadata_version` as `expected_version` and a `patch`. An omitted title is unchanged; null clears it. `archived` must be boolean, and archiving requires an inactive root. Metadata version, configuration version, and continuation ID protect different operations.

## Run-only execution environment

Add `environment` to `/submit` when one Run needs different execution selections:

```json
{
  "parts": ["Review this project"],
  "environment": {
    "environment_profile_id": "environment-sandbox",
    "local_roots": ["/absolute/path/to/code"],
    "environment_bindings": [],
    "default_environment": "workspace"
  }
}
```

Choose profile IDs from `/api/selectors`: `environment-native` is Full Control and `environment-sandbox` is Sandbox. Omitted environment axes inherit saved Thread choices, empty collections clear selections, and null clears only `default_environment`. The patch leaves the saved configuration and its version unchanged. Environment preparation can fail after admission; inspect the receipt rather than assuming readiness or a Full Control fallback.

`/submit` also accepts Run-only `model_id`, `thinking`, `fast`, and `reasoning_mode`. Omitted/null controls inherit Model settings; `thinking: false` requests Off. Selectors provide supported values and disabled reasons. Invalid selections are rejected. Steering accepts neither Environment nor Model overrides. Deferred responses keep the suspended continuation's captured Environment; a later ordinary submit inherits saved defaults again.

## Conversation input navigation

Use `/api/threads/{thread_id}/inputs` for a lightweight directory of ordinary input turns. It excludes steering and hidden input. Each entry has an input identity, preview, input position, exclusive end position, and optional saved final-response position. Follow `next_cursor`; `limit` is 1–100.

Use `/api/threads/{thread_id}/transcript` for the messages. Supply `turn_id` on the initial request to open a selected turn. `next_cursor` moves earlier; `newer_cursor` moves later. Cursors and `expected_continuation_id` bind the observed history. After a mismatch, refresh instead of mixing message positions from different continuations.

Transcript pages include intersecting `turns` and `boundary_entries` needed to display input or final answer outside the ordinary page. Deduplicate by position and paginate missing intermediate entries. A recorded final position comes from saved execution, not the last assistant text or a live text-end event.

## Saved output

For a child, GET `/api/threads/{thread_id}/children/{execution_id}/saved-output` to obtain typed targets and bounded text windows. POST a target to `/api/threads/{thread_id}/saved-output`, with optional `offset` and `limit` query parameters, to read another window.

A root target identifies its producing Thread, continuation digest, and original message/part positions. Refetch after a stale-target error. These reads expose saved output; they do not retain earlier checkpoints.

## Inspect configuration and working state

Choose the read that answers your question:

| Question                              | Route and interpretation                                                                               |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| Which resources can I select?         | `GET /api/selectors`; `/api/catalog` instead lists discovered implementations                          |
| What would a new Thread use?          | `POST /api/threads/configuration-preview` with `NewThreadDefaults`; inspect per-axis `provenance`      |
| What will this Thread use next?       | `GET /api/threads/{thread_id}/configuration`; inspect `next_run`                                       |
| What did the admitted Run capture?    | The same response's `captured`, `capture_source`, and exact Run/receipt or continuation ID             |
| Which tool sources are grouped?       | Thread configuration shows selected sources; `/api/agents/{agent_id}/tool-search` shows Agent defaults |
| How large was the last model request? | `/api/threads/{thread_id}/context-usage`                                                               |
| How much usage was observed?          | `/api/threads/{thread_id}/usage`; root, descendants, and combined usage                                |
| What tasks or notes exist?            | `/api/threads/{thread_id}/work?include=notes` or `include=tasks`                                       |

Creation provenance is `explicit`, `project`, `agent`, `global`, or `builtin`. Existing saved selections report `thread`; equality with today's defaults does not reconstruct their original source. These reads inspect configuration, without connecting to MCP or proving provider readiness.

During active work, a null `captured` means that operation has not published its composition yet. Without active work, `selected_continuation` reads the saved composition. Subsequent configuration edits leave earlier captures unchanged. Captures omit instructions, credentials, transport/configuration payloads, and dependency imports; `omitted_fields` names those categories. Immediate child summaries are capped at 100 with an omitted count.

## Configure Projects and Threads

### Edit reusable resources

1. GET `/api/configuration/sources` and then `/api/configuration/sources/{relative_path}` to inspect accepted files. The accepted generation can differ from an invalid edit on disk. MCP source content is withheld as null because it can contain credentials; do not write that null back.
2. POST `{"content":"..."}` to `/api/configuration/validate?path=projects/work.yaml` to validate the resulting complete configuration.
3. PUT the same body to `/api/configuration/sources/projects/work.yaml` to publish it. Use DELETE for a removable non-root source.
4. Inspect the publication response and accepted generation. Validation reserves nothing; source saves are last-write-wins, not expected-digest updates.

Replacement can repair an invalid file, and deletion can remove an invalid unused file. Other invalid sources still block publication. Root deletion, traversal, symlink sources, and arbitrary Host paths are rejected. Account credential stores are separate. If delivery is uncertain, read current sources before retrying.

### Change saved Thread selections

Preview creation by POSTing `NewThreadDefaults` directly to `/api/threads/configuration-preview`; nest those defaults under `defaults` when creating a Thread. For example, `{"project_id":null}` suppresses the global Project default. Preview creates nothing and creation resolves the configuration again.

For an existing Thread, PATCH `/api/threads/{thread_id}/configuration` with `expected_version` and `patch`. Supported fields are `project_id`, `agent_id`, `default_model_id`, `local_roots`, `environment_profile_id`, `environment_bindings`, `default_environment`, `harness_plugin_ids`, `environment_run_extension_ids`, and `mcp_server_ids`. Omission preserves values; empty lists select none. Changing Agent leaves other saved axes intact. Active captures and child Threads are unchanged.

To adopt the selected Project's defaults:

1. GET `/api/threads/{thread_id}/project-defaults` and review `current`, `replacement`, and `patch`.
2. POST the returned `expected_version` and `defaults_digest` to that route.
3. Refresh the preview on 409 before accepting changed defaults.

Only Project-specified axes are applied. For just the four Environment axes, use GET/POST `/api/threads/{thread_id}/project-environments`; its preview describes a complete Environment replacement. Neither operation copies files.

## Coordinator

Create an owner with `POST /api/threads` and `{"defaults":{"project_id":"project-main"},"coordinator":true}`. Use an available Project ID. Creation saves the role and enables automatic follow-up; submit input separately. A failed submission leaves the created Thread in place.

Promote an existing ordinary root with `POST /api/threads/{thread_id}/coordinator`. The Thread must belong to an accepted Project, be unarchived and inactive, and have no pending decisions. Promotion preserves identity, history, title, and settings; it is idempotent for an eligible Coordinator. Workers and children cannot be promoted, and there is no demotion or worker-adoption API.

PATCH that route with `{"auto_followup":false}` to pause automatic lifecycle notifications, or true to enable them. This setting is last-write-wins and independent of Sidekick configuration. Coordinators and workers keep their Project permanently. Restore an archived Coordinator through the ordinary versioned metadata API.

Create a worker through `POST /api/threads` with `coordinator_thread_id` and the owner's available Project in `defaults`. The owner must be unarchived. Ownership is permanent and mutually exclusive with `coordinator:true`; human creation uses the supplied defaults, not Sidekick. Creation starts no Run. The first human submission schedules a best-effort owner notice when follow-up is enabled. A caller-supplied `thread_id` lets the client reconcile uncertain creation without creating another worker.

Read `role` (`ordinary`, `coordinator`, or `worker`), `coordinator_thread_id`, and `auto_followup` from Thread summaries. Filter workers with `coordinator_thread_id`, or exclude workers with `independent_only=true`. Cursors bind these filters. Unfiltered human search includes all roots, and a Project can contain multiple Coordinators. Legacy Project lead bindings migrate once while preserving IDs/history; disabled bindings become Coordinators with follow-up off.

## Navigate and inspect child work

| Need                                   | Request                                                                               |
| -------------------------------------- | ------------------------------------------------------------------------------------- |
| Navigation activity and pending counts | `GET /api/threads/activity`, with Project/query/archive/cursor/limit filters          |
| Summaries for known roots              | `POST /api/threads/lookup` with `{"thread_ids":["<thread ID>"]}`; 1–100 IDs           |
| Children of an exact parent            | `GET /api/threads/{thread_id}/children`, optionally `execution_id` or cursor/limit    |
| Steer a child                          | `POST /api/threads/{thread_id}/children/{execution_id}/steer` with `{"prompt":"..."}` |
| Cancel a child                         | `POST /api/threads/{thread_id}/children/{execution_id}/cancel`                        |

Lookup includes archived roots and unavailable Project references, omits missing/child IDs, and coalesces duplicates. It does not paginate or load continuation state. Child controls acknowledge the request; inspect the child afterward. Unlike root steering, child steering is text-only and rejects attachment fields.

Root summaries may contain `completion: {version, run_id, continuation_id, completed_at}` for the latest successfully saved root Run. This marker survives restart and remains through later failures or checkpoints. Transcript `completion_version` identifies the snapshot actually read. A client implementing personal read state should acknowledge that rendered version, not a newer summary; the server has no per-user read acknowledgement endpoint.

## MCP input and integration control

MCP human input belongs to an in-flight tool call, not the suspended-decision workflow above:

| Request                                                            | Action                                                |
| ------------------------------------------------------------------ | ----------------------------------------------------- |
| `GET /api/threads/{thread_id}/mcp/integrations`                    | Inspect retained generations without connecting       |
| `POST /api/threads/{thread_id}/mcp/integrations/{server_id}/close` | Close that owning Thread's generation; returns 204    |
| `GET /api/threads/{thread_id}/mcp/inputs`                          | Read this Thread's and descendant children's requests |
| `POST /api/threads/{thread_id}/mcp/inputs/{request_id}/response`   | Answer the exact live request without starting a Run  |

Inspect the mode and original schema. A form accepts `{"action":"accept","content":{"name":"Ada"}}`; URL confirmation uses `{"action":"accept"}`. Decline or cancel with the corresponding `action`. Continue observing the original operation after acceptance.

Identical answers reconcile; conflicting answers fail. After uncertain delivery, reread and retry only the exact answer, not the business tool call. Expired, retired, and unavailable requests cannot authorize another send. Requests are process-local and disappear on restart. See [MCP human input](mcp.md#human-input-from-mcp-servers).

## Skill catalogs and references

Preview new-Thread Skills with `POST /api/threads/skills-preview` and a `NewThreadDefaults` body. For an existing Thread, GET `/api/threads/{thread_id}/skills`, or POST an `EnvironmentSelectionPatch` there to preview Run-only roots. Idle Threads show their next-Run catalog; active Runs keep their pinned catalog. Preview changes no Thread.

Use each result's `catalog_id` and item `item_id`/`name` in the optional `skill_references` array on submit or root steer (maximum 512). Keep `$name` in the text. Applicable-catalog references must match item identity; older catalogs resolve by name. Missing, ambiguous, or duplicate references reject input. A reference selects guidance without expanding its bytes or granting permissions.

## Memory observation

GET `/api/threads?memory=true` to list Memory Threads; ordinary listings exclude them. `projectless=true` selects Global memory, and Project filters retain normal pagination. `/api/threads/lookup` accepts the same `memory` discriminator. Memory Threads expose `memory_scope` and support transcript, live, configuration, and usage reads. Execution or mutation returns `memory_thread_read_only`.

GET `/api/memory/files` to list Global files and `/api/memory/file?path=MEMORY.md` to read one. Add `project_id` for a configured Project's scope. These authenticated, read-only routes work independently of computer sharing and require Memory to be enabled. They expose relative scope files and do not trigger organization.

## Subscription accounts and sharing

For account setup, start with [Models and authentication](models-and-authentication.md). These API groups have different purposes:

| Purpose                            | Routes                                                                                                                    |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Inspect/select provider accounts   | `GET /api/auth/accounts/{provider}`, GET `/sources`, PUT `/selection` with `{source, account_id}`                         |
| Discover account-specific models   | `POST /api/auth/accounts/{provider}/models`                                                                               |
| Manage provider keys               | GET/PUT `/api/auth/keys`, DELETE `/api/auth/keys/{reference}`                                                             |
| Complete a login flow              | POST `/api/auth/logins`, GET/DELETE `/api/auth/logins/{session_id}`                                                       |
| Manage native account identities   | GET `/api/subscriptions`, POST `/api/subscriptions/accounts`, GET/PATCH/DELETE `/api/subscriptions/accounts/{account_id}` |
| Refresh/logout/redeem a reset      | POST account `/refresh`, `/logout`, or `/reset`; GET `/reset-vouchers`                                                    |
| Inspect selected Copilot allowance | `GET /api/subscriptions/copilot/usage`                                                                                    |

Provider inspection supports `codex`, `grok`, `copilot`, and `chatgpt`. Source selection supports Copilot accounts and ChatGPT native registrations; it accepts no client file path. DELETE `/api/auth/accounts/{provider}` logs out local credentials, separately from cancelling a login session. Reset redemption is an explicit action, separate from usage refresh.

Manage share grants with instance credentials: GET `/api/shares` by `scope`/`target_id`, POST to create, PATCH `/api/shares/{share_id}` to change permission, and DELETE to revoke. Creation returns the key once; inspect existing grants after an uncertain response. Guests use the scoped shared surface and cannot manage links or instance settings. See [WebUI](webui.md) for permissions and expiry.

## Device connections and working environments

Create reusable Device connections through configuration sources, for example PUT `/api/configuration/sources/devices/build.yaml` with the [Device YAML](environments-and-projects.md#add-device-bindings). Device credentials are separate from the listener key.

| Request                                    | Result                                                             |
| ------------------------------------------ | ------------------------------------------------------------------ |
| `GET /api/devices`                         | Configured IDs, names, and carrier kinds; no connection attempt    |
| `GET /api/devices/{device_id}`             | Availability, path style, working directory, and discovery support |
| `GET /api/devices/{device_id}/directories` | One directory page without an Envd Session                         |
| `WS /api/devices/{device_id}/connect`      | Native daemon reverse attachment                                   |

Directory queries accept `path`, `offset` (0–1,000,000), and `limit` (1–200, default 100). Omit `path` for the Device's default working directory; follow `next_offset`. Paths belong to the Device. Discovery can be disabled on an online Device, and one unreadable directory does not establish that it is offline. These reads create neither Threads nor Envd Sessions and do not require computer sharing.

The reverse daemon connection negotiates `eip.v1` with Device bearer authentication in the upgrade header. It does not use browser first-frame authentication or URL credentials.

Set bindings in Thread creation `defaults` or a configuration patch:

```json
{
  "project_id": null,
  "environment_bindings": [
    {"device_id": "device-build", "alias": "build", "working_directory": "/work/repository"}
  ],
  "default_environment": "build"
}
```

A known absolute directory can be saved while offline. Each Run captures selections and prepares fresh execution-owned Sessions. `default_environment` routes relative paths and omitted cwd; it does not confine access. Native Files, Changes, and Terminal always operate on the listener Host.

### Forget offline configuration and repair selections

1. Remove references from current Project/global defaults through source updates.

2. DELETE the Device source or custom Environment profile. This forgets local configuration without contacting the Device or deleting remote files. Built-in profiles are not removable files.

3. Inspect existing Thread configuration and explicitly repair its next selections with the current version:

   ```json
   {
     "expected_version": 1,
     "patch": {"environment_bindings": [], "default_environment": "thread-files"}
   }
   ```

For selected local roots, `workspace` is another valid default. Removing the selected default without a replacement fails. Deletion never cascade-edits Thread selections, active captures, or saved history.

## Native Host Files

Enable computer sharing to use these endpoints; `webui` enables it by default, while the tutorial above disables it. Files use the server OS account's authority, including container mounts in Docker. Project roots are navigation starts, not confinement boundaries. Disabled sharing returns `403 host_files_disabled` before file access.

### Browse and edit a file

1. GET `/api/host/files?path=<absolute-path>` for a directory. Pages default to 200 entries, allow 500, and reject scans over 10,000. Follow `next_offset` with the previous `directory.revision`; restart the listing after conflict.
2. GET `/api/host/files/metadata?path=...` for the entry itself, or `/api/host/files/info?path=...` for resolved regular-file metadata and a filename-derived MIME hint. The latter follows symlinks and accepts `expected_revision`.
3. GET `/api/host/files/text?path=...`. `presentation:text` supplies complete NUL-free UTF-8 up to 512 KiB; `binary` and `too_large` are not editable text.
4. PUT `/api/host/files/text` with `path`, edited `text`, and the observed `expected_revision`. Omitting the revision means create-only. On `409 host_files_conflict`, retain the edit and reload for comparison.

Select a symlink's resolved target explicitly before saving. Atomic replacement of a hard-linked file changes only the selected entry. Raw uploads use PUT `/api/host/files/content?path=...&expected_revision=...`, octet-stream bytes, and a 10 MiB limit; omit the revision only for a new file.

### Download or preview bytes

POST `/api/host/files/transfers` with the reviewed file revision:

```json
{"path":"/absolute/clip.mp4","expected_revision":"<reviewed revision>","disposition":"inline"}
```

Use the returned `url` directly in the browser. It grants GET/HEAD `/api/host/files/transfer` for that revision and disposition, expires after 30 minutes, and contains no instance key. Listener restart invalidates it. Default `attachment` forces an octet-stream download. `inline` admits inferred image (except SVG), audio, and video types; other types become downloads. Browser decoding failure still permits downloading the original.

Transfers stream in chunks up to 256 KiB with no whole-file size limit. Single byte ranges return 206; unsatisfiable ranges return 416. HEAD has no body. Unsupported or multipart ranges are ignored, and an If-Range mismatch returns the full representation. Files with unreliable sizes stream to EOF without Content-Length or ranges. A changed revision yields 409 before headers or terminates an active transfer. Refresh metadata before requesting another link. The separate image-preview decode limit is 10 MiB.

### Create, move, or delete entries

| Operation                          | Body and behavior                                                                                            |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| POST `/api/host/files/directories` | `path`; parent must exist                                                                                    |
| POST `/api/host/files/move`        | `path`, `destination`, `expected_revision`; destination must be absent; no cross-device copy/delete fallback |
| POST `/api/host/files/delete`      | `path`, `expected_revision`, optional `recursive:true`; otherwise directories must be empty                  |

Recursive deletion is bounded to 10,000 entries and 128 levels. Symlinks are deleted as entries. Unsupported no-replace moves return `host_files_unsupported`; partial deletion reports `host_files_partial_failure` and completed removals. Refresh before another destructive action.

Revisions are opaque OS metadata observations, not content hashes or historical versions. Reads and writes check revisions, but external processes can race native mutations. An interrupted request can have completed its filesystem work. Read current state before retrying; mutations are not replayed automatically.

### Select reviewed file content for input

POST `/api/threads/{thread_id}/host-file-captures` with `path`, reviewed `expected_revision`, and optionally both one-based inclusive `start_line`/`end_line`. It reads the file at selection time and returns `attachment` with immutable source metadata, plus `prompt_text` for captured text up to 64 KiB.

Submit the attachment ID in `parts`. Small captured text is attributed inline; binary or larger captures remain readable Thread attachments. Later Host edits do not change those bytes. Root steering uses the same preparation; child steering remains text-only.

## Native Git Changes

Use these read-only routes to select a reviewed diff as input. They operate on the listener checkout and require computer sharing. Missing Git returns `503 host_git_unavailable`; disabled sharing returns `403 host_git_disabled`.

1. GET `/api/host/git/repository?path=<absolute-native-path>`. Inspect `state` (`repository`, `not_repository`, or `bare`) and repository identity: `root`, `git_dir`, `common_dir`, `head_oid`, and `branch`. Detached/unborn repositories can omit branch/HEAD respectively.
2. GET `/api/host/git/status?path=...&include_ignored=false`. Read separate index/worktree status and repository-relative paths. Pagination defaults to 200, allows 500, and rejects scans above 10,000. Later pages use `offset` and `expected_revision`; that revision covers status records, not every file's bytes.
3. GET `/api/host/git/diff?repository_path=...&path=...&comparison=unstaged`. Use `staged` for HEAD-to-index, `unstaged` for index-to-worktree, or `untracked` for a new file. Paths are literal. Inspect `text`, `binary`, or `unchanged` presentation and the diff revision.
4. POST `/api/threads/{thread_id}/host-git-captures` with `repository_path`, relative `path`, `comparison`, `expected_revision`, and optionally inclusive one-based **patch** line bounds. The server recomputes the reviewed diff before staging an attachment. Binary/unchanged captures are rejected; use Files for binary bytes.

Send the returned attachment ID through submit or root steer. Saved provenance retains repository, paths, comparison, HEAD/index/diff revisions, and selected range. Captures up to 64 KiB become attributed inline text; larger patches remain attachments.

Refresh on demand after edits or when returning to Changes. Git state is shared checkout state, not per-Run attribution. Subprocess limits are 15 seconds, 2 MiB stdout, and 64 KiB stderr; overflow/timeout returns 413/504. The API returns no partial patch as complete, disables external diff/textconv helpers, and performs no staging, commit, discard, branch, or remote operation.

## Native terminal

Check `features.host_terminal` before creating a PTY. Linux/macOS support native interactive terminals when a shell and computer sharing are available. Windows reports `host_terminal_unavailable`; disabled sharing reports `host_terminal_disabled`.

1. POST `/api/host/terminals` with `{"cwd":"/work","rows":24,"columns":80}` and optionally an accepted `project_id`. Keep the returned `terminal_id`. The initial cwd belongs to the Host and does not follow navigation.

2. Connect to `/api/host/terminals/{terminal_id}/connect?cursor=<last-end>` over same-origin WebSocket. Within ten seconds, send `{"api_key":"<instance key>"}` as the first text frame, or `{}` for a bypass listener. Invalid credentials close with 4401 before resource lookup.

3. Read `TerminalFrame` for your connection-local participant, terminal view, and base64 bytes. Preserve a streaming UTF-8 decoder and save `end` as the reconnect cursor. At most 1 MiB is retained; `gap:true` means bytes are missing or the cursor was ahead.

4. Claim control using the latest `control_epoch`, then use the new epoch for input or resizing:

   ```json
   {"kind":"control","control_epoch":0}
   ```

   After a successful claim returns epoch 1:

   ```json
   {"kind":"input","control_epoch":1,"text":"pwd\n"}
   {"kind":"resize","control_epoch":1,"rows":40,"columns":120}
   ```

5. Release with `{"kind":"control","control_epoch":1,"release":true}`, or DELETE `/api/host/terminals/{terminal_id}` to close the shared session.

Each connection starts as a viewer. A claim/takeover increments the epoch; stale commands return `host_terminal_control_conflict`. Only the controller can release. Input supports control characters, is limited to 16,384 characters per frame, and can partially deliver before backpressure fails. Never automatically retry keystrokes. Dimensions are 1–1,000.

Disconnect removes a participant, not the terminal. Exited terminals remain inspectable and count toward the 32-session limit until deleted. DELETE terminates the native session's jobs; deliberately daemonized independent OS sessions have separate lifetimes. App shutdown closes all terminals; PTYs/output do not survive restart. `x-interactive` in OpenAPI describes the wire schemas.

## Page presence

Connect to `/api/presence/connect` with the same first-frame authentication as terminals. `features.page_presence` advertises support; GET `/api/presence` reads the current directory without joining. Each tab has its own participant ID, even with the same display name.

Report the current focused pane:

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

Targets identify a workbench page, conversation, Project, resource, native file, Git comparison, or terminal. Use canonical paths returned by Files/Git. The containing root is separate from the focused target. Unavailable targets keep their identity and reason; presence neither opens a replacement nor reads file contents or starts a terminal.

Report changes immediately, set `foreground:false` when hidden/unfocused, and resend at least every 20 seconds. Sixty seconds without a report closes with 4408. Limits are 32 participants and 16 KiB per report. Invalid input returns `presence_invalid` without replacing accepted state.

`PresenceFrame` includes the directory and `same_page_participant_ids`; matching uses the target, not scroll/layout. HTTP accepts a participant ID for the same grouping. Resource availability refreshes every 15 seconds and on membership changes. Disconnect/restart clears membership. Presence is transport awareness, not verified identity or synchronized navigation, and remains independent of drafts and execution.

## Shared composer

Implement this protocol only if clients need to edit the same unsent input. Ordinary HTTP scripts can submit `parts` directly.

GET `/api/drafts` lists nonempty drafts as `{thread_id,draft_id,unsent_since}` without text or editor membership. Selected pending/failed attachments count as nonempty; whitespace and dormant entries do not. Refetch after `draft` summary hints or reset, then look up Thread metadata separately. The index and documents are in memory and disappear on App restart.

Connect to `/api/threads/{thread_id}/draft/connect` using first-frame authentication. Root Threads support one shared document per App, independently of computer sharing. `DraftFrame` contains `draft_id`, participant ID, a base64 Yjs v1 full-state update, and presence. Use these root types:

| Root                            | Meaning                                                                         |
| ------------------------------- | ------------------------------------------------------------------------------- |
| `text` (`Y.Text`)               | Plain text containing any inline attachment tokens                              |
| `attachments` (`Y.Map<string>`) | `inline-<UUID>` keys mapped to same-Thread attachment IDs or `pending`/`failed` |

Inline text tokens are `U+FFFC + key + U+FFFC`. Only live occurrences select input; dormant map entries support undo. Missing or unfinished entries block submission. Expand occurrences in text order before sending, so editor tokens never reach execution. Inspect an attachment through its metadata endpoint without downloading bytes.

Apply server updates to a Yjs-compatible replica. Send `{"kind":"sync","draft_id":"...","update_base64":"..."}` with the **complete** replica update, including dependencies and deletions. The server validates the merged document atomically. `draft_invalid` preserves server state; retain rejected local content for correction. Limits are 512 KiB encoded CRDT state, 256 Ki text characters, eight selected attachments, and 20 MiB attachment bytes. CRDT history counts toward the state limit.

Editor presence uses `{"kind":"presence","draft_id":"...","presence":{"name":"Alice","color":"#112233"}}`; optional `anchor`/`head` are base64 Y relative positions. Names/colors are unverified. Merge offline edits on reconnect only when `draft_id` matches. After identity loss, offer local recovery explicitly.

Send through ordinary `/submit`, not a separate draft endpoint:

1. Synchronize edits and observe their accepted CRDT state.
2. Clone the replica and expand its text/attachment selections into ordered `parts`.
3. After positive submission acknowledgement, delete only text visible in that clone, then merge its complete deletion update into the live replica. Keep inline registry entries for concurrent token occurrences.
4. Preserve the draft on rejection or uncertain delivery. Editing-state replay is safe to reconcile; execution resubmission is a separate action.

Disconnect removes editor presence while retaining the in-memory document and active Run. Closing the App drops drafts; saved conversation history keeps its own lifetime.

### Ordered input bodies

Root submit and root steer take strings and attachment references in authored order:

```json
{
  "parts": [
    "Compare ",
    {"attachment_id": "attachment-first"},
    " with ",
    {"attachment_id": "attachment-second"}
  ]
}
```

Stage bytes first. References are Thread attachment IDs, not Host paths or editor tokens. Root input accepts `parts`, not `prompt`/`attachment_ids`. Maximums are 1,024 parts and 256 Ki authored text characters, plus attachment limits. Empty input is rejected; attachment-only input is valid.

Images become native image input; ordinary files remain readable references. File/diff captures add attributed inline text when eligible. Display metadata includes `source_id` and `harness_ui.composer.index`; an attachment can expand into several pieces sharing its authored index. Group by source/index rather than attachment ID alone.

## Observe realtime channels and recover gaps

Connect to `/api/realtime/connect` over WebSocket, then send `{"api_key":"<instance key>"}`. Subscribe using a unique channel ID:

```json
{"version":1,"kind":"subscribe","channel":"focused-root","stream":"focus","root_thread_id":"<thread ID>","after":null}
```

Use `stream:"summary"` without a root ID for invalidation hints. Each root and the summary can have one channel. Active roots have reserved capacity; other channels share twelve idle slots. Envelopes contain `version:1`, channel, and frame. Unsubscribe with `{"version":1,"kind":"unsubscribe","channel":"focused-root"}` and answer ping with `{"version":1,"kind":"pong"}`.

Implement focused-stream startup in order:

1. Apply `snapshot`. If `snapshot.root_stream` exists, its cursor is null and indexed `root_stream` batches follow (up to 16 events each).
2. Apply those events once to the exact Run's provisional display. After `ready`, retain its `resume_cursor`. If disconnected before ready, discard the incomplete bootstrap and start fresh.
3. Apply subsequent `event` frames before saving their cursors. Without root replay, the initial snapshot already supplies a cursor.
4. Fetch saved transcript separately and replace provisional output when the selected continuation advances. Do not append diagnostic `recent_events` again.

Reconnect with the opaque cursor in `after` only when the client kept its display. A fresh page needs a fresh bootstrap. Observer indexes and hub sequences are distinct, and this is not Service's `Last-Event-ID` protocol. Child reconnect views show closed activity, not unfinished child output.

Summary starts with `open`, then `invalidation`. `resumed:true` replays missed hints; false requires initial reconciliation. Refresh affected resources, batching up to 100 IDs through POST `/api/threads/activity/lookup`. Only the first activity page includes active work. Settled root notices may include receipt/status and a 320-character brief; deduplicate by epoch/receipt and avoid notifying historical completions after reset.

A `reset` requires refetch and a fresh subscription. Focus and summary cursors bind their own scopes/epochs; sparse sequences are valid. Buffers are bounded and process-local, with no durable replay or SSE fallback. Disconnect stops observation, not execution. Recover channels independently.

## Current route map

Use the live OpenAPI document for the complete endpoint inventory, including Browser, Live, and MCP Apps. These remaining entry points are useful during integration:

| Request                                          | Use                                             |
| ------------------------------------------------ | ----------------------------------------------- |
| `GET /api/setup`                                 | Inspect setup readiness                         |
| `POST /api/setup/preview` and `/api/setup/apply` | Preview and apply setup selections              |
| `POST /api/environments/preflight`               | Test Full Control or Sandbox for a Project path |
| `GET /api/projects`                              | Read available Projects and creation defaults   |
| `GET /api/threads`                               | Query and page roots                            |
| `GET /api/host/terminals` and `/{terminal_id}`   | List terminals or inspect one                   |
| `GET /healthz` and `/readyz`                     | Check listener liveness and App readiness       |

Feature flags describe availability: `host_files` requires sharing; `host_git` additionally requires Git; `host_terminal` requires a supported native terminal. `shared_drafts` and `page_presence` describe in-memory collaboration independently of sharing. Health, readiness, static assets, and the OpenAPI download have their own non-schema-listed boundaries.

## Errors and versions

Read the code in `{"error":{"code":"...","message":"..."}}`, not message text:

| Status                | Usual action                                                |
| --------------------- | ----------------------------------------------------------- |
| 400                   | Correct invalid input or rejected Host address              |
| 401                   | Authenticate again                                          |
| 403                   | Check Origin or the operation's access/feature boundary     |
| 404                   | Refresh the missing resource or process-local receipt       |
| 409                   | Refetch version, continuation, revision, or readiness state |
| 413                   | Reduce the request or operation size                        |
| 422 `request_invalid` | Correct query parameters                                    |
| 503                   | Check App/runtime readiness or shutdown state               |

A timeout or disconnected response leaves mutation outcome uncertain. Inspect current state before repeating it. Check `/api/status` and the running schema when supporting multiple Harness UI versions.
