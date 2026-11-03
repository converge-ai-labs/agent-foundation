# Harness UI WebUI

This private React application provides collaborative chats, execution/decision controls, instance authentication, guided setup, provider-account/key management, configuration resource editing and Project readiness. It uses the public `a13n-ui` design system, with local light/dark preferences and per-tab display profiles. Console localization is unchanged.

[Harness WebUI design](../../../spec/a13n-harness-ui/webui/06-design-guidelines.md) owns presentation; [workbench interaction](../../../spec/a13n-harness-ui/webui/04-workbench-interaction.md) owns behavior and recovery.

The browser consumes the Python App's HTTP, summary realtime and page-presence WebSocket APIs. It does not own agent execution or import the Service SDK. Comment UI is disabled without removing backend records or APIs; historical captured references remain readable. Native Files and read-only Git Changes open beside Chat, with a resizable terminal panel below. Terminal sessions use server-owned POSIX PTYs under the same native computer-sharing gate. These panels operate on the listener Host, not the Agent's selected Environment.

The npm package `a13n-harness-ui-webui` is private build input. Repository automation copies `dist/` into the generated `a13n_harness_ui/static/` tree for the Python wheel and sdist. Neither generated directory is committed. A wheel rebuilt from the sdist requires no Node.js.

## Development and validation

From the repository root:

```bash
make frontend-sync
make webui
```

`make webui` builds packaged assets and starts the authenticated Python listener using the isolated development configuration/data. Keep the generated instance key. For hot frontend development, leave that listener running and start Vite separately:

```bash
pnpm --dir frontend --filter a13n-harness-ui-webui run dev
```

Vite listens on `http://127.0.0.1:5174` and proxies `/api` HTTP and WebSocket traffic to `http://127.0.0.1:8765`. `A13N_HARNESS_UI_URL` overrides the proxy target. Enter the listener's key on the Vite page; do not disable authentication or add credentials to query strings. The proxy preserves Host/Origin and does not introduce a separate CORS policy.

```bash
make frontend-check-all
make a13n-harness-ui-build
```

The frontend gate covers formatting, generated contract drift, TypeScript, Vitest/jsdom, and real App protocol tests. Tests use isolated configuration/data and need neither paid models nor a browser. Use local browser checks for editor behavior, responsive layout, presence, and integrated server flows.

## Collaborative chats

The shell leads with New chat and keeps search, task options, workspace tools, and activity inspection behind named entry points. Model selection stays in the composer; active Goal and Coordinator choices remain visible. Routine usage shows context and cost; Debug reveals detailed counters and diagnostics. UI copy uses Chat consistently while transport and source identifiers keep their existing names.

`src/conversations` consumes App-owned history, focused realtime, execution/decision/child projections, and shared drafts. Keep saved history keyed by its continuation and retain the last successful transcript during refresh. Observation follows active and selected Threads independently of sidebar expansion; warming history must not mount composers, report presence, or acknowledge results. See the [collaboration contract](../../../spec/a13n-harness-ui/webui/01-collaborative-conversations.md) for observation and draft lifetimes.

For deterministic browser layout checks, run `tests/protocol_server.py --streaming-layout` with the locked Python environment. It streams Markdown through a disposable App without external models. Preserve mounted drafts and transcripts during background refresh; use the [workbench interaction contract](../../../spec/a13n-harness-ui/webui/04-workbench-interaction.md) for loading, scrolling, focus, and responsive behavior.

Each Thread owns an in-tab Yjs document and local UndoManager. `y-codemirror.next` binds the real CodeMirror editor; the adapter exchanges the existing complete v1 Yjs updates over authenticated JSON WebSockets, not a stock y-websocket protocol. Synchronization checks include deletion ranges. Submission clones visible input, invokes ordinary App admission once, and clears only that captured content after a positive receipt. Unknown responses do not clear or retry. Private local input appears immediately and joins streamed/saved content only by its exact source identity, never by text or receipt. It is not shared CRDT state. Route changes retain drafts; a new server draft incarnation requires explicit recovery. Home and every Project's plus action share one New chat slot: plus changes Project without clearing input. Its text, selections, Yjs identities and unresolved submission state are browser-persisted across reloads, independently of server shared drafts. Local attachment bytes remain tab-local and require reattachment after reload. Positive admission retires the persisted New slot; pending admission restores as unknown rather than replaying input.

