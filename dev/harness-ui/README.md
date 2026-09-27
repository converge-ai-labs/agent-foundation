# Harness UI development tracing

This directory owns the development environment for `HarnessUiApp`, shared by the CLI, WebUI and embedding API. It reuses the local Langfuse backend owned by `make langfuse-up`; it creates no duplicate infrastructure stack.

From the repository root:

```bash
make langfuse-up
make cli
```

`make cli`, `make webui`, and `make harness-ui-smoke` create `dev/harness-ui/.env` from its sibling `.env.example` when missing. Existing private files are never overwritten, including when the template changes. `make env-init` prepares both Harness development profiles without starting an application or infrastructure.

`make cli` and `make webui` run the workspace version with startup update detection disabled and explicit workspace-local paths:

- Configuration: `var/harness-ui/a13n-harness-ui.yaml`, with sibling Agent/Model and other resource directories.
- Runtime data: `var/harness-ui/data/`, including the database, immutable objects and Threads.

The entire `var/` directory is Git-ignored. On the first launch, if the development root YAML is missing, the launcher copies `~/.a13n-harness-ui/a13n-harness-ui.yaml`, its immediate Model/Agent/extension/MCP/Project/subagent resource files, and optional `AGENTS.md`. Existing destination files are never overwritten. Subsequent launches reuse the development copy without synchronizing changes from the original. If the user root YAML is also missing, the launcher creates a minimal root and the normal interactive launch enters setup. New users of the installed CLI do not need this bootstrap: a missing default configuration already enters setup directly.

API-key Models with `credential_ref` also need the Host-local key store. The first copy includes only `auth.json` from the user's data root (`A13N_HARNESS_UI_DATA_ROOT` when set, otherwise `~/.a13n-harness-ui/data/`) into the development data root, with private file permissions. It never overwrites an existing key store or prints key values. Environment-variable references remain unchanged: supply their values in the shell or development `.env`. Codex/Grok subscription authentication continues to use the same upstream product account stores; subscription tokens are not copied, and login, logout, or refresh can still affect those shared accounts.

Databases, conversation history, immutable objects, logs, and installed Content Plugins are not copied. Install any required Content Plugins separately in the development data root; Python extension packages must be available in the workspace environment. Configuration bytes and external paths are preserved, not rewritten or migrated by the launcher. Invalid imported configuration remains visible for repair in the development copy instead of silently falling back to setup.

Both destination paths are passed explicitly, so `A13N_HARNESS_UI_DATA_ROOT` from the shell or `.env` cannot redirect the default development runtime. The original configuration and application history are not modified. This exercises configuration compatibility and lets the retained development database follow later schema upgrades; it does **not** replay a migration from the daily-use database on every launch. Repeated migration tests require a separate disposable database snapshot, not this configuration-only bootstrap.

Forward normal options or select another environment file:

```bash
make cli CLI_ARGS='--help'
make webui
make webui WEBUI_ARGS='--port 9000 --no-share-computer'
make cli HARNESS_UI_ENV=/absolute/path/to/.env
```

`make webui` builds and installs the bundled browser assets before starting the foreground server. It passes no API key and does not skip authentication: without a CLI or `A13N_HARNESS_UI_API_KEY` environment key, the server generates a fresh key and prints a directly usable login link. A supplied key retains normal precedence and is not echoed. The default listener is `127.0.0.1:8765`, and native computer sharing is on; `WEBUI_ARGS` forwards server options such as `--host`, `--port`, and `--no-share-computer`. Ctrl+C stops the server. No browser is opened automatically.

For an intentional path override, pass `--config` and `--data-root` in `CLI_ARGS`; these follow and override the development defaults. An explicit `--config` bypasses copying and initialization entirely, even if that file is missing. Overriding only `--data-root` retains the development configuration path and selects the destination for its initial API-key copy. Overriding only `--config` leaves runtime data workspace-local, so referenced API keys must already exist there or be added explicitly. Help and version commands do not copy configuration or credentials. Do not point an unreleased build at daily-use state unless you accept that older installed versions may no longer read it.

An alternate environment file is used unchanged. If it does not exist, the launcher copies `<path>.example`; if neither exists, it stops with a setup hint. `make a13n-harness-ui` remains a separate launcher without the development `.env` or workspace-local path defaults; it uses the CLI's normal configuration/data selection and accepts `CLI_ARGS`.

