# Harness UI WebUI

This private English-only React application provides collaborative conversations, execution/decision controls, instance authentication, guided setup, provider-account/key management, configuration resource editing and Project readiness. It uses the public `a13n-ui` design system, with local light/dark preferences and per-tab display profiles. Console localization is unchanged.

The browser consumes the Python App's HTTP, summary SSE and page-presence WebSocket APIs. It does not own agent execution or import the Service SDK. Comment UI is disabled without removing backend records or APIs; historical captured references remain readable. Native Files and read-only Git Changes run beside Chat on wide screens and switch views on narrow screens. The terminal browser panel is the next workbench block, not an enabled placeholder control.

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

Vite listens on `http://127.0.0.1:5174` and proxies `/api` HTTP, SSE and WebSocket traffic to `http://127.0.0.1:8765`. `A13N_HARNESS_UI_URL` overrides the proxy target. Enter the listener's key on the Vite page; do not disable authentication or add credentials to query strings. The proxy preserves Host/Origin and does not introduce a separate CORS policy.

```bash
make frontend-check-all
make a13n-harness-ui-build
```

The frontend gate covers formatting, generated contract drift, TypeScript and Vitest/jsdom behavior tests. Vitest keeps per-file isolation and prebundles external UI dependencies instead of reloading their full module trees in each jsdom suite. The CodeMirror/Yjs entries share one optimized module graph to preserve class identity; Node crypto remains native. Local runs use two workers; CI uses four on an eight-core runner, leaving capacity for isolated Python App fixtures. CI reports WebUI checking, testing, and asset building as separate steps. Automated tests do not require a real browser or a browser CI runner. Local browser verification supplements unit and protocol tests for CodeMirror, responsive layout, presence and integrated server flows without becoming a test-suite dependency. Tests and smoke sessions use isolated configuration/data and do not require paid models.

## Collaborative conversations

`src/conversations` consumes existing App-owned history, focused SSE, operation/decision/child projections and the shared draft WebSocket protocol. Saved history stays keyed by its actual continuation; replacement fetches do not erase the last successful transcript. Root identity changes obtain a fresh observer prefix/base before continuing display. Workbench-owned observation retains up to three focused subscriptions (the selected Thread first, then recent active roots) and eight recent displays. Global activity discovery is independent of sidebar expansion. Retained query observers warm current history and reconcile background completion without mounting composers, reporting presence, or acknowledging results. Excess active roots retain summary updates and load focused output on selection. Access replacement releases the whole observation lifetime. No durable browser transcript store, execution queue or reconnect framework is introduced.

Startup uses a public themed shell while validating access; only rejected credentials show key entry, while connection failures offer retry. Streaming commits and resize callbacks share immediate bottom following; only explicit New output navigation animates. Short replay handshakes stay quiet and connection notices do not resize the reader. For deterministic local browser layout checks, run `tests/protocol_server.py --streaming-layout` with the locked Python environment; it uses disposable configuration and streams repeated Markdown paragraphs without external models. Initial entry reveals the conversation after its required first observations initialize behind one loading surface; failures and a slow-load escape remain explicit. Background refreshes and later sends preserve the mounted composer and transcript. Submission gives immediate local feedback, explains blocked prerequisites, and restores input focus without taking it from another field. Pending completion checks without visible candidates do not consume Enter.

Each Thread owns an in-tab Yjs document and local UndoManager. `y-codemirror.next` binds the real CodeMirror editor; the adapter exchanges the existing complete v1 Yjs updates over authenticated JSON WebSockets, not a stock y-websocket protocol. Synchronization checks include deletion ranges. Submission clones visible input, invokes ordinary App admission once, and clears only that captured content after a positive receipt. Unknown responses do not clear or retry. Private local input appears immediately and joins SSE/saved content only by its exact source identity, never by text or receipt. It is not shared CRDT state. Route changes retain drafts; a new server draft incarnation requires explicit recovery. Home and every Project's plus action share one New conversation slot: plus changes Project without clearing input. Its text, selections, Yjs identities and unresolved submission state are browser-persisted across reloads, independently of server shared drafts. Local attachment bytes remain tab-local and require reattachment after reload. Positive admission retires the persisted New slot; pending admission restores as unknown rather than replaying input.

Controls consume canonical request discriminators and exact receipt/parent identities. The composer has one primary action: Send while idle, Steer with text or attachments during an active operation, and Stop with an empty draft during an active operation. Unavailable or pending input disables submission rather than exposing a separate Stop button. Stop preserves the draft and keeps private receipt-owned feedback across navigation until the operation ends. A cancellation acknowledgement does not mean cleanup finished: unresolved Stop requests inspect their exact operation, disclose observation failures, and permit deliberate retries after rejected or uncertain responses only when active status has been observed. No cancellation POST is automatically replayed, and an old receipt cannot stop its replacement. Inspection separates captured and next-Run configuration; configuration conflicts retain dirty patches against their original version. Markdown does not execute supplied HTML or automatically fetch remote images. GFM tables, static code highlighting, and complete Mermaid diagrams share light/dark styling and exact-source copy controls. Adjacent reasoning shares one disclosure; context summaries remain distinct collapsed activities. Retry input is hidden behind a quiet status, while terminal failures show a concise reason. Child inspection keeps tools collapsed and pages through the latest complete saved result independently of comments. Mobile composition uses one-line height and follows the visible keyboard viewport without rebuilding the shared editor.

`tests/protocol_server.py` supplies an isolated real Python App with FunctionModel for Node protocol tests. Actual JavaScript Yjs replicas exercise HTTP admission, metadata, WS coediting and focused SSE together, without browsers, external models or user configuration. The normal Vitest command includes these tests alongside jsdom components and pure folds.

## Native Files and Changes

`src/native` consumes the existing Host APIs independently of Agent Environments. Configured roots and absolute paths navigate the server or container filesystem. File operations require their observed revisions; new-file/upload destinations must be absent, while explicit replacement uploads carry the existing file's observed revision. Native mutations are never automatically retried. Dirty CodeMirror buffers remain private to this tab across navigation and access replacement. Conflicts and unknown save outcomes retain local text and offer an inspected disk version before explicit adoption or replacement. Common line endings are preserved; editing mixed endings explicitly normalizes them to the first style.

Changes distinguishes HEAD/index, index/worktree and new-file comparisons, retains repository and patch identities, and discloses binary, rename, conflict, ignored and unborn states. Read failures do not mean a clean tree. Refresh after native actions or returning to the pane updates observations without replacing dirty buffers. There are no stage, commit, Git-discard or worktree controls.

Add to prompt captures reviewed disk bytes or exact patch lines using the existing Thread attachment owner, then selects only its returned handle in that conversation's shared composer. Opening/editing never adds input. Source metadata and captured bytes remain inspectable even after the native source changes. Nonstandard line separators that differ from the backend's line numbering use explicit whole-source capture rather than an approximate range. Steering shares ordinary submission's image/file preparation and attachment limits; binary/large captures retain their bytes as file references.

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
