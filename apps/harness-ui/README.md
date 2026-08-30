# Harness UI

Harness UI is the private browser source application bundled into the `a13n-ui` Python distribution. It is not published to npm and does not have an independent version or release workflow.

Repository automation builds this application, copies `dist/` into the generated `a13n_ui/static/` package tree, and verifies that both the Agent UI sdist and wheel contain the compiled assets. Neither generated directory is committed to Git. A wheel rebuilt from the sdist does not require Node.js.

Run the Agent UI CLI or validate/build its browser assets through the repository targets:

```bash
make a13n-ui
make harness-ui-check
make agent-ui-build
```

The Agent UI target starts the package-provided interactive CLI. Browser asset preparation and product design remain behind the dedicated frontend build targets; the browser consumes the same stable `AgentUiHost` operations as the CLI rather than owning another runtime.

During frontend-only development, Vite proxies relative `/api` requests to `http://127.0.0.1:8765`. Set `AGENT_UI_API_PROXY_TARGET` when the local Agent UI Host uses another loopback origin.
