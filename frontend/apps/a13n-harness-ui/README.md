# Harness UI WebUI

This private English-only React application provides collaborative conversations, execution/decision controls, instance authentication, guided setup, provider-account/key management, configuration resource editing and Project readiness. It uses the public `a13n-ui` design system, with local light/dark preferences and per-tab display profiles. Console localization is unchanged.

The browser consumes the Python App's HTTP, summary SSE and page-presence WebSocket APIs. It does not own agent execution or import the Service SDK. Saved-output comments and native Files/Git/terminal browser panels are subsequent workbench blocks, not enabled placeholder controls.

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

The frontend gate covers formatting, generated contract drift, TypeScript and Vitest/jsdom behavior tests. Automated tests do not require a real browser or a browser CI runner. Local browser verification supplements unit and protocol tests for CodeMirror, responsive layout, presence and integrated server flows without becoming a test-suite dependency. Tests and smoke sessions use isolated configuration/data and do not require paid models.

## Collaborative conversations

`src/conversations` consumes existing App-owned history, focused SSE, operation/decision/child projections and the shared draft WebSocket protocol. Saved history stays keyed by its actual continuation; replacement fetches do not erase the last successful transcript. Root identity changes obtain a fresh observer prefix/base before continuing display. No browser transcript store, execution queue or durable reconnect framework is introduced.

Each Thread owns an in-tab Yjs document and local UndoManager. `y-codemirror.next` binds the real CodeMirror editor; the adapter exchanges the existing complete v1 Yjs updates over authenticated JSON WebSockets, not a stock y-websocket protocol. Synchronization checks include deletion ranges. Submission clones visible input, invokes ordinary App admission once, and clears only that captured content after a positive receipt. Unknown responses do not clear or retry. Route changes retain drafts; a new server draft incarnation requires explicit recovery.

Controls consume canonical request discriminators and exact receipt/parent identities. Inspection separates captured and next-Run configuration; configuration conflicts retain dirty patches against their original version. Markdown does not execute supplied HTML or automatically fetch remote images. Saved assistant raw text and output targets remain intact for later comment integration.

`tests/protocol_server.py` supplies an isolated real Python App with FunctionModel for Node protocol tests. Actual JavaScript Yjs replicas exercise HTTP admission, metadata, WS coediting and focused SSE together, without browsers, external models or user configuration. The normal Vitest command includes these tests alongside jsdom components and pure folds.

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
