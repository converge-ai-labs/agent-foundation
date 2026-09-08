# Harness UI WebUI

This private frontend currently renders only a Hello World page. Browser application features have not been implemented. It does not connect to the Harness UI HTTP API, authenticate, or start an agent runtime.

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

The development server listens at `http://127.0.0.1:5174`. The frontend gate checks formatting, TypeScript, the backend contract snapshot, and the Hello World rendering test; the complete gate also builds production assets.

To prepare packaged browser assets and serve them through the existing Python adapter:

```bash
make a13n-harness-ui-assets
uv run a13n-harness-ui webui
```

The Python runtime and HTTP API remain separate from this placeholder page.

## Backend contract snapshot

The checked-in `src/openapi.json` remains a snapshot of the Python adapter contract. It is not imported into the page. No browser client, TypeScript API declarations, or runtime validators are generated.

```bash
pnpm --dir frontend --filter a13n-harness-ui-webui run generate
pnpm --dir frontend --filter a13n-harness-ui-webui run generate:check
```

Generation requires the repository's locked Python environment and exports the schema without starting an App or listener. `generate:check` compares temporary output without rewriting the snapshot.
