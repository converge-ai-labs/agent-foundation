# a13n Console

The private React and TypeScript web application for Agent Foundation Service. Console uses the shared `a13n-ui` design system and the TypeScript Service SDK. English is the default language; Simplified Chinese is available from the account menu.

## Local development

From the repository root, prepare local PostgreSQL, Redis and Langfuse, upgrade the schema, prepare frontend dependencies and the TypeScript SDK, and run Service and Console together. Configuration comes from `dev/service/local.toml`; no `.env` or manual Langfuse setup is required:

```bash
make dev
```

Keep the terminal open; Ctrl+C stops both application processes. If either process exits, the launcher stops the other and returns the exited process's status. The local Service configuration selects the Console origin for browser authentication. Use `make dev-reset STATE=seeded` before startup for fictional accounts and content, or `STATE=empty` for the initial administrator flow. See [local Service development](../../../dev/service/README.md).

To run only Console against an already running Service:

```bash
make frontend-sync
make sdk-typescript-build
pnpm --dir frontend --filter a13n-console dev
```

The local launcher assigns a stable Console port per checkout and passes the matching Service upstream. Run `make dev-status` to discover both URLs. A direct Vite invocation still accepts `A13N_CONSOLE_SERVICE_URL`. Configure the Service public origin as the Console origin so invitation, recovery, email confirmation, cookies, and WebSocket origin checks use the same browser boundary. Email flows require the Service SMTP configuration.

## Application surfaces

- Workspace Agents with configuration, immutable versions, and lifecycle controls.
- Sessions with threads, runs, retained and live output, attachments, branching, waiting feedback, steering, interruption, and queued messages.
- Models and providers, Skills and versions, and Environment providers, templates, and instances.
- Application accounts and targets, Connectors, and remote MCP connections and tool discovery.
- Run attempt details and Traces, including an explicit unavailable state when no trace query backend is configured.
- Workspace, organization, and personal settings, membership, invitations, API keys, service accounts, sessions, security activity, and profile images.

Usage and Schedules remain clearly marked as coming soon. Plugin, Secret, and Hook editors are outside the Console scope. Existing hidden configuration is preserved when editing supported fields.

Console uses Service permission hints for navigation and controls; the Service authorizes every request. API keys are workspace-bound, and one-time credentials are displayed only when created. The application has no demo data or embedded credentials.

## Validation and production build

```bash
pnpm --dir frontend --filter a13n-console check
pnpm --dir frontend --filter a13n-console build
make frontend-check-all
```

The production build is emitted to `dist/`. Hosting must serve the application shell for browser routes and route `/api` to the Service on the same origin, including WebSocket upgrades. Service deployment and static hosting configuration are managed separately. The design system showcase remains in `frontend/packages/a13n-ui/dev`.

Console tests use Node.js for `*.test.ts` and jsdom for browser/React `*.test.tsx` files. Only the latter load the DOM setup. Both run under `test`; use `--project=unit` or `--project=dom` for an explicit subset. Keep real keyboard interactions where they are under test; paste complete fixture URLs when only the resulting value matters. Timer behavior uses Vitest fake timers rather than waiting for wall-clock delays.
