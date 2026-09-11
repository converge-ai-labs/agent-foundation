# Provider smoke checks

Manual checks of Model and Connector modules against external providers, without starting a13n Service, PostgreSQL, or Redis. These are development diagnostics, not service entry points or automated CI tests. Scripts, helpers, and their purpose stay together in this directory.

| Entry point     | Purpose                                                                     | Credential environment variable |
| --------------- | --------------------------------------------------------------------------- | ------------------------------- |
| `openrouter.sh` | Discover models, inspect settings, and run inference                        | `OPENROUTER_API_KEY`            |
| `composio.sh`   | Browse toolkits and tools, start hosted authorization, and try a bound tool | `COMPOSIO_API_KEY`              |

Start one interactive walkthrough from the repository root:

```bash
bash scripts/provider-smoke/openrouter.sh
bash scripts/provider-smoke/composio.sh
```

Each script accepts `--help` for individual commands. Missing keys are requested through hidden input; `.env` files are not loaded. The Bash entry points also work from another directory when invoked by absolute path. Keys are never written to files by these scripts.

## Composio and OpenRouter

Composio previews tools before account authorization. Hosted OAuth requires an existing auth configuration, your browser callback URL, and `AUTHORIZE` confirmation. After completing authorization in the browser, return to verify the account and its exact provider user ID. The script rechecks ownership, readiness, and the pinned tool definition before a call. Tool execution requires `CALL` or an explicit `--execute`.

OpenRouter `discover` lists lightweight candidates; `describe --model vendor/model` uses the single-model description operation to show its settings schema without enumerating the complete catalog first. `call --model vendor/model` validates the native endpoint and invokes the model without discovery. Inference consumes quota. The Composio script neither retries calls automatically nor revokes accounts on exit.

## Diagnostics and local checks

Connector HTTP failures print the status, request method, and path before the module error. HTTP 401 means authentication was rejected, HTTP 403 indicates access denial, and HTTP 404 indicates a missing route or resource. Response bodies, headers, and query strings are omitted from diagnostics.

Each provider has its own Python entry point. `common.py` shares hidden key input, error reporting, and interactive helpers. Automated checks live under `scripts/tests/provider_smoke/` and make no live provider calls:

```bash
uv run --locked python -m pytest scripts/tests/provider_smoke -q
```
