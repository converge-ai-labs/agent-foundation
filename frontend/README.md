# Frontend workspace

Private pnpm workspace for a13n browser applications. Use Node.js 24 and the pnpm version pinned in `package.json`.

- `apps/a13n-console`: minimal Console entry point with English and Simplified Chinese resources.
- `apps/a13n-harness-ui`: Harness UI browser application, compiled into its Python distribution.
- `packages/a13n-ui`: reserved shared UI source; no components or styles yet.

From the repository root:

```bash
make frontend-sync
make frontend-check-all
pnpm --dir frontend --filter a13n-console dev
pnpm --dir frontend --filter a13n-harness-ui-webui dev
```

`make install`, `make format`, `make check`, `make build`, and `make check-all` include this workspace. `make a13n-harness-ui-assets` prepares only the browser assets needed by the Python distribution. Harness UI contract checks also require the repository's uv/Python environment.

Applications own their business state, API integration, and translations. Shared UI source must not import application modules. Add workspace packages and dependencies when implementations need them; the reserved UI directory is not an empty runtime dependency. The standalone TypeScript SDK remains outside this workspace.

Console starts in English regardless of browser language. Its bundled `en` and `zh-CN` resources establish the translation boundary; language settings and persistence are outside this scaffold.
