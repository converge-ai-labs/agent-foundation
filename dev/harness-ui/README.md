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

Run the synthetic fan-out benchmark in fresh Linux processes with the checkout's locked environment:

```bash
for watchers in 0 1 8; do
  uv run --locked python dev/harness-ui/benchmark_live_memory.py --watchers "$watchers" --events 128 --trace
  for repeat in 1 2 3; do
    uv run --locked python dev/harness-ui/benchmark_live_memory.py --watchers "$watchers" --events 128
  done
done
```

Each run publishes 128 child events containing 128 nested rows apiece, then drains every subscriber and verifies event order. The ring and subscriber buffers hold the complete workload. `queued_heap_bytes` measures the traced Python heap after publication and garbage collection, before draining. Use the separate untraced runs for latency; `max_rss_kib` is Linux process RSS high-water, including imports and allocator retention.

Representative Linux/Python 3.13.7 measurements against baseline `4f0bdae9c`, using the same script and locked dependencies. Heap figures are one traced run; timing figures are medians of three fresh untraced runs:

| Watchers | Queued heap, before → after | Publish, before → after | Drain, before → after |
| -------- | --------------------------- | ----------------------- | --------------------- |
| 0        | 7.73 → 7.66 MiB             | 30.50 → 31.56 ms        | 0.00 → 0.00 ms        |
| 1        | 10.87 → 7.67 MiB            | 65.37 → 31.63 ms        | 31.29 → 32.50 ms      |
| 8        | 33.35 → 7.70 MiB            | 303.40 → 33.51 ms       | 266.84 → 276.63 ms    |

Queues now share the hub's private detached event; each consumer still receives an independent copy. This removes the per-subscriber queued copy and its publication cost while preserving mutation isolation. Drain time remains comparable; the zero-watcher difference is within the observed run-to-run spread.

Focused App subscriptions also transfer their one-time replay to the consumer, and WebSocket/TUI consumers release it after bootstrap. A separate isolated App probe seeded 512 text rows of about 8 KiB, opened eight watches, discarded their bootstrap values, and kept their subscriptions open. With tracing started after seeding, additional retained heap fell from 11.39 MiB to 0.09 MiB; every watch received the same 513 replay events. Regression tests in `test_app.py`, `test_live.py`, and `test_execution_protocol.py` verify collection with weak references, preserved compact replay refresh, and continued delivery over a real loopback WebSocket.

These measurements isolate live fan-out and bootstrap retention. Whole-process long-running memory additionally includes native history, retained tool results, imports, and allocator high-water; the benchmark does not estimate their production footprint.

## Sequential-turn memory comparison

A disposable real-App probe compared the subscription-only revision `9f65d8630` with the checkpoint lifetime fixes. On Linux/Python 3.13.7 with the same locked dependencies, it submitted 20 sequential `FunctionModel` turns, each producing 256 KiB in 8 KiB chunks (5 MiB total). It used fresh HOME/configuration/data roots, disabled tracing exporters and pricing updates, and kept the App open. Zero or eight watches joined halfway through the first response, consumed bootstrap, and continued receiving events. Each sample followed operation settlement, a 20 ms event-loop yield, and garbage collection. RSS came from `/proc/self/smaps_rollup`; process high-water came from `getrusage`.

Three fresh untraced processes per case gave these medians. Growth is calculated against each process's own idle sample before taking the median:

| Workload                 |   Idle RSS | RSS after turn 20 | RSS growth | Process RSS high-water |
| ------------------------ | ---------: | ----------------: | ---------: | ---------------------: |
| UI before, no watches    | 264.29 MiB |        571.96 MiB | 307.67 MiB |             596.75 MiB |
| UI after, no watches     | 264.20 MiB |        358.62 MiB |  94.35 MiB |             441.16 MiB |
| UI before, eight watches | 264.21 MiB |        577.95 MiB | 313.70 MiB |             602.77 MiB |
| UI after, eight watches  | 264.21 MiB |        374.59 MiB | 110.38 MiB |             442.86 MiB |

Every watch received 913 events in every repetition. Final RSS ranges were 519.95–592.84 MiB before and 347.62–358.65 MiB after without watches, and 569.76–582.30 MiB before and 370.05–376.61 MiB after with eight watches.

A Harness-only reference used the same prompts, output chunks, and sample boundaries through `HarnessBuilder` with embedded bindings, carrying only the current `HarnessState` between turns. Across three fresh untraced processes its median idle/final RSS was 167.69/256.46 MiB, with 88.77 MiB growth. UI growth without watches is now close to that reference; its roughly 96 MiB higher idle baseline and checkpoint peaks remain distinct costs. The UI also owns configuration, pricing, compact display, and durable checkpoints, whereas the Harness reference retains its current approximately 5 MiB state in memory.

Separate traced runs started after App/reference initialization:

| Workload                                  | Live Python heap after turn 20 | Peak Python heap |
| ----------------------------------------- | -----------------------------: | ---------------: |
| UI before, no watches                     |                     176.40 MiB |       298.04 MiB |
| UI with checkpoint-lock fix only          |                      16.79 MiB |       161.89 MiB |
| UI with lock and buffer-lifetime fixes    |                      15.90 MiB |       131.96 MiB |
| Harness reference, current state retained |                       6.16 MiB |        81.19 MiB |

The lock's cancelled 30-second acquisition timer captured native Run context and could temporarily retain completed streams and histories. The existing nonblocking lock poll now checks a monotonic deadline without creating that long-lived timer. Object publication drops completed encoding buffers before staged/published verification, encodes the validated payload without another container-tree dump, and retains only the native object reference while publishing display. Both payload codecs keep their exact canonical bytes and integrity checks.

The ContextVar/weak-reference regression fails against the old lock and passes against the fix. Focused tests cover contention deadlines, cancellation, selected-reader exclusion, terminal checkpoint selection, and both codecs' byte identity and mapping order. Diagnostic probes remain outside product code. These synthetic runs measure short-term working-set amplification; an earlier 35-second idle control also released the old timer-held histories. They do not establish the cause of a separate production process's multi-GiB footprint.
