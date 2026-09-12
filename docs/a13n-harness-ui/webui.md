# Browser server

Harness UI's HTTP server shares the local `HarnessUiApp` with the terminal product. Its English-only browser workbench provides collaborative conversations, execution controls, setup, provider accounts, configuration resources and Project readiness, **not** Service Console business models. Browser capabilities and backend API availability remain distinct.

## Start the server

The bundled workbench accepts the instance API key, displays the installed Python package version, and provides light/dark themes, guided setup, provider-account/key management, resource editing and Project readiness. Conversations include a shared prompt editor, saved history, live output, pending decisions and execution/configuration inspection. Saved-output comments support exact selection, original-source inspection and explicit feedback references. Host Files, Git and terminal browser panels are subsequent workbench blocks. The HTTP API is independent: native Host Files and read-only Git Changes are enabled by default (Git requires an installed executable), with real native terminal sessions on POSIX Hosts and process-local shared CRDT drafts through authenticated WebSockets.

```bash
a13n-harness-ui webui                       # 127.0.0.1:8765, generated per-process API key
a13n-harness-ui webui --host 127.0.0.1 --port 9000
a13n-harness-ui webui --no-share-computer   # Opt out of native computer sharing
```

WebUI enables native file browsing, editing, transfer, creation, move, deletion, and captured Thread input through the [Files API](http-api.md#native-host-files) by default; `--share-computer` explicitly selects that default. The [Git Changes API](http-api.md#native-git-changes) adds repository discovery, status, selected diffs and reviewed Thread input under the same sharing gate. The [Terminal API](http-api.md#native-terminal) adds App-owned interactive POSIX PTYs under the same gate. The [shared draft protocol](http-api.md#shared-composer), [page presence](http-api.md#page-presence), and [saved output comments](http-api.md#saved-output-comments) are independent of computer sharing. Comments persist separately from model history; live presence and drafts do not survive App restart. Native Files/Git/terminal browser panels remain unavailable. Paths refer to the server account or container mounts, regardless of the Agent's Environment. Project roots are navigation starts, not filesystem confinement. Use `--no-share-computer` to keep native operations unavailable even when authentication is bypassed. Embedded Apps and the bare terminal CLI do not enable native sharing implicitly.

## Configure the workbench

Use **Setup & readiness** for first-use configuration: choose a model connection and execution environment, add optional Project roots and additional Agent instructions, check readiness, then preview and publish generated files. Provider login and model credentials are managed under **Provider accounts**, separately from the instance access key. Device login is preferred for remote servers where a browser callback cannot reach the server's loopback listener.

**Resources** supports source creation, validation, publication and deletion. Common fields and the advanced YAML editor share one local draft. Edits survive navigation and access-key replacement in the current tab, but not a reload. Publication is a complete-file, last-write-wins operation; an observed external change never silently overwrites a dirty draft. Validation does not publish, and active Runs keep their captured configuration. MCP source content is not readable through this API: replacing it explicitly replaces every resource and unseen field in that file.

**Projects** displays server directories and previews accepted defaults with per-axis provenance. Default, None and Custom list selections remain distinct. Preview does not include unsaved source changes or execute a model. The online indicator displays per-tab presence and lets each collaborator set a display profile; this is not provider login or an authenticated identity.

## Work in a conversation

The sidebar groups root conversations by Project and supports search, Project scope and archived history. **New conversation** previews the effective Agent and Environment before creating an empty Thread; it does not call a model. A direct Thread link takes precedence over restoring your last accessible conversation. Rename, Share and Details are secondary header actions. Archived conversations keep their history and can be restored.

The shared CodeMirror editor shows collaborator cursors and synchronization status. Enter adds a line; Ctrl+Enter or Cmd+Enter sends. **Send** is enabled only after this browser's pending edits have synchronized. Uploads and immutable captured context are Thread-scoped, with metadata and original-byte download available to collaborators. A positive receipt clears only the submitted snapshot; edits outside that snapshot remain. An uncertain acknowledgement preserves input and requires inspection before an explicitly new submission; nothing retries execution automatically.

While a Run is active, **Next message** remains editable without becoming a queue. **Send as instruction** targets the current operation, rather than creating a new turn. Ordinary uploads and unsupported captured content cannot be steered; the draft stays intact. **Stop** addresses the exact displayed receipt. Closing a page stops observation, not execution. Questions, approvals (including allowed argument overrides) and external-result requests have complete-set response controls; stale or competing decisions are refreshed rather than represented as a second success.

Saved transcript pages remain visible while replacement history loads or fails. Live output is provisional until replacement saved history arrives; stream completion alone is not proof of a saved continuation. Reasoning, tool activity, media, context operations and diagnostics remain distinguishable. Original assistant source and its saved identity remain available without offering comments on provisional output.

**Details** separates root operation, child executions, tasks/notes/usage, captured configuration and next-Run selections. Model execution, continuation publication and Environment cleanup have separate outcomes. Child review/control uses the exact parent execution. Applying Project defaults requires a before/after preview and the reviewed Thread version/digest. Competing configuration changes retain local selection edits and require another review, never silently replace the edit base.

Draft collaboration lives only in this App instance. In-app navigation retains the browser's editor state, and reconnecting to the same instance resynchronizes it. After server restart, explicitly rejoin the replacement draft and choose whether to restore this browser's text. Reload/browser-process-loss recovery is not promised. Display profiles are not authenticated identities; undo is local to the editor, and accepted Send establishes a new undo boundary.

## Discuss saved output

Saved assistant blocks offer **Comment**. Select source-identical rendered text for **Comment on selection**; when Markdown decoding or formatting makes that mapping ambiguous, select the exact text under **Original text** or comment on the whole block. Unsaved output is not commentable. Child execution details expose their independently saved output and the same comment actions.

The header's **Comments** action opens the Thread's saved discussion, including older output, with exact-output filtering and pagination. **View original output** reads the retained source without switching the conversation's continuation. Author labels are self-declared. Private comment drafts survive in-app navigation and access replacement in this tab, not reload. A lost publication acknowledgement freezes its exact identity/content for **Reconcile publication**, rather than creating a second comment. Publishing and opening comments never invoke a model.

**Add feedback to prompt** captures the complete selected comment (including its quote and attribution) and the full original assistant block. There is no reply tree: each comment is an independent complete publication. The shared composer shows a comment-reference card; open it to inspect the actual captured text before ordinary **Send** or **Send as instruction**. Model input carries that text with presentation metadata, not a tool instruction to look up the comment. Captures over 64 KiB of UTF-8 or unavailable original sources fail without truncation or changing the draft. Later Runs and new comments cannot replace a captured snapshot.

## Authentication and key retention

Startup stdout prints the ordinary URL and, only for a generated key, the key and a convenience fragment URL. The browser consumes and removes the key fragment, sends `Authorization: Bearer <key>` on API requests, and retains successfully used keys in same-origin localStorage. Use **Forget API key** to remove that retention. Static assets contain no key and need no authentication.

Key precedence is `--apikey`, then `A13N_HARNESS_UI_API_KEY`, then a fresh process key. Supplied keys are not echoed; command arguments may still be visible to the shell and operating system. Explicitly empty keys and conflicting repeated key values are rejected. `--dangerous-skip-permissions` disables Web authentication only, not Agent permissions or computer-sharing gates; combining it with a CLI or environment key is an error. `--api-key` and `--dangerously-bypass-permission` remain compatibility aliases.

## Listener and application lifetime

Non-loopback listening grants shared instance authority on a trusted network, not tenant isolation; use external TLS when needed. The server owns the App lifetime even without browsers; Ctrl+C or SIGTERM closes it. Unauthenticated `/healthz` and `/readyz` report bounded liveness and App readiness. A fresh instance can be ready for setup before any model is configured.

TUI and WebUI processes can share one local database across compatible package upgrades. A newer migration revision alone does not reject an older compatible reader or gate an active Run's save. An older App leaves unknown newer migration history unchanged and checks that its required tables and columns remain available. Missing storage and real continuation/version conflicts still fail explicitly; schema compatibility does not share live execution ownership. Already released binaries keep their own startup checks, and incompatible payload formats cannot be made readable merely by relaxing revision validation.

## Container and installed assets

The GHCR image is `ghcr.io/converge-ai-labs/a13n-harness-ui`: `dev` follows main, releases use `X.Y.Z`, and RCs use `X.Y.Z-rc.N` without advancing `latest`. Python and the page display RC metadata as `X.Y.ZrcN`. Development builds display source version `0.0.0` with a separate Git revision. For persistent configuration, data, and work mounts with loopback-only port publishing, use the repository's `deploy/compose/a13n-harness-ui.yaml`. The image runs as UID/GID `10001:10001`; bind mounts must be writable by that account. Do not remove its volumes when preserving data. Restart rotates generated keys; supply the API-key environment variable at runtime when a stable key is needed.

The browser assets ship inside the wheel. End users do not need Node.js or a separate frontend checkout. For repository development, use `make webui`: it builds and installs the bundled assets, then starts the foreground server with isolated configuration/data in `var/harness-ui/`. No manual API key is required: without a supplied CLI or environment key, stdout contains a directly usable login link. Authentication is still required. Use `make webui WEBUI_ARGS='--port 9000 --no-share-computer'` to forward server options; `CLI_ARGS` forwards global options before the subcommand. See the [development guide](https://github.com/converge-ai-labs/agent-foundation/blob/main/dev/harness-ui/README.md) for configuration seeding and environment overrides.

## Options and ownership

See [the registered webui options](command-reference.md#webui) for listener, authentication, and compatibility aliases. Listener settings are process arguments rather than fields in root YAML. An open server owns active App work; closing a tab does not stop the server, and this is not a detached worker service.

For API clients, follow [the HTTP workflow and route reference](http-api.md). For an in-process interface, use [the Python App](embedding.md). For automation without a browser server, use [one-shot execution](automation-and-troubleshooting.md#automation-and-diagnostics).
