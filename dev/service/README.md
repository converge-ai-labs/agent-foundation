# Local Service development

This directory owns the repository's local Service workflow. The stable entrypoint is the root Makefile; agents and people use the same commands.

## Quick start

```sh
make dev
```

The command assigns this checkout a stable local identity and loopback ports, prepares changed dependencies and build outputs, starts its PostgreSQL and Redis stores, reuses the machine-shared Langfuse stack, applies migrations once, then starts Service, the scripted model, and Console in the background. It returns after all three TCP listeners accept connections. Applications run in an independent OS session with no terminal input and write logs to `var/dev/applications.log`, so closing the launching terminal or completing an agent turn does not send them the launcher's shutdown signals.

Use `make dev-stop` to drain the background applications. Use `make dev-foreground` when attached logs and Ctrl+C ownership are preferable; press Ctrl+C once to drain all application process groups and again to force-stop them if shutdown stalls. Infrastructure and data remain available for the next start.

Use `make service-dev` to run Service without Console in the foreground. Use `make setup` to prepare dependencies, infrastructure, and schema without starting an application listener. Successful preparation is fingerprinted in `var/dev/preparation.json`; unchanged Python and frontend dependency inputs with their required outputs skip the corresponding work. Console compiles its own internal Service client; preparation neither installs nor builds an external SDK. Phase timings are printed on every setup.

Do not assume ports. Discover the current checkout without changing it:

```sh
make dev-status
```

The JSON response includes the instance ID, assigned ports, URLs, listener state, state path, and incomplete-reset state. In a fresh checkout it reports `configured: false`; status never allocates ports, installs dependencies, starts Docker, or migrates data. `make dev`, `make service-dev`, `make setup`, reset, and explicit infrastructure commands create `var/dev/instance.json` when needed. That ignored file contains only checkout identity and ports, not secrets or full settings.

## Data and lifecycle

Every checkout owns a distinct Compose project, PostgreSQL volume, Redis volume, optional Mem0 stack, business files, object bytes, model listener, Service listener, and Console listener. Port allocation and instance persistence are serialized by a machine lock. An existing assignment is stable: if one of its ports is occupied, startup fails and identifies the conflict instead of silently changing addresses already stored in seeded model or MCP resources. All host listeners bind `127.0.0.1`.

```sh
make dev-stop                 # Stop detached Service, model, and Console applications
make dev-down                 # Stop this checkout's infrastructure; preserve its data
make dev-reset STATE=empty    # Delete only this checkout's business state; rebuild the schema
make dev-reset STATE=seeded   # Delete only this checkout's state; provision fictional journeys
```

Stop the checkout's applications with Ctrl+C or `make dev-stop` before reset. A lifecycle lock prevents setup, serve, down, and reset races. Reset checks for database and Redis clients and leaves `var/dev/reset-incomplete` when rebuilding fails; rerun the intended reset to recover. It never kills a process merely because that process owns a port. `dev-down` and reset do not stop or delete shared Langfuse.

The `empty` state contains only the migrated schema. The `seeded` state retains the representative UI and API journeys: Agents, Skills, Assets, both built-in Web Provider types with fictional credentials, three bulk Sessions, a 13-Run conversation, attachments, revisions, failure/retry, fork, pending client feedback, cancellation/queue, structured output, child Threads, MCP, publication, and an empty Workspace. Seeding uses real Service execution and persistence, verifies semantics, and writes `seed.json`, `seed-report.md`, and private diagnostics under `var/dev/service/`. Seeding happens only on explicit reset. The fictional Web Provider credentials make the accounts selectable in Agent configuration but cannot make real Brave or Exa requests.

After a seeded reset, sign in as `admin@example.com` with `local-public-password-123`. The builder, runner, and viewer fictional accounts use the same password. Each instance uses its own cookie name so two Console origins on `127.0.0.1` can keep independent sessions in one browser. Cookies remain `Secure`, `HttpOnly`, and `SameSite=Lax`; unique names prevent accidental collision but are not a security boundary.

The seeded default Workspace includes one demonstration connection and one disabled example Model for every built-in Model Provider type, alongside the working local scripted Provider. Demonstration credentials are fictional and cannot authenticate to cloud providers; Azure uses the reserved example endpoint, and Ollama points to a local endpoint that need not be running. Example Models use representative catalog identities where available, with directory declarations when the catalog is reachable. They exercise Model and Provider management, not live inference. Seeded Agents continue to use the local scripted model.

## Shared Langfuse

One machine-owned Compose project, `agent-foundation-local-langfuse-v2`, serves all local worktrees at `http://127.0.0.1:3000` (media on 3001). Its Compose file and manifest are copied to machine-owned state on first initialization. Later starts use that canonical copy; a checkout whose source Compose file differs fails before issuing a Docker mutation. Setup starts the shared stack on demand and reuses it after health and project-authentication checks. Traces carry the distinct `local-<instance-id>` deployment environment label.

```sh
make langfuse-up       # Start or verify the shared stack
make langfuse-test     # Exercise OTLP write and authorized trace query
make langfuse-down     # Stop the shared stack and preserve every worktree's traces
make langfuse-reset    # Explicitly delete all shared local trace data
```

