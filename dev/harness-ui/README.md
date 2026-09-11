# Harness UI development tracing

This directory owns the development environment for `HarnessUiApp`, shared by the CLI, WebUI and embedding API. It reuses the local Langfuse backend owned by `make langfuse-up`; it creates no duplicate infrastructure stack.

From the repository root:

```bash
make langfuse-up
make cli
```

`make cli` and `make harness-ui-smoke` create `dev/harness-ui/.env` from its sibling `.env.example` when missing. Existing private files are never overwritten, including when the template changes. `make env-init` prepares both Harness development profiles without starting an application or infrastructure.

`make cli` runs the workspace version with startup update detection disabled and explicit workspace-local paths:

- Configuration: `var/harness-ui/a13n-harness-ui.yaml`, with sibling Agent/Model and other resource directories.
- Runtime data: `var/harness-ui/data/`, including the database, immutable objects and Threads.

The entire `var/` directory is Git-ignored. The first launch starts a separate setup; subsequent launches reuse this development state. No existing configuration or history is copied, migrated or deleted. This keeps unreleased schema changes away from the installed CLI's normal `~/.a13n-harness-ui/` configuration and data. Both paths are passed explicitly, so `A13N_HARNESS_UI_DATA_ROOT` from the shell or `.env` cannot redirect the default development launch.

Forward normal options or select another environment file:

```bash
make cli CLI_ARGS='--help'
make cli CLI_ARGS='webui'
make cli HARNESS_UI_ENV=/absolute/path/to/.env
```

For an intentional path override, pass `--config` and `--data-root` in `CLI_ARGS`; these follow and override the development defaults. Overriding only one leaves the other workspace-local. Do not point an unreleased build at daily-use state unless you accept that older installed versions may no longer read it.

An alternate environment file is used unchanged. If it does not exist, the launcher copies `<path>.example`; if neither exists, it stops with a setup hint. `make a13n-harness-ui` remains a separate launcher without the development `.env` or workspace-local path defaults; it uses the CLI's normal configuration/data selection and accepts `CLI_ARGS`.

Unlike the SDK development launcher, this command needs no `opentelemetry-instrument` wrapper. `open_harness_ui_app()` initializes an App-owned OTLP/HTTP tracing provider when tracing is enabled and no global Host provider exists. It passes that same provider to root and child Harness builds and drains it after App tasks finish. Explicitly supplied or preconfigured Host providers remain externally owned.

## Deterministic App smoke test

```bash
make harness-ui-smoke
```

This runs the real App, native OpenAI-compatible HTTP model path, an async Markdown child, two root turns and checkpoint storage. It reuses the scripted model fixture from `dev/service`, starts it on an owned ephemeral local port, and closes it on exit. No Service process, real model credentials, Envd daemon, or model network charges are required.

Each invocation retains its own configuration and data under `var/harness-ui-smoke/<id>/`. The smoke process uses an isolated home so it does not load the developer's global instructions, skills or subscription accounts. It prints Thread/Run IDs for finding the observations in Langfuse. Expect two root traces and a separately linked child trace, not one lifetime-long Thread trace. The local `.env` enables standard content capture of these fictional inputs.

## Deployment environment

The committed template sets `OTEL_RESOURCE_ATTRIBUTES=deployment.environment.name=local`. This labels new root and child traces as `local`; it is independent of execution Environment profiles, providers, and mount names. For an existing private `.env`, merge this attribute without replacing credentials or other resource attributes, then restart the CLI/App. Exported shell values take precedence. An embedding Host must set the resource on its own provider. Existing traces are not relabeled.

## Try Logfire

The private `.env` template contains a commented working OTLP configuration for Logfire. Replace the active endpoint and headers together; insert a project write token and select its US or EU region. Then run `make cli` or `make harness-ui-smoke` unchanged. No Langfuse server or Logfire SDK is required.

For an embedded Logfire SDK Host, configure Logfire once and pass its existing providers through `HarnessInstrumentation` to `open_harness_ui_app()`. Do not also enable Pydantic auto-instrumentation. The UI neither replaces nor closes those providers.

Exported shell values take precedence over `.env` according to `uv`; clear conflicting `OTEL_*` values when switching backends. In particular, a signal-specific endpoint or header can override the common OTLP setting. Keep remote credentials only in the ignored `.env`, not its committed example. Before exporting real conversations, review `A13N_HARNESS_TRACE_CONTENT`.
