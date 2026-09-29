# Frontend workspace

Private pnpm workspace for a13n browser applications. Use Node.js 24 and the pnpm version pinned in `package.json`.

- `apps/a13n-console`: Service management and conversation application with English and Simplified Chinese resources.
- `apps/a13n-harness-ui`: Harness UI browser application, compiled into its Python distribution.
- `apps/a13n-docs`: the Fumadocs documentation site built from the repository `docs/` Markdown; see [Documentation Changes](../CONTRIBUTING.md#documentation-changes).
- `packages/a13n-ui`: shared design tokens, React primitives, and an independent development showcase.

From the repository root:

```bash
make dev
```

This prepares dependencies and runs Service and Console together. Ctrl+C stops both application processes.

For individual frontend checks and development servers:

```bash
make frontend-sync
make frontend-check       # Formatting, types, and generated contracts
make frontend-test        # Unit and interaction tests
make frontend-build       # Application assets, UI showcase, and docs site
make frontend-check-all   # All three, without repeated type checking
pnpm --dir frontend --filter a13n-ui dev
pnpm --dir frontend --filter a13n-console dev
pnpm --dir frontend --filter a13n-harness-ui-webui dev
make docs-serve
```

`make install`, `make format`, `make check`, `make build`, and `make check-all` include this workspace. `make a13n-harness-ui-assets` prepares only the browser assets needed by the Python distribution. Harness UI contract checks also require the repository's uv/Python environment.

Package scripts follow the same separation: `check` is static validation, `test` runs tests, and `build` produces assets without repeating type checking. For a Console-only change, use `pnpm --dir frontend --filter a13n-console check` and pass the relevant test files to `pnpm --dir frontend --filter a13n-console test`.

Applications own their business state, API integration, and translations. Shared UI source must not import application modules. Console consumes the private `a13n-ui` package through a workspace dependency. The standalone TypeScript SDK remains outside this workspace.

Console defaults to English and lets users select English or Simplified Chinese. It remembers the selection locally when browser storage is available.
