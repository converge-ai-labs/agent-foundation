# Harness UI WebUI

This private English-only React application provides the workbench entry, instance authentication, guided setup, provider-account/key management, configuration resource editing and Project readiness. It uses the public `a13n-ui` design system, with local light/dark preferences and per-tab display profiles. Console localization is unchanged.

The browser consumes the Python App's HTTP, summary SSE and page-presence WebSocket APIs. It does not own agent execution or import the Service SDK. Conversation execution, shared prompting, saved-output comments, native Files/Git and terminal pages are subsequent workbench blocks, not enabled placeholder controls.

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