These commands affect the shared resource, not only the current checkout. A successful `langfuse-reset` deletes the canonical configuration record as well as trace data, so the next start can initialize a changed Compose version; `langfuse-down` preserves both. `down` and `reset` use the recorded canonical Compose file even when the invoking checkout has since changed. They do not adopt, stop, delete, or migrate resources from the former `agent-foundation-local-langfuse` project or legacy checkout-specific stacks. A port or configuration incompatibility fails without resetting shared data. Keep an older project if you need its traces, or stop it explicitly with the matching older checkout/configuration before starting v2.

The public local account is `dev@agent-foundation.local` / `agent-foundation-local`. All credentials are fictional. Local launchers remove inherited `OTEL_*` settings before applying the selected profile so ambient collectors cannot receive local content. Loopback traffic bypasses inherited proxies; unrelated destinations preserve their proxy policy.

### Using Logfire

The commented alternative in `local.toml` supports both OTLP export and Console Trace Query. Copy the template to an ignored private file, select `provider = "logfire"`, set the project's US or EU regional root URL and a timezone-aware history lower bound, and supply a project read token. Export a separate project write token for OTLP:

```sh
export LOGFIRE_TOKEN='YOUR_LOGFIRE_WRITE_TOKEN'
export A13N_SERVICE_OBSERVABILITY_QUERY_LOGFIRE_READ_TOKEN='YOUR_LOGFIRE_READ_TOKEN'
make dev SERVICE_CONFIG=dev/service/local.private.toml
```

Use the same project for both tokens, but do not assume a write token can query. The launcher does not load `.env` files. It preserves private observability values while deriving local instance policy and does not start Langfuse for a Logfire profile. Switching providers neither resets Service data nor migrates traces. For export without Console queries, select query provider `none`, set `A13N_DEV_TRACE_BACKEND=logfire`, and set `LOGFIRE_BASE_URL` for a non-US project. To disable tracing, select provider `none` and set tracing false.

### Incremental Logfire execution check

With a seeded baseline and Service already running with that private Logfire configuration, this opt-in regression creates eight fictional Runs and verifies ingestion, adapter projections, pagination, relationships, and Run/IAM authorization through the production HTTP API:

```sh
A13N_TEST_LOGFIRE_CONFIG=dev/service/local.private.toml \
A13N_TEST_LOGFIRE_OUTPUT=var/logfire-check-$(date +%Y%m%d-%H%M%S) \
uv run pytest dev/service/tests/test_logfire_integration.py -q --tb=short
```

The check never initializes identity or resets/reseeds storage, and ordinary test runs skip it. It records Run/Attempt IDs and verified projections in the unique output directory. Backend reads are spaced by ten seconds by default and boundedly retry transient rate limits, so validation can take several minutes. Set `A13N_TEST_LOGFIRE_QUERY_INTERVAL` only to match the selected project's budget. After an interrupted readback, point `A13N_TEST_LOGFIRE_RUNS` at the earlier output directory's complete `runs.json` and choose a new output directory to verify the existing Runs without creating more.

## Configuration boundary

`local.toml` is a committed local-only template. Normal Service configuration remains unaware of worktrees. The development resolver loads the selected TOML and normal environment/private overrides, then derives all local policy in one place: Service identity and port, Console target, IAM and connectivity origins/callbacks, session cookie name, model/MCP origin, PostgreSQL and Redis URLs, object/filesystem roots, Mem0 port, Compose ownership, and trace environment label.

The resolver refuses remote or differently named stores and symlinked state. Private observability credentials are preserved and never written to `instance.json`. No `.env` is loaded. Production uses the ordinary `iam.session_cookie_name` setting, whose default remains `a13n_session`; production packages contain no worktree or `dev.service` imports.

Optional Mem0 remains checkout-owned and disabled by default in `dev/mem0/local.toml`. Enabling it uses the instance-assigned port, so concurrent worktrees do not share the old fixed port. See [the Mem0 guide](../mem0/README.md) for provider creation and real embedding configuration.

The v2 business Compose namespace is `a13n-dev-v2-<instance-id>`. A checkout may report exact resources retained under the former `a13n-local-<instance-id>` namespace. Those volumes are deliberately not adopted, migrated, stopped, or reset; use the older checkout if their data must be inspected. The warning is informational and no destructive compatibility action is automatic.

## Failure recovery and validation

- A stable application port conflict: stop the reported owner, or use another worktree. Do not edit or delete the instance record: the machine registry restores the same assignment to preserve stored endpoint references.
- An incomplete reset: rerun `make dev-reset` with the intended `STATE`.
- Docker unavailable: start the selected daemon and retry. On macOS the helper may open a compatible local Docker Desktop endpoint; it never switches Docker context.
- Shared Langfuse conflict: inspect the existing owner. The helper never deletes or replaces it implicitly.

Run `make dev-state-check` for local resolver, lifecycle, seed, and ownership tests. The reset integration test uses disposable uniquely owned containers and never resets a developer checkout or shared Langfuse.
