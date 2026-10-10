# Harness SDK development tracing

This directory owns the explicit development environment for embedded Harness SDK scenarios. It does not start Service or Harness UI. The existing [observation scenarios](../observation-demo/README.md) exercise summarize, compaction, image understanding and inline delegation with scripted models.

From the repository root:

```bash
make langfuse-up
make harness-dev
make harness-dev HARNESS_ARGS=summary
```

`make harness-dev` creates `dev/harness/.env` from its sibling `.env.example` when missing, without overwriting an existing private file even when the template changes. Use `make env-init` to prepare both Harness development profiles without starting an application or infrastructure.

The example environment contains only public local-test credentials, matching `dev/observability/langfuse.py`. Private `.env` files are ignored by Git. Change the endpoint and headers together if the local Langfuse project is customized. `make harness-dev` uses the official `opentelemetry-instrument` launcher to configure the SDK provider before the scenario runs. The Harness library itself never loads dotenv, initializes exporters or changes global providers.

These trace-validation scenarios explicitly enable structural tracing and use `A13N_HARNESS_TRACE_CONTENT` to select captured content. They print trace IDs and, for Langfuse endpoints, local trace links. To test an ordinary SDK application with the environment-selected on/off policy instead:

```bash
uv run --locked --env-file dev/harness/.env opentelemetry-instrument python your_agent.py
```

## Deployment environment

The committed template sets `OTEL_RESOURCE_ATTRIBUTES=deployment.environment.name=local`. This labels new traces as `local` in Langfuse and other OTLP backends; it does not select the Harness execution Environment. If a private `.env` already exists, merge this setting into its resource attributes without replacing the file or other attributes, then restart the launcher. Exported shell values take precedence. Previously exported traces keep their original environment label.

## Try Logfire

In your private `.env`, replace the active OTLP endpoint and headers with the commented Logfire values and your project write token. Use the matching US or EU region. Run the same Make target; no local Langfuse server or Logfire SDK is needed. This uses Logfire's standard OTLP ingestion rather than adding a second Pydantic instrumentor. Do not use Langfuse Basic credentials with Logfire.

The environment file is loaded explicitly; exported shell values take precedence according to `uv` dotenv behavior. Clear conflicting `OTEL_*` values, especially signal-specific endpoints/headers, before testing another backend. For an alternate private file, use `HARNESS_ENV=/absolute/path/to/.env`. A missing alternate file is initialized only from its own sibling `<path>.example`; if neither exists, the launcher stops with a setup hint.

`standard` captures model/tool content. Review the information boundary before exporting anything other than these fictional inputs. `none` omits normal execution payloads but does not scrub all upstream exceptions or tool schemas.

## Native Live smoke test

With `OPENAI_API_KEY` exported, run one real-provider exchange against a realtime model available to your account:

```bash
uv run --locked python dev/harness/live_smoke.py --model YOUR_REALTIME_MODEL
```

The test invokes a harmless `double` tool, consumes native output audio separately from semantic events, closes after the exchange, requires exactly one completed post-cleanup result, and round-trips portable state. It has a 60-second deadline. It prints transcripts and audio byte counts; it does not open a microphone, play sound, save credentials, or claim complete session billing. Provider access is required and usage can incur charges. A missing key stops before connecting; `--help` needs no credentials. Deterministic integration coverage lives in `packages/a13n-harness/tests/test_live.py` and does not substitute for this real-provider check.
