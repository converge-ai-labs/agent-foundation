# Harness UI development tracing

Use this development profile to run the CLI, WebUI, or embedded `HarnessUiApp` with checkout-local configuration and traces in Langfuse.

From the repository root:

```bash
make langfuse-up
make cli
```

`make cli`, `make webui`, and `make harness-ui-smoke` copy `dev/harness-ui/.env.example` to `.env` when missing and preserve existing files. `make env-init` prepares both Harness development profiles without starting them.

`make cli` and `make webui` run the workspace version with startup update detection disabled and explicit workspace-local paths:

- Configuration: `var/harness-ui/a13n-harness-ui.yaml`, with sibling Agent/Model and other resource directories.
- Runtime data: `var/harness-ui/data/`, including the database, immutable objects and Threads.

`var/` is Git-ignored. When the development root YAML is missing, the launcher copies the user's root YAML, immediate Model/Agent/Device/extension/MCP/Project/subagent resource files, and optional `AGENTS.md` from `~/.a13n-harness-ui/`. Existing destination files stay unchanged. Later launches reuse this copy without synchronizing it. If the user root YAML is also missing, the launcher creates a minimal root and enters normal setup.

API-key Models with `credential_ref` also need the Host-local key store. The first copy includes only `auth.json` from the user's data root (`A13N_HARNESS_UI_DATA_ROOT` when set, otherwise `~/.a13n-harness-ui/data/`) into the development data root, with private file permissions. It never overwrites an existing key store or prints key values. Environment-variable references remain unchanged: supply their values in the shell or development `.env`. The first App bootstrap imports supported external Codex/Grok logins once into the development data root. Original CLI files remain unchanged; later native login, logout, and refresh affect only that development instance. A fresh development data root performs its own adoption. Subscription groups are not copied. Recreate custom groups using accounts in the development data root before using Models that reference them.

The development instance starts with its own database, history, objects, logs, and Content Plugins. Install required Content Plugins there and Python extensions in the workspace environment. Copied configuration retains its bytes and external paths; repair invalid configuration in the development copy.

Explicit destination paths keep the default runtime checkout-local even when `A13N_HARNESS_UI_DATA_ROOT` is set. The original configuration and history stay unchanged. The retained development database follows schema upgrades; use a separate disposable database snapshot to test a migration repeatedly.

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

`open_harness_ui_app()` initializes an App-owned OTLP/HTTP tracing provider when tracing is enabled and no Host provider exists. Root and child Harness builds share it; the App drains it after its tasks finish. Supplied or preconfigured Host providers remain externally owned. No `opentelemetry-instrument` wrapper is needed.

## Disposable MCP Apps demo

Run `make mcp-apps-demo` to build and launch the [stateful MCP App example](../../examples/mcp-apps/README.md) in an isolated WebUI with a scripted local HTTP model. Send `[mcp-app] Open the counter.` The server uses real stdio MCP, the bundled public App SDK and normal Host permissions; no model API key or browser automation service is required. Native computer sharing is off by default. `WEBUI_ARGS='--port 9000'` chooses another WebUI port. Ctrl+C stops the model and WebUI and removes the launcher's temporary state. The demo does not copy or alter daily-use configuration.

## Disposable first-launch experience

```bash
make cli-landing
make webui-landing
make webui-landing WEBUI_ARGS='--port 9000 --no-share-computer'
```

These targets create a fresh temporary HOME, workspace, and data root for the ordinary first-run flow. They omit existing configuration, guidance, Skills, history, credentials, and the development `.env`; Langfuse is optional. Shell-provided provider keys, WebUI authentication, and proxy settings remain available. The temporary state does not sandbox execution.

`cli-landing` launches the normal CLI, including setup and the subsequent chat. `webui-landing` builds browser assets and starts the normal authenticated server; open its printed login link (or use your supplied key). It accepts `WEBUI_ARGS`, but neither target accepts `CLI_ARGS` or configuration/data path overrides. The temporary path is printed before startup.

After setup, test a first conversation. Exit the CLI or press Ctrl+C in the WebUI server terminal to delete the temporary state, including saved credentials; closing a browser tab leaves the server running. Normal exits and Python errors also clean up. A forced kill or machine crash can leave the printed directory behind. Real provider requests and account authorizations keep their external effects.

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

## Long-history performance benchmark

Run the synthetic comparison from the repository root:

```bash
uv run --locked python dev/harness-ui/benchmark_history.py --items 5000 50000 --repeats 3
uv run --locked python dev/harness-ui/benchmark_history.py --items 10000 --single-turn --repeats 3
```