Controls consume canonical request discriminators and exact receipt/parent identities. The composer has one primary action: Send while idle, Steer with text or attachments during an active operation, and Stop with an empty draft during an active operation. Unavailable or pending input disables submission rather than exposing a separate Stop button. Stop preserves the draft and keeps private receipt-owned feedback across navigation until the operation ends. A cancellation acknowledgement does not mean cleanup finished: unresolved Stop requests inspect their exact operation, disclose observation failures, and permit deliberate retries after rejected or uncertain responses only when active status has been observed. No cancellation POST is automatically replayed, and an old receipt cannot stop its replacement. Inspection separates captured and next-Run configuration; configuration conflicts retain dirty patches against their original version. Markdown does not execute supplied HTML or automatically fetch remote images. GFM tables, static code highlighting, and complete Mermaid diagrams share light/dark styling and exact-source copy controls. Adjacent reasoning shares one disclosure; context summaries remain distinct collapsed activities. Retry input is hidden behind a quiet status, while terminal failures show a concise reason. Child inspection keeps tools collapsed and pages through the latest complete saved result independently of comments. Mobile composition uses one-line height and follows the visible keyboard viewport without rebuilding the shared editor.

`tests/protocol_server.py` supplies an isolated real Python App with FunctionModel for Node protocol tests. Actual JavaScript Yjs replicas exercise HTTP admission, metadata, WS coediting and focused realtime together, without browsers, external models or user configuration. The normal Vitest command includes these tests alongside jsdom components and pure folds.

## Native Files and Changes

`src/native` consumes the existing Host APIs independently of Agent Environments. Configured roots and absolute paths navigate the server or container filesystem. File operations require their observed revisions; new-file/upload destinations must be absent, while explicit replacement uploads carry the existing file's observed revision. Native mutations are never automatically retried. Dirty CodeMirror buffers remain private to this tab across navigation and access replacement. Conflicts and unknown save outcomes retain local text and offer an inspected disk version before explicit adoption or replacement. Common line endings are preserved; editing mixed endings explicitly normalizes them to the first style.

Changes distinguishes HEAD/index, index/worktree and new-file comparisons, retains repository and patch identities, and discloses binary, rename, conflict, ignored and unborn states. Read failures do not mean a clean tree. Refresh after native actions or returning to the pane updates observations without replacing dirty buffers. There are no stage, commit, Git-discard or worktree controls.

Add to prompt captures reviewed disk bytes or exact patch lines using the existing Thread attachment owner, then selects only its returned handle in that chat's shared composer. Opening/editing never adds input. Source metadata and captured bytes remain inspectable even after the native source changes. Nonstandard line separators that differ from the backend's line numbering use explicit whole-source capture rather than an approximate range. Steering shares ordinary submission's image/file preparation and attachment limits; binary/large captures retain their bytes as file references.

## Configuration editing

Resources are complete source documents, with tailored common fields and a lazy-loaded CodeMirror YAML editor. Structured edits retain unrelated YAML fields/comments. Validation checks a candidate; publication writes a source and returns the accepted generation. Writes are last-write-wins, not source compare-and-swap. Active runs keep their captured configuration.

Source drafts are memory-only and survive navigation and access-key replacement within the current tab. They do not survive a reload or browser closure. An observed external source change is disclosed without overwriting the local draft. MCP source content is intentionally unreadable through the API: replacing it is explicit, affects the entire source, and cannot preserve unseen secrets or fields automatically. Invalid disk candidates are not exposed as accepted source text.

Project preview resolves accepted configuration and reports per-axis provenance. Inherited lists, explicitly empty lists and custom selections are distinct. Readiness does not run a model or verify provider credentials. Display profiles, provider accounts and instance access keys remain separate concepts.

## App-owned generated contract

`src/openapi.json` snapshots the Python adapter's contract; `src/api.generated.ts` provides private HTTP and interactive component types through `openapi-typescript`. `openapi-fetch` consumes these types. Neither the schema nor a frontend-owned version is used to fabricate server status.

```bash
pnpm --dir frontend --filter a13n-harness-ui-webui run generate
pnpm --dir frontend --filter a13n-harness-ui-webui run generate:check
```

Generation uses the locked Python environment without opening an App. The drift check compares temporary outputs without modifying committed files. Runtime channel decoders remain separate from generated compile-time declarations.

See [browser-server operation](../../../docs/a13n-harness-ui/webui.md) and [HTTP integration](../../../docs/a13n-harness-ui/http-api.md) for backend behavior and lifecycle boundaries.

## Display language

