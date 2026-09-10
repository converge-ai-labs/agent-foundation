# Harness SDK development tracing

This directory owns the explicit development environment for embedded Harness
SDK scenarios. It does not start Service or Harness UI. The existing
[observation scenarios](../observation-demo/README.md) exercise summarize,
compaction, image understanding and inline delegation with scripted models.

From the repository root:

```bash
cp dev/harness/.env.example dev/harness/.env # only when the private file does not exist
make langfuse-up
make harness-dev
make harness-dev HARNESS_ARGS=summary
```

The example environment contains only public local-test credentials, matching
`dev/service/local.toml`. Private `.env` files are ignored by Git. Change the
endpoint and headers together if the local Langfuse project is customized.
`make harness-dev` uses the official `opentelemetry-instrument` launcher to
configure the SDK provider before the scenario runs. The Harness library itself
never loads dotenv, initializes exporters or changes global providers.

These trace-validation scenarios explicitly enable structural tracing and use
`A13N_HARNESS_TRACE_CONTENT` to select captured content. They print trace IDs and,
for Langfuse endpoints, local trace links. To test an ordinary SDK application
with the environment-selected on/off policy instead:

```bash
uv run --locked --env-file dev/harness/.env opentelemetry-instrument python your_agent.py
```

## Try Logfire

In your private `.env`, replace the active OTLP endpoint and headers with the
commented Logfire values and your project write token. Use the matching US or EU
region. Run the same Make target; no local Langfuse server or Logfire SDK is
needed. This uses Logfire's standard OTLP ingestion rather than adding a second
Pydantic instrumentor. Do not use Langfuse Basic credentials with Logfire.

The environment file is loaded explicitly; exported shell values take precedence
according to `uv` dotenv behavior. Clear conflicting `OTEL_*` values, especially
signal-specific endpoints/headers, before testing another backend. For an
alternate private file, use `HARNESS_ENV=/absolute/path/to/.env`.

`standard` captures model/tool content. Review the information boundary before
exporting anything other than these fictional inputs. `none` omits normal
execution payloads but does not scrub all upstream exceptions or tool schemas.
