# Stateful MCP App example

A real stdio FastMCP server and a self-contained browser App using the public `@modelcontextprotocol/ext-apps` SDK. The counter makes connection lifetime visible: tool calls after the assistant finishes use the same process and preserve its value. This is not browser automation.

## Run in Harness UI

From the repository root, with the normal development dependencies installed:

```bash
make mcp-apps-demo
```

The launcher builds the browser resources and starts an isolated WebUI, a separate sandbox origin, a real stdio MCP server on demand, and the local scripted HTTP model. Open the printed login URL, start a conversation and send `[mcp-app] Open the counter.` No model account or API key is required. The model is scripted development data, not real inference; the Host, transport, history and permission paths are the production implementations. Ctrl+C stops the launcher and removes its temporary state.

1. Open the App, then choose **Activate interactions** in the trusted Host card.
2. Click **Add one** several times after the assistant has finished. The value persists without new conversational turns.
3. Click **Reset (approval)**. Decline once, then try again and approve in the Host card, outside the iframe.
4. **Read current value** uses MCP resource reads, not another tool call.
5. **Offer value as context**, inspect and select the context in the Host card, then send an ordinary composer message. An unselected value is not attached.
6. Expand the follow-up section and propose a message. Nothing is submitted until **Send once** in the trusted Host card. External links similarly require confirmation.
7. Reload the page. Reopening displays the retained original result, not the last DOM state. Activate and read the server resource to see the still-live counter. Closing a View does not close the connection; restarting the Host loses this example's in-memory state.

## Use the server independently

Python 3.13, uv and Node.js are needed to prepare this example. After building, the server needs no Node.js or CDN.

```bash
cd examples/mcp-apps
npm ci
npm run build
uv sync --locked
uv run --locked pytest
uv run --locked mcp-apps-example
```

The final command speaks MCP on stdin/stdout and waits for an MCP client. It is not an HTTP server. Add the following resource under the selected configuration directory's `mcp/` and enable it in the root YAML. WebUI automatically adds it to every root and child Agent; no Agent `mcp_servers` entry is needed:

```yaml
schema_version: '1'
kind: mcp_server
id: mcp-counter
name: Counter App
transport:
  command: /absolute/path/to/uv
  arguments:
    - run
    - --locked
    - --project
    - /absolute/path/to/examples/mcp-apps
    - mcp-apps-example
```

```yaml
webui:
  mcp_apps:
    enabled: true
    servers: [mcp-counter]
```

For the same permission demonstration, add an Agent `ToolPermissionsCapability` rule of `ask` for `mcp/mcp-counter/reset_counter`. The increment and reset tools have App-only visibility; the model sees `show_counter`.

## Read the implementation

- `src/mcp_apps_example/server.py`: tool/resource metadata and process-owned business state.
- `app.js`: handlers registered before connection, tool calls, resource reads, context, messages, links and theme updates.
- `app.html` and `build.mjs`: accessible controls and one bundled HTML resource with no external network grants.
- `tests/test_server.py`: real stdio calls, HTML delivery and fresh-process state.
- `../../dev/harness-ui/mcp_apps.py`: disposable Host configuration and the normal CLI entry point.

The two-part tool/resource registration and handler-before-connect pattern follow the official MCP Apps `basic-server-vanillajs` example at tag `v1.7.5` (commit `92f46a574568a3ddac7600343b7d3c4c4ed7b588`). This implementation uses FastMCP and a counter rather than copying the upstream time server. Host contracts remain in `spec/a13n-harness-ui/09-mcp-apps.md`; this example does not add a new public Host API.
