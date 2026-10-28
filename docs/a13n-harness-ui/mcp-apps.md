---
title: Interactive MCP Apps
description: Enable an MCP App, interact with its result, and configure a separate sandbox origin for remote WebUI.
---

MCP Apps turn tool results into interactive cards in WebUI. For example, a counter can keep responding to clicks after the Agent finishes, using the same MCP server connection. This is a result interface, separate from [Host browser control](webui.md#browse-on-the-host).

## Enable Apps for selected servers

You need a working WebUI and an MCP server that produces Apps. Ordinary text-only MCP tools will not gain an interface by enabling this setting. The server must supply `ui.resourceUri` metadata and a `text/html;profile=mcp-app` resource; legacy MCP-UI HTML is not supported.

1. [Add the MCP server](mcp.md) under the selected configuration directory's `mcp/` directory. Note its exact resource ID.

2. Merge the following into the selected root `a13n-harness-ui.yaml`. Replace `mcp-counter` with that ID.

   ```yaml
   webui:
     mcp_apps:
       enabled: true
       servers: [mcp-counter]
   ```

3. Run `a13n-harness-ui config validate` using the same configuration selection. Fix configuration errors before proceeding.

4. Restart WebUI, open a conversation, and ask the Agent to invoke the server's App-producing tool.

5. Check for an App card alongside the ordinary tool result. If it is absent, use [Troubleshooting](#troubleshooting).

Apps default off. In WebUI, the selected servers become available to every root and child Agent without editing Agent YAML. Existing tool filters and permissions still apply. TUI and one-shot `run` use their ordinary MCP selections instead.

For an account-free trial, prepare the repository's [source development environment](installation.md#run-from-source), then run this at the repository root:

```bash
make mcp-apps-demo
```

Open the printed login URL, start a conversation, and send `[mcp-app] Open the counter.` Activate the card and click **Add one** after the Agent finishes: the value should change without another conversational turn. Ctrl+C stops the demo and removes its temporary state. The demo uses a scripted model and a real stdio server. Its build prerequisites and independent server setup are in `examples/mcp-apps/README.md` in the checkout.

## Open and interact

A live result can show its App immediately. In saved history, choose **Open App** to display the retained original result. Opening does not repeat the tool call or connect to the server.

1. Choose **Activate interactions** in the Host card to create a View: the active controls for this presentation.
2. Use the App's controls. Same-server tool and resource requests are checked against current Agent selection, tool visibility, and permissions.
3. If approval is required, inspect the server, tool, and exact arguments in the Host card outside the iframe, then approve or deny.
4. If an operation's outcome is uncertain, use **Check result** to read its existing receipt. Do not repeat a possibly completed write.

App-operated calls treat `deny` as blocked and `ask` as requiring approval. A `review` rule allows dispatch without invoking a review model or custom Agent reviewer. Calls made by the Agent retain their normal review policy. Child Apps also require their original delegation route to remain selected.

An App request can require [MCP human input](mcp.md#human-input-from-mcp-servers). Answer in the Host interface outside the iframe to continue that operation. Only its complete final result can become a saved App snapshot.

Keep these lifetimes separate:

| Action                                                                                 | Result                                                                              |
| -------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| Close a View                                                                           | Ends its controls and pending confirmations; dispatched operations may still finish |
| Reload and choose Open App                                                             | Shows the saved original, not the last interactive screen state                     |
| Reactivate                                                                             | Checks current authority and lets the App read current server state                 |
| Finish a Run or disconnect the browser                                                 | Keeps an eligible Host-owned server connection alive                                |
| Shut down the Host, close the connection, retire its binding, or dispose of the Thread | Ends the connection; server-private state is not restored by WebUI                  |

Connections have no idle timeout. A crashed server is not silently restarted, and business calls are not replayed. Removing a generic Agent MCP selection leaves the server available while Apps still selects it. Removing the Apps selection retires its App-enabled binding; an independent generic selection follows `mcp.host_owned_servers`. Selection edits apply to later Run captures.

## Context is opt-in

To include an App's value in a prompt:

1. Use the App's action that offers text or structured context.
2. Inspect and select that value in the Host card.
3. Write an ordinary composer message and send it.

Send captures the exact selected values. Selection belongs to mounted Apps in this browser tab's conversation, not another tab or the shared draft. Unselected context stays out of the prompt; stale or missing selections fail visibly. Each value is limited to 64 KiB, with at most eight Views per submission. The Model receives source-attributed external data. Context is not automatically attached to steering.

An App can also propose a separate message. Review its text and destination, then choose **Send once** or **Decline** outside the iframe. A child App proposes a handoff to its owning root conversation. A busy root rejects the message instead of queuing it or turning it into steering. Your composer draft stays intact. If the response is uncertain, check the receipt before taking further action.

## External links

Review the destination shown in the Host card, then click to open it. The Host opens confirmed HTTP(S) links in a new tab without access back to the workbench. It rejects embedded credentials and links to the Host or sandbox origin. Opening a link does not require an active MCP connection.

## Local, Docker and reverse-proxy origins

Apps run in an opaque iframe inside a separate-origin sandbox proxy. This listener belongs to WebUI; it is separate from the Agent's Environment. It serves no authenticated Host API or login. Keep Host authentication headers and cookies away from it.

### Same-machine development

When the browser and WebUI run on the same machine, the defaults are sufficient: `127.0.0.1` and an automatically chosen free port. Other devices cannot reach that loopback URL. Use an explicit browser-reachable origin for remote access.

### Container or remote WebUI

For a WebUI published as `https://chat.example.com`, configure a distinct sandbox origin such as `https://apps.example.net`:

1. Merge this fragment into the root YAML, using your server ID and actual sandbox hostname.

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

2. Make port 8766 reachable from your reverse proxy. Route `https://apps.example.net/sandbox.html` to this sandbox listener, preserving query strings and response security headers.

3. Use HTTPS for both public origins. Do not route sandbox traffic to Host API/auth endpoints or forward Host credentials to it.

4. Restart WebUI. From the remote browser, invoke an App-producing tool and verify that its card opens and activates.

`public_url` is an origin, not a path prefix, and must differ from the WebUI origin. Prefer a distinct hostname that does not receive domain-scoped Host cookies. For a proxy on the same machine, keep `bind: 127.0.0.1` and forward the fixed port. For local HTTP Docker tests, publish separate WebUI and sandbox ports and advertise the browser-reachable sandbox origin.

| Setting              | Default     | When to change it                                       |
| -------------------- | ----------- | ------------------------------------------------------- |
| `enabled`            | `false`     | Set `true` to host Apps                                 |
| `servers`            | `[]`        | Select exact App server resource IDs                    |
| `sandbox.bind`       | `127.0.0.1` | Use a reachable literal IP for a proxy outside the Host |
| `sandbox.port`       | `0`         | Choose a fixed port for forwarding                      |
| `sandbox.public_url` | `null`      | Required when WebUI binds a non-loopback address        |

Installed wheels and sdists include the sandbox and compiled Host assets. Running an installed WebUI or rebuilding its wheel from an sdist needs no Node.js; preparing repository assets does.

## Troubleshooting

| Symptom                                                | Check or action                                                                                                                                                                                                                             |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| No App card                                            | Check the enabled flag and server ID, restart WebUI, and invoke an App-producing tool. Old history without App metadata cannot gain a card retroactively.                                                                                   |
| Sandbox unavailable                                    | Check bind address, port conflicts, and the public origin, then restart. Ordinary text results remain usable; there is no same-origin HTML fallback.                                                                                        |
| Initialization failed                                  | The SDK handshake exceeded 15 seconds. Inspect browser errors and declared resource/CSP origins. This is not an MCP connection idle timeout.                                                                                                |
| Activation fails after an edit                         | Current permissions, server binding, child route, or original tool contract changed. Invoke the tool again under current configuration if appropriate.                                                                                      |
| Operation limit reached                                | Close and reopen the View explicitly. Each View retains at most 128 tool requests, each at most 256 KiB.                                                                                                                                    |
| External assets fail                                   | Check exact declared resource/connect origins. Host-origin access and wildcard grants are unavailable; external assets can also prevent offline display.                                                                                    |
| An App expects browser storage or unsupported features | This profile supports inline display, same-server tools/resources, text/structured context, messages, external links, and theme updates. It does not provide stable-origin storage, image context, App-provided model tools, or fullscreen. |
