# Harness UI WebUI

This private frontend implements the Harness UI browser authentication/status foundation. It consumes an access-key fragment, authenticates against `/api/status`, checks the API version, displays runtime/build information, and lets the user forget a saved key. It does not provide browser chat, setup editors, shared drafts, Host Files, Git, or terminal controls. Agent execution remains in the Python App, not the browser.

The npm package name is `a13n-harness-ui-webui`. It is not published to npm and does not have an independent version or release workflow. Repository automation builds this application and copies `dist/` into the generated `a13n_harness_ui/static/` package tree for the `a13n-harness-ui` Python wheel and sdist. Neither generated directory is committed. A wheel rebuilt from the sdist does not require Node.js.

## Development and validation

From the repository root:

```bash
make frontend-sync
pnpm --dir frontend --filter a13n-harness-ui-webui run dev
make frontend-check
make frontend-check-all
make a13n-harness-ui-build
```

The development server listens at `http://127.0.0.1:5174` and has no built-in `/api` proxy. Bare Vite is frontend-only; a successful status screen needs a separately supplied same-origin backend route. The working integrated path below builds assets and serves them from the Python listener. The frontend gate checks formatting, TypeScript, the backend contract snapshot, and authentication/status behavior; the complete gate also builds production assets.

To prepare packaged browser assets and serve them through the existing Python adapter:

```bash
make a13n-harness-ui-assets
uv run a13n-harness-ui webui
```

The Python runtime exposes more API operations than this browser currently renders. See [browser-server operation](../../../docs/a13n-harness-ui/webui.md) and [HTTP integration](../../../docs/a13n-harness-ui/http-api.md); available API routes are not a claim that corresponding browser controls exist.

## Backend contract snapshot

The checked-in `src/openapi.json` remains a snapshot of the Python adapter contract. It is not imported into the page. No browser client, TypeScript API declarations, or runtime validators are generated.

```bash
pnpm --dir frontend --filter a13n-harness-ui-webui run generate
pnpm --dir frontend --filter a13n-harness-ui-webui run generate:check
```

Generation requires the repository's locked Python environment and exports the schema without starting an App or listener. `generate:check` compares temporary output without rewriting the snapshot.
