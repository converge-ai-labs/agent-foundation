# Browser server

Harness UI's HTTP server shares the local `HarnessUiApp` with the terminal product. Its bundled browser page is currently an authentication/status foundation, **not** the Service Console or a completed browser chat workbench. Do not infer browser controls from backend API availability.

## Start the server

The bundled foundation page accepts the instance API key and displays the installed Python package version returned by the server. It does not yet provide conversation, setup, shared drafts, Host Files, Git, or terminal controls; those browser controls remain unavailable. The HTTP API is independent: native Host Files is available with explicit computer sharing, while Git, terminal, and shared drafts remain unavailable.

```bash
a13n-harness-ui webui                       # 127.0.0.1:8765, generated per-process API key
a13n-harness-ui webui --host 127.0.0.1 --port 9000
a13n-harness-ui webui --share-computer      # Native Files API as the server OS account
```

`--share-computer` enables native file browsing, editing, transfer, creation, move, deletion, and captured Thread input through the [Files API](http-api.md#native-host-files). It does not enable Git, PTY, shared drafts, or browser workbench panels in this release. Paths refer to the server account or container mounts, regardless of the Agent's Environment. Project roots are navigation starts, not filesystem confinement. Omit the option to keep native operations unavailable even when authentication is bypassed.

## Authentication and key retention

Startup stdout prints the ordinary URL and, only for a generated key, the key and a convenience fragment URL. The browser consumes and removes the key fragment, sends `Authorization: Bearer <key>` on API requests, and retains successfully used keys in same-origin localStorage. Use **Forget API key** to remove that retention. Static assets contain no key and need no authentication.

Key precedence is `--apikey`, then `A13N_HARNESS_UI_API_KEY`, then a fresh process key. Supplied keys are not echoed; command arguments may still be visible to the shell and operating system. Explicitly empty keys and conflicting repeated key values are rejected. `--dangerous-skip-permissions` disables Web authentication only, not Agent permissions or computer-sharing gates; combining it with a CLI or environment key is an error. `--api-key` and `--dangerously-bypass-permission` remain compatibility aliases.

## Listener and application lifetime

Non-loopback listening grants shared instance authority on a trusted network, not tenant isolation; use external TLS when needed. The server owns the App lifetime even without browsers; Ctrl+C or SIGTERM closes it. Unauthenticated `/healthz` and `/readyz` report bounded liveness and App readiness. A fresh instance can be ready for setup before any model is configured.

## Container and installed assets

The GHCR image is `ghcr.io/converge-ai-labs/a13n-harness-ui`: `dev` follows main, releases use `X.Y.Z`, and RCs use `X.Y.Z-rc.N` without advancing `latest`. Python and the page display RC metadata as `X.Y.ZrcN`. Development builds display source version `0.0.0` with a separate Git revision. For persistent configuration, data, and work mounts with loopback-only port publishing, use the repository's `deploy/compose/a13n-harness-ui.yaml`. The image runs as UID/GID `10001:10001`; bind mounts must be writable by that account. Do not remove its volumes when preserving data. Restart rotates generated keys; supply the API-key environment variable at runtime when a stable key is needed.

The browser assets ship inside the wheel. End users do not need Node.js or a separate frontend checkout. For repository development, run `make a13n-harness-ui-assets` before `uv run --locked a13n-harness-ui webui`.

## Options and ownership

See [the registered webui options](command-reference.md#webui) for listener, authentication, and compatibility aliases. Listener settings are process arguments rather than fields in root YAML. An open server owns active App work; closing a tab does not stop the server, and this is not a detached worker service.

For API clients, follow [the HTTP workflow and route reference](http-api.md). For an in-process interface, use [the Python App](embedding.md). For automation without a browser server, use [one-shot execution](automation-and-troubleshooting.md#automation-and-diagnostics).
