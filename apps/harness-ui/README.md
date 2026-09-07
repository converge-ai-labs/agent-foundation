# Harness UI

Harness UI is the private browser source application bundled into the `a13n-ui` Python distribution. It is not published to npm and does not have an independent version or release workflow.

Repository automation builds this application, copies `dist/` into the generated `a13n_ui/static/` package tree, and verifies that both the Agent UI sdist and wheel contain the compiled assets. Neither generated directory is committed to Git. A wheel rebuilt from the sdist does not require Node.js.

Run the Agent UI TUI or validate/build its browser assets through the repository targets:

```bash
make a13n-ui
make harness-ui-check
make agent-ui-build
```

The Agent UI target starts the package-provided interactive TUI. Browser asset preparation and product design remain behind the dedicated frontend build targets; the browser consumes the same stable `AgentUiApp` operations as the TUI and management CLI rather than owning another runtime.

During frontend-only development, Vite proxies relative `/api` requests to `http://127.0.0.1:8765`. Set `AGENT_UI_API_PROXY_TARGET` when the local Agent UI App adapter uses another loopback origin.

## Development and contract generation

From the repository root, prepare assets and start the real foreground adapter:

```bash
make agent-ui-assets
uv run a13n-ui webui
```

For an isolated configuration, pass global `--config` and `--data-root` before `webui`. Keep fixture accounts and stub models separate from real subscription stores when testing. The browser captures the terminal's fragment bootstrap key before routing and sends authenticated relative API requests; it does not own an App or replay failed commands.

The checked-in OpenAPI document, TypeScript declarations, and standalone runtime validators derive from the Python adapter without starting an App:

```bash
npm --prefix apps/harness-ui run generate
npm --prefix apps/harness-ui run generate:check
npm --prefix apps/harness-ui run test
make harness-ui-check-all
```

`generate:check` creates temporary output and detects drift without rewriting committed sources. Standalone validators are generated ahead of time because the production Content Security Policy disallows runtime `eval`. Do not add `unsafe-eval` to make runtime schema compilation work.

Vitest and Testing Library cover revision-aware drafts, create/admit failure recovery, unknown outcomes, and stale navigation. Python adapter tests cover real HTTP/SSE disconnect cleanup and resume. Browser QA uses the built application, a real local adapter, disposable configuration/data roots, and a deterministic model rather than sending real subscription requests.

Vite's development proxy must preserve the production same-origin/Host boundary; when testing authentication and CSP, prefer the packaged static tree served by the actual adapter. Production assets are local; React Markdown does not execute returned HTML or automatically fetch arbitrary Markdown images.