The script uses a disposable SQLite/object store, a fixed approximately 14 KB native context, and synthetic input/answer history. The baseline embeds the entire display namespace and uses the original full fold export; the referenced path seals previous turns and captures changed items. Its version-2 manifest references physically separate native-state and display-archive objects; read and publication timings include all three objects. Both paths use the same verified object-store implementation. It asserts native-history equality and complete display reconstruction. Reported times are medians of three iterations; migration is a separate one-time measurement. These are storage/fold primitives, not end-to-end HTTP submission or model latency.

Representative Linux/Python 3.13 measurements:

| History shape                      | Operation           | Whole display | Referenced display |
| ---------------------------------- | ------------------- | ------------: | -----------------: |
| 5,000 items, two-item active tail  | Read checkpoint     |        626 ms |            1.59 ms |
|                                    | Warm capture        |        309 ms |            0.15 ms |
|                                    | Encode and publish  |      2,032 ms |            4.14 ms |
|                                    | Hot checkpoint JSON |       6.61 MB |            20.0 KB |
| 50,000 items, two-item active tail | Read checkpoint     |      8,955 ms |            2.31 ms |
|                                    | Warm capture        |      3,422 ms |            0.13 ms |
|                                    | Encode and publish  |     24,994 ms |            4.46 ms |
|                                    | Hot checkpoint JSON |      66.21 MB |            20.0 KB |
| 5,001 items in one active turn     | Read checkpoint     |        448 ms |             272 ms |
|                                    | Warm capture        |        226 ms |            1.73 ms |
|                                    | Encode and publish  |      1,306 ms |             826 ms |
|                                    | Hot checkpoint JSON |       5.98 MB |            5.98 MB |

Sealing cost was 1.27 seconds for 5,000 items and 12.08 seconds for 50,000 items. Historical segments remain retained on disk. Hot checkpoint JSON is the sum of the full uncompressed manifest, native-state and display-archive envelopes, not total retained storage. At 50,000 items these are 1,018 + 15,071 + 3,911 = 20,000 bytes. At each successful same-Run replacement, the superseded manifest and its exclusive state/archive payloads are removed; shared historical segments remain. The single-turn command removes every ordinary input except the first, leaving 5,001 items from its 10,000-item seed.

The active suffix retains the complete last turn and its input-free resumes. Its initialization, indexing, serialization, and tuple scans still grow with that turn. Warm capture avoids copying unchanged item payloads but does not make all checkpoint work constant-time. Measured collector initialization for the single-turn case increased from 434 ms to 801 ms because the collector also prepares detached reusable values. The diagnostic heartbeat excludes seed publication but includes initialization and Python garbage collection; it is not a responsiveness guarantee. The observed maximum remained about 1.04 seconds for the long single turn.

Two private, read-only copied checkpoints were also replayed offline into disposable stores. Only aggregates are reported; original content and replay files stay outside the repository. Complete items, completion-ID sets, transcript entries, and native histories were verified equal:

| Sample | Display / active items | Read, before → after | Publish, before → after | JSON, before → after | Seal once |
| ------ | ---------------------- | -------------------- | ----------------------- | -------------------- | --------- |
| A      | 21,645 / 3,319         | 3,263 → 201 ms       | 4,927 → 494 ms          | 54.44 → 7.43 MB      | 3.30 s    |
| B      | 39,774 / 690           | 5,527 → 66.0 ms      | 7,813 → 154 ms          | 72.83 → 2.34 MB      | 5.42 s    |

The referenced totals include every hot object. Sample A consists of a 5,337-byte manifest, 2,089,861-byte native state, and 5,331,276-byte display archive; sample B consists of 2,230 + 1,363,913 + 973,949 bytes. Full native state remains a snapshot rather than a message delta.

WebUI regression tests check that 100 typing-and-acknowledgement cycles cause no history-subscriber renders, while local-input and submission transitions still render. Input navigation requests one page until Load more is activated. Complete turn caches survive checkpoint changes only when the server's full-entry content revision is identical; changed revisions are fetched again.

## Live subscription memory benchmark

Run each sample in a fresh Linux process with the checkout's locked dependencies:

```bash
uv run --locked python dev/harness-ui/benchmark_live_memory.py --watchers 8 --events 128 --trace
uv run --locked python dev/harness-ui/benchmark_live_memory.py --watchers 8 --events 128
```

The benchmark queues nested child events, drains each subscriber, and verifies event order. Compare `--watchers 0`, `1`, and `8` to measure fan-out costs.

- `queued_heap_bytes` and `closed_heap_bytes`: traced Python heap before draining and after closing the hub.
- `publish_ms` and `drain_ms`: publication and delivery latency; measure with tracing disabled.
- `max_rss_kib`: Linux process RSS high-water, including imports and allocator retention.

Use separate traced and untraced runs, and repeat latency samples across fresh processes.
