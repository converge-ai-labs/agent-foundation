# Provider smoke checks

Manual checks of Model and Connector modules against external providers, without starting Foundation Service, PostgreSQL, or Redis. These are development diagnostics, not service entry points or automated CI tests. Scripts, helpers, and their purpose stay together in this directory.

| Entry point        | Purpose                                                                                       | Credential environment variable |
| ------------------ | --------------------------------------------------------------------------------------------- | ------------------------------- |
| `openrouter.sh`    | Discover models, inspect settings, and run inference                                          | `OPENROUTER_API_KEY`            |
| `composio.sh`      | Browse toolkits and tools, start hosted authorization, and try a bound tool                   | `COMPOSIO_API_KEY`              |
| `openconnector.sh` | Browse OOMOL providers and Actions, verify a console-authorized connection, and try an Action | `OPENCONNECTOR_API_KEY`         |

Start one interactive walkthrough from the repository root:

```bash
bash scripts/provider-smoke/openrouter.sh
bash scripts/provider-smoke/composio.sh
bash scripts/provider-smoke/openconnector.sh
```

Each script accepts `--help` for individual commands. Missing keys are requested through hidden input; `.env` files are not loaded. The Bash entry points also work from another directory when invoked by absolute path. Keys are never written to files by these scripts.

## OOMOL OpenConnector

This integration targets [oomol-lab/open-connector](https://github.com/oomol-lab/open-connector). The hosted endpoint is `https://connector.oomol.com`; requests use `Authorization: Bearer` with your personal OOMOL API key. A self-hosted deployment uses its runtime token and `--deployment self_hosted --endpoint https://your-runtime.example`. Private hosts also require `--allow-private-domain HOST`.

Run these commands separately to see discovery before authorization:

```bash
bash scripts/provider-smoke/openconnector.sh discover
bash scripts/provider-smoke/openconnector.sh tools --connector github
bash scripts/provider-smoke/openconnector.sh describe --connector github --tool github.get_current_user
bash scripts/provider-smoke/openconnector.sh authorize --connector github
```

`discover` reads the complete provider array. `tools` and `describe` read Action definitions without inspecting or authorizing accounts. `authorize` previews Actions, then asks you to connect the account in the OOMOL or self-hosted console and return to refresh its visible connections. It does not create an OAuth API session or collect third-party credentials. Choose the exact connection ID printed after authorization. The walkthrough accepts an existing connection and prompts for `CALL` before execution.

For a subsequent explicit call, supply the saved connection ID:

```bash
bash scripts/provider-smoke/openconnector.sh call --connector github \
  --connection-id YOUR_CONNECTION_ID --tool github.get_current_user \
  --arguments '{}' --execute
```

OOMOL personal/runtime APIs execute against a connection alias. The client rechecks the selected ID, alias/default mapping, active status, and Action definition before dispatch. Aliases and definitions remain mutable upstream; these checks do not provide an atomic version pin. OOMOL project keys and `/v1/saas` end-user APIs are a separate contract. Personal/runtime connections cannot attest Foundation owner correlations and are not registered as Foundation Connector Providers.

## Composio and OpenRouter

Composio previews tools before account authorization. Hosted OAuth requires an existing auth configuration, your browser callback URL, and `AUTHORIZE` confirmation. After completing authorization in the browser, return to verify the account and its exact provider user ID. The script rechecks ownership, readiness, and the pinned tool definition before a call. Tool execution requires `CALL` or an explicit `--execute`.

OpenRouter `discover` lists lightweight candidates; `describe --model vendor/model` uses the single-model description operation to show its settings schema without enumerating the complete catalog first. `call --model vendor/model` validates the native endpoint and invokes the model without discovery. Inference consumes quota. Neither connector script retries calls automatically or revokes accounts on exit.

## Diagnostics and local checks

Connector HTTP failures print the status, request method, and path before the module error. HTTP 401 means authentication was rejected, HTTP 403 indicates access denial, and HTTP 404 indicates a missing route or resource. Response bodies, headers, and query strings are omitted from diagnostics.

Each provider has its own Python entry point. `common.py` shares hidden key input, error reporting, and interactive helpers. Automated checks live under `scripts/tests/provider_smoke/` and make no live provider calls:

```bash
uv run --locked python -m pytest scripts/tests/provider_smoke -q
```