Unlike the SDK development launcher, this command needs no `opentelemetry-instrument` wrapper. `open_harness_ui_app()` initializes an App-owned OTLP/HTTP tracing provider when tracing is enabled and no global Host provider exists. It passes that same provider to root and child Harness builds and drains it after App tasks finish. Explicitly supplied or preconfigured Host providers remain externally owned.

## Disposable MCP Apps demo

Run `make mcp-apps-demo` to build and launch the [stateful MCP App example](../../examples/mcp-apps/README.md) in an isolated WebUI with a scripted local HTTP model. Send `[mcp-app] Open the counter.` The server uses real stdio MCP, the bundled public App SDK and normal Host permissions; no model API key or browser automation service is required. Native computer sharing is off by default. `WEBUI_ARGS='--port 9000'` chooses another WebUI port. Ctrl+C stops the model and WebUI and removes the launcher's temporary state. The demo does not copy or alter daily-use configuration.

## Disposable first-launch experience

```bash
make cli-landing
make webui-landing
make webui-landing WEBUI_ARGS='--port 9000 --no-share-computer'
```

These interactive manual-test targets start from scratch on every invocation. They do not seed from the regular development configuration, load the development `.env`, or require Langfuse. The launcher creates a temporary HOME, workspace, and data root; the default root YAML is deliberately absent so the ordinary first-run path decides what to show. User guidance, skills, history, API-key files, and Codex/Grok account files are not copied. Shell-provided provider keys, WebUI authentication settings, and proxy settings remain available; this is disposable application state, not a sandbox or a fully empty environment.

`cli-landing` launches the normal CLI, including setup and the subsequent chat. `webui-landing` builds browser assets and starts the normal authenticated server; open its printed login link (or use your supplied key). It accepts `WEBUI_ARGS`, but neither target accepts `CLI_ARGS` or configuration/data path overrides. The temporary path is printed before startup.

Exit the CLI or press Ctrl+C in the WebUI server terminal to delete all temporary state, including credentials and configuration saved during setup. Completing setup does not immediately delete live application files, so you can continue testing a first conversation. Closing a browser tab does not stop the server or trigger cleanup. Normal exits, Python errors, and Ctrl+C unwind the temporary-directory scope; a forced kill or machine crash can leave the printed directory behind. Any real provider requests or account authorizations still have their usual external effects.

## Deterministic App smoke test

```bash
make harness-ui-smoke
```

This runs the real App, native OpenAI-compatible HTTP model path, an async Markdown child, two root turns and checkpoint storage. It reuses the scripted model fixture from `dev/fixtures`, starts it on an owned ephemeral local port, and closes it on exit. No Service process, real model credentials, Envd daemon, or model network charges are required.

Each invocation retains its own configuration and data under `var/harness-ui-smoke/<id>/`. The smoke process uses an isolated home so it does not load the developer's global instructions, skills or subscription accounts. It prints Thread/Run IDs for finding the observations in Langfuse. Expect two root traces and a separately linked child trace, not one lifetime-long Thread trace. The local `.env` enables standard content capture of these fictional inputs.

## Deployment environment

The committed template sets `OTEL_RESOURCE_ATTRIBUTES=deployment.environment.name=local`. This labels new root and child traces as `local`; it is independent of execution Environment profiles, providers, and mount names. For an existing private `.env`, merge this attribute without replacing credentials or other resource attributes, then restart the CLI/App. Exported shell values take precedence. An embedding Host must set the resource on its own provider. Existing traces are not relabeled.

## Try Logfire

The private `.env` template contains a commented working OTLP configuration for Logfire. Replace the active endpoint and headers together; insert a project write token and select its US or EU region. Then run `make cli` or `make harness-ui-smoke` unchanged. No Langfuse server or Logfire SDK is required.

For an embedded Logfire SDK Host, configure Logfire once and pass its existing providers through `HarnessInstrumentation` to `open_harness_ui_app()`. Do not also enable Pydantic auto-instrumentation. The UI neither replaces nor closes those providers.

Exported shell values take precedence over `.env` according to `uv`; clear conflicting `OTEL_*` values when switching backends. In particular, a signal-specific endpoint or header can override the common OTLP setting. Keep remote credentials only in the ignored `.env`, not its committed example. Before exporting real conversations, review `A13N_HARNESS_TRACE_CONTENT`.
