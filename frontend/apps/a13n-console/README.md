# a13n Console

The private React and TypeScript web application for Agent Foundation Service. Console uses the shared `a13n-ui` design system and its private Service client. English is the default language; Simplified Chinese is available from the account menu.

## Local development

From the repository root, start this checkout's PostgreSQL and Redis, apply migrations, and run the local scripted model, the Service and Console in the background:

```bash
make dev
```

The command returns once all three accept connections and prints the Console URL; `make dev-stop` stops them and `make dev-foreground` runs them attached. It writes this checkout's Service settings to `var/dev/service.toml`, with the Console origin as the Service public URL; no `.env` is required. Use `make dev-reset STATE=seeded` for fictional accounts and content, or `STATE=empty` for the initial administrator flow. See [local Service development](../../../dev/service/README.md).

To run only Console against an already running Service:

```bash
make frontend-sync
pnpm --dir frontend --filter a13n-console dev
```

The local launcher assigns a stable Console port per checkout and passes the matching Service upstream. Run `make dev-status` to discover both URLs. A direct Vite invocation reads the Service upstream from `A13N_CONSOLE_SERVICE_URL` (default `http://127.0.0.1:8000`). Configure the Service public origin as the Console origin so invitation, recovery, email confirmation, connection authorization returns, cookies, and origin checks use the same browser boundary. Email flows require the Service SMTP configuration.

## Application surfaces

- Workspace Agents with configuration, immutable versions, and lifecycle controls.
- Sessions with threads, runs, retained and live output, attachments, waiting feedback, steering, interruption, and queued messages.
- Models and providers, Skills and versions, file Memories with their history, and Environment providers, templates, and instances.
- Connectors, and remote MCP connections and tool discovery.
- Run attempt details and Traces.
- Workspace, organization, and personal settings, membership, invitations, API keys, service accounts, sessions, and audit events.

Existing hidden configuration is preserved when editing supported fields.

Console uses Service permission hints for navigation and controls; the Service authorizes every request. API keys are workspace-bound, and one-time credentials are displayed only when created. The application has no demo data or embedded credentials.

## Service contract

Console owns `src/service-client/`; it does not install, build, or release with an external Service SDK. HTTP types are generated from `proto/a13n-service/openapi.json`, not edited by hand. After changing a Service route or model, run `make service-contract-generate`; `make service-contract-check` detects drift without modifying files. Transport, session/CSRF, and thread stream tests run against the internal client.

## Validation and production build

```bash
pnpm --dir frontend --filter a13n-console check
pnpm --dir frontend --filter a13n-console build
make frontend-check-all
```

The production build is emitted to `dist/`. Hosting must serve the application shell for browser routes and route `/api` to the Service on the same origin, without buffering event streams. Service deployment and static hosting configuration are managed separately. The design system showcase remains in `frontend/packages/a13n-ui/dev`.

Console tests use Node.js for `*.test.ts` and jsdom for browser/React `*.test.tsx` files. Only the latter load the DOM setup. Both run under `test`; use `--project=unit` or `--project=dom` for an explicit subset. Keep real keyboard interactions where they are under test; paste complete fixture URLs when only the resulting value matters. Timer behavior uses Vitest fake timers rather than waiting for wall-clock delays.