Login, connection startup, guided setup, its model editor and provider-account connection controls, and setup/readiness support English and Simplified Chinese. The Agent & Model panel also localizes Agent/Model selection, search, Thinking, Speed and Reasoning mode, including known control descriptions, default summaries and unavailable-selection guidance. Workbench navigation, Project and Recent chat lists, Drafts, chat search, and Archived chat lists and deletion confirmation also support both languages, including their menus, status labels, counts and accessible text. The conversation transcript and live message stream support both languages across Thought, activity and system-update disclosures, Work log entry points, failure notices, message and code copy feedback, Markdown code, table, diagram and image controls including the expanded diagram and image viewer, context summary states, generated execution outcomes and subagent states, and live-update connection status. Destination workflows such as Project configuration and collaboration settings remain separate localization work under [#1248](https://github.com/converge-ai-labs/agent-foundation/issues/1248).

Sharing link management also supports both languages: the Share entry and dialog, chat/Project scope, permissions, expiry, copying, permission changes and revocation. Language changes retain selected options, an issued link and copy feedback, expanded grant management, unsaved permission edits and pending operations without replaying requests. Grant dates and counts follow the display language; authored Project names and raw server diagnostics remain unchanged. Guest-page localization remains separate work. Other workbench workflows are not fully localized, though shared network error and retry prompts appear in both languages wherever they are used.

Choose **System**, **English**, or **简体中文** during login/setup or in General settings. The preference belongs to this viewer's browser, independently of Console. System scans browser languages in order: `en` variants select English, `zh` variants select Simplified Chinese, and no supported match selects English. Automatic resolution never saves a manual language choice. If browser storage is unavailable, a manual choice lasts for the current page session.

Switching updates text and document language without remounting the application or replaying requests. Navigation retains the selected chat, Project and worker expansion, loaded history, search input and Project filter, keyboard ordering and sidebar width. Authored chat titles and excerpts remain verbatim, including titles that match interface messages; only an absent title/excerpt uses the translated fallback. The transcript retains loaded turns, reading position and output following, open Thought, activity and Work log disclosures, an expanded image viewer with its zoom state, and its live subscriptions; the stream reducer reports operation, execution and subagent states instead of interface text, so backend-authored part kinds, run and subagent messages, notification text, streamed output, code, diagram source, context diagnostics, recovery outcome values and unknown live-update states remain verbatim. The existing MCP Apps Host-context observer forwards locale changes without remounting Apps or replaying tool results. Model/user content, source, credentials, provider diagnostics, identifiers and paths retain their original values.

Messages live in application-owned `src/i18n/locales/{en,zh-CN}.json`. Use `useTranslation` from `src/i18n`, whole messages with interpolation, i18next number formatting, and each language's plural categories. Shared `a13n-ui` components receive translated strings from their callers. English fallback remains available at runtime; keep both catalogs complete for every localized message. `conversations/model-control-messages.ts` translates only known application-owned control descriptors and exact budget/effort summary formats. Keep its allowlist aligned with the backend descriptor producers; unknown diagnostics, custom labels, resource names, IDs and routes remain verbatim. Selection values and capability decisions remain backend-owned.

Run the focused catalog, preference and language-control checks from the repository root:

```bash
pnpm --dir frontend --filter a13n-harness-ui-webui test src/i18n src/conversations/composer-settings.test.tsx src/conversations/model-control-messages.test.ts
pnpm --dir frontend --filter a13n-harness-ui-webui test src/conversations/navigation.test.tsx src/conversations/archived.test.tsx src/shell/sidebar.test.tsx
pnpm --dir frontend --filter a13n-harness-ui-webui test src/sharing.test.tsx src/shell/connection.test.tsx
pnpm --dir frontend --filter a13n-harness-ui-webui test src/conversations/transcript-language.test.tsx
```

These tests also run in the existing Harness UI frontend CI suite. Catalog checks cover missing/empty values, interpolation and locale-required plurals, with invalid fixtures. Literal translation calls in localized workflows, dynamic login-state labels and model-control descriptors have focused coverage. Navigation tests also cover runtime switching during search, keyboard ordering, worker expansion, sidebar resizing and archive restoration. Agent/Model interaction checks preserve the open panel page, search input and selection, editor/undo identities, draft parameters and unresolved submission state while switching languages on desktop and mobile. Transcript checks cover saved and live rows, plural reconnection counts, known and unknown live-update states, and Markdown controls, asserting that the open disclosure, element identity and model content survive the switch. Replayed stream events cover context, execution and subagent states together with verbatim diagnostics, and the expanded viewer keeps its open and zoom state across a switch. Explicit false and inherited null remain distinct; changing display language neither applies selections nor replays requests. Application-wide untranslated-source detection and the remaining tool-call and usage details, configuration and native-panel workflows remain follow-up work under #1248.
