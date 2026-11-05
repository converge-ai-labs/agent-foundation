---
title: Run and restart WebUI
description: Configure instance access and reverse proxies, update the server, and check recovery and shutdown.
---

Use this guide when operating a Harness UI instance for yourself or trusted collaborators. First [start WebUI and verify a conversation](webui.md#start-the-server). Instance access shares workbench authority; use [Service](../a13n-service/index.md) for managed users and durable execution across workers.

## Authentication and key retention

Startup prints the ordinary address. With a generated key, it also prints the key and fragment login link. The browser removes the fragment and retains a successfully used key in same-origin localStorage for authenticated API requests. **Log out** removes it and closes protected views. Static assets contain no key.

Key precedence is `--apikey`, `A13N_HARNESS_UI_API_KEY`, then a fresh process key. Supplied keys are not echoed, though command-line arguments can be visible to the OS. Empty keys and conflicting repeated values are rejected.

`--dangerous-skip-permissions` disables Web authentication only; Agent permissions and sharing gates still apply. It cannot be combined with a supplied key. `--api-key` and `--dangerously-bypass-permission` remain compatibility aliases. Review [CLI options](command-reference.md#webui) before exposing the listener.

## Reverse proxies and public addresses

1. Add your external address to the selected root YAML. Replace the example with your actual scheme, host, and port:

   ```yaml
   webui:
     allowed_origins:
       - "https://workbench.example.com:8090"
   ```

2. Configure TLS termination and WebSocket upgrades. Preserve the external `Host` (including port) and browser `Origin`; forward the HTTPS scheme as `X-Forwarded-Proto: https`.

3. If the proxy peer is not trusted loopback `127.0.0.1`, set `FORWARDED_ALLOW_IPS` in the WebUI process environment to the proxy IPs/networks as seen by that process/container. Have the proxy overwrite client-supplied forwarding headers.

4. Restart WebUI, open the external address, log in, and check a conversation and interactive connection.

Origin admission matches scheme, domain, and port. The example does not also allow HTTP or port 443. Default `[]` keeps listener-address restrictions; `allowed_origins: ["*"]` permits any address. Neither disables authentication or enables cross-origin API access. Trust only actual proxy peers; do not rewrite headers to loopback or use unrestricted forwarded-header trust to evade admission. See [allowed origins](configuration.md#webui-allowed-origins).

Device pairing returns root-relative paths resolved against the paired Host URL, retaining public HTTPS and port. Approval alone does not establish Device connection or desktop permission.

## Restart or update the server

1. Save private editor changes.
2. Press Ctrl+C once or send SIGTERM. Wait for **WebUI stopped** and process exit.
3. For an update, install the new package after exit using the [installation workflow](installation.md).
4. Start the replacement with the same data directory. Inspect interrupted conversations and any recovery errors.

Graceful shutdown saves root/child checkpoints, finalizes Environments, and records a restart handoff. The next start consumes it once and continues compatible interrupted work in new Runs, without resending prompts or replaying the saved tool batch.

`shutdown_timeout_seconds` (default 60 seconds) bounds waiting for a safe boundary, not total process exit. If a batch cannot finish, or checkpoint/finalization fails, that batch is not armed for automatic recovery. Cancellation and cleanup still take time. Let the old process finish before starting its replacement.

After forced termination or failed reconstruction, inspect saved history/logs before retrying uncertain work. Pending decisions remain unanswered. Native terminals, old Shell handles, shared drafts, and unsaved editors are not restored.

## Server logs and shutdown

Use `process.log_level` and `process.log_format` to control logs. INFO reports normal API activity; DEBUG adds assets, health probes, and cleanup stages. API entries record route templates, status, elapsed time, codes, and generated IDs, excluding bodies, keys, query values, and native paths.

| Observation                                                            | Action                                                                                   |
| ---------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| **Stopping WebUI**                                                     | Wait for cleanup; **WebUI stopped** confirms completion                                  |
| Slow shutdown                                                          | Inspect DEBUG stages; connection-drain timeout is not an App cleanup deadline            |
| **Connection interrupted**                                             | Let reconnect run or choose **Retry now**; restart the server first if stopped           |
| `thread_history_continuation_changed` / `thread_continuation_conflict` | Refresh the conversation                                                                 |
| `object_payload_incompatible`                                          | Inspect object/version/validation details in storage warnings; retain the data directory |

Reconnect refreshes observations, not failed saves or submissions. Browser disconnection does not stop Runs or terminals. WARNING log level hides normal progress.

## Listener and application lifetime

The running server owns the App even without browser clients. Non-loopback listening shares instance authority with a trusted network; use TLS and suitable access controls. It does not provide tenant isolation. Unauthenticated `/healthz` and `/readyz` expose bounded liveness/readiness; readiness can mean ready for setup before a Model exists.

Compatible TUI/WebUI processes can use one local database across package upgrades. An older compatible reader preserves unknown migration history and checks its required tables/columns; a newer migration number alone does not reject it or block an active save. Real storage, continuation/version, and payload incompatibilities still fail. Database compatibility does not share live execution ownership, and released binaries retain their own checks.

## Container and installed assets

Install the Python distribution on the workbench Host. Its wheel/sdist includes WebUI assets, so end users need no Node.js or frontend checkout. Harness UI publishes no official container image; a custom deployment must pin the distribution and configure listener, authentication, and persistent mounts. `a13n-sandbox` is an Agent execution image, not the WebUI server.

For repository development, `make webui` builds/installs assets and starts a foreground server with isolated config/data in `var/harness-ui/`. It prints a generated-key login link when no key was supplied. Use `WEBUI_ARGS` for server options and `CLI_ARGS` for global options:

```bash
make webui WEBUI_ARGS='--port 9000 --no-share-computer'
```

See the [development guide](https://github.com/converge-ai-labs/agent-foundation/blob/main/dev/harness-ui/README.md) for seeding and environment overrides.

## Options and ownership

Listener address, port, authentication, and native sharing are process options. Additional admitted origins live in root YAML. Use the [command reference](command-reference.md#webui) for the complete option list.

For your own client, continue with the [HTTP workflow](http-api.md). For in-process integration, use the [Python App](embedding.md). For scripts that need no listener, use [one-shot execution](automation-and-troubleshooting.md#automation-and-diagnostics).
