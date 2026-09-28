# Interactive MCP Apps

MCP Apps display interactive tool results inside a Harness UI conversation. An App can keep working after the assistant finishes, using its MCP server's existing connection. This feature belongs to WebUI; it does not install a browser-control service or give the Agent access to your browser.

## Enable Apps for selected servers

First [configure and select an MCP server](mcp.md). Then add its exact resource ID to the root YAML:

```yaml
webui:
  mcp_apps:
    enabled: true
    servers: [mcp-counter]
```

Apps are disabled by default. This allowlist does not select an MCP server for an Agent: the Agent must already include it in `mcp_servers`. Restart WebUI after enabling Apps or changing the sandbox listener. Ordinary MCP tools continue to work without Apps support, and ordinary text results remain available when a presentation fails.

The server must provide the MCP Apps `ui.resourceUri` metadata and a `text/html;profile=mcp-app` resource. Legacy MCP-UI HTML conventions are not an alternative supported protocol. The initial Host supports inline display, same-server tools and resources, text/structured context, text messages, external links and theme changes. Image context, App-provided model tools, fullscreen mode and stable-origin-dependent browser storage are not supported.

For a complete local example without a model account, run `make mcp-apps-demo` from a source checkout. See `examples/mcp-apps/README.md` in the repository for the real stdio counter, public App SDK bundle and standalone server instructions. The demo uses a scripted HTTP model but the normal Host, history and permission paths.

## Open and interact

A live App appears beside its tool result. Saved history shows **Open App** instead of running HTML automatically. Opening displays the retained original presentation; it does not repeat the tool call or connect to its server.

Choose **Activate interactions** to allow the App to request same-server operations. Activation and subsequent requests check current Agent selection, tool visibility and permission rules, rather than granting the old Run's permissions indefinitely. A child App retains its source identity and current delegation route; it cannot use a removed child route as authority.

App tool operations do not use model review or custom Agent reviewers. A `review` permission permits App dispatch without a model request; explicit `deny` still blocks it and `ask` still requires your approval. Agent-originated calls retain their normal review policy, including calls made after you confirm an App message.

- Tool approvals appear in the trusted Host card, outside the App iframe. Review the server, tool and exact arguments before approving. A later policy or credential change can invalidate a pending approval.
- **Check result** reconciles an uncertain operation by reading its existing receipt. It does not repeat the call or promise that an unconfirmed write had no effect.
- Closing a View ends its controls and pending confirmations, not the MCP server's connection. Already dispatched operations may still finish.
- Connections have no idle timeout. A completed Run or a disconnected browser does not discard server state. Host shutdown, explicit connection closure, binding retirement or Thread disposal ends the connection. A crashed server is not silently restarted and its business calls are not replayed.

Follow-up results belong to the open View; they do not replace the saved original. A page reload or later visit restores the original result and HTML, not the previous DOM or follow-up UI state. After reactivation, an App can explicitly read current server state. Restarting the Host cannot recover an MCP server's private in-memory state.

## Context is opt-in

An App can offer its latest text or structured value as context. Inspect it and select it in the Host card before submitting an ordinary composer message. Unselected context is not attached. Selection is local to mounted Apps in that browser conversation, not another tab or the shared draft.

Send captures the exact selected values. Later App updates cannot rewrite an in-flight submission. Replaced, discarded or missing selections fail visibly rather than silently substituting newer context. Each value is limited to 64 KiB and one submission may select at most eight Views. Context is source-attributed external data, not Host instructions, and is not implicitly attached to steering.

An App message is a separate proposal. Review its text and destination, then choose **Send once** or **Decline** outside the iframe. A child App proposes a handoff to its owning root conversation. If the root is busy, the message fails instead of being queued or converted to steering. Sending does not replace your composer draft. After an uncertain response, check the receipt rather than sending the same proposal again.

## External links

An App may propose an absolute HTTP or HTTPS URL. The Host displays the normalized destination and requires a separate click before opening a new tab without an opener. Credential-bearing URLs and links to the Host or sandbox origin are rejected. App links cannot navigate the workbench itself. Opening an external link does not require an active MCP connection.

## Local, Docker and reverse-proxy origins

Apps run inside an opaque iframe nested in a separate-origin sandbox proxy. The proxy has no authenticated Host API or login credential. Do not proxy it under the WebUI origin or forward Host authentication headers/cookies to it.

### Same-machine development

Defaults bind the sandbox to `127.0.0.1` on a free ephemeral port. This works when the browser and WebUI are on the same machine. The generated sandbox URL uses that loopback address. It is not reachable from a browser on another device.

### Container or remote WebUI

Set a fixed listener and the separate public origin reachable by the browser. For example, with WebUI exposed as `https://chat.example.com`:

```yaml
webui:
  mcp_apps:
    enabled: true
    servers: [mcp-counter]
    sandbox:
      bind: 0.0.0.0
      port: 8766
      public_url: https://apps.example.net
```

Publish port 8766 to the reverse proxy's network and route `https://apps.example.net/sandbox.html` to that sandbox listener. Preserve the query string and response security headers. Use HTTPS for both public origins; do not embed an HTTP sandbox in an HTTPS workbench. `public_url` is an origin, not a path prefix, and must differ from the WebUI origin. A distinct hostname also avoids accidentally sharing domain-scoped Host cookies. Do not give the sandbox hostname a Host authentication cookie.

With a local reverse proxy on the same machine, keep `bind: 127.0.0.1` and forward the fixed port instead. For plain-HTTP local Docker testing, publish separate WebUI and sandbox ports and set `public_url` to the browser-reachable sandbox origin. Binding WebUI to a non-loopback address requires an explicit sandbox `public_url`; startup rejects omission rather than advertising an unusable internal address.

| Setting              | Default     | Meaning                                                                  |
| -------------------- | ----------- | ------------------------------------------------------------------------ |
| `enabled`            | `false`     | Enable Apps capture and WebUI hosting                                    |
| `servers`            | `[]`        | Allowlisted IDs among the Agent's selected servers                       |
| `sandbox.bind`       | `127.0.0.1` | Literal IPv4/IPv6 listener address                                       |
| `sandbox.port`       | `0`         | `0` chooses a free local port; use a fixed port for forwarding           |
| `sandbox.public_url` | `null`      | Browser-reachable separate HTTP(S) origin; required for remote listeners |

The installed Python wheel and sdist contain the sandbox proxy and compiled Host assets. Node.js is needed to prepare repository assets, not to run an installed WebUI or rebuild its wheel from an sdist.

## Troubleshooting

- **No App card:** confirm server selection and the Apps allowlist, restart WebUI, then invoke an App-producing tool. Enabling Apps does not reconstruct missing presentation metadata in older history.
- **Sandbox unavailable:** fix the bind address, port conflict or public origin and restart WebUI. A bind failure leaves the normal workbench and text result usable but does not fall back to same-origin HTML.
- **Initialization failed:** the App did not finish the public SDK handshake within 15 seconds. Inspect browser errors and asset/CSP declarations. This timeout concerns the View handshake, not MCP connection lifetime.
- **Activate fails after a configuration change:** current authority or the original tool contract no longer matches. Reinvoke the tool under the current configuration if appropriate; opening history never does this for you.
- **Operation limit reached:** explicitly close and reopen the View. The browser retains at most 128 tool requests of at most 256 KiB each; it does not evict undecided work to admit more calls.
- **App requires external assets or browser storage:** offline display is not guaranteed. Only declared exact resource/connect origins are allowed, Host-origin network access is blocked, and this profile does not supply a stable App storage origin.
