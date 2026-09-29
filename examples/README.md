# Examples

These five independent projects show public Harness integration points. Each has its own manifest, lockfile, source, and tests.

## Start Here

| Goal                                                       | Example                                                 | Start with                                               |
| ---------------------------------------------------------- | ------------------------------------------------------- | -------------------------------------------------------- |
| Save a conversation and recover after restart              | [Agent application](agent-app/README.md)                | Offline CLI turns and `HarnessState` persistence         |
| Construct or re-enter an Environment                       | [Environment Providers](environment-provider/README.md) | Direct Local; remote, Envd, and Docker paths are opt-in  |
| Add Capabilities, middleware, Providers, or Run extensions | [Plugins](plugins/README.md)                            | Installed entry points and explicit composition          |
| Install a Provider as a separate distribution              | [Provider plugin](provider-plugin/README.md)            | Entry-point loading and fresh workspace adapters         |
| Render an interactive MCP tool result                      | [MCP App](mcp-apps/README.md)                           | Stdio server, bundled UI resource, and real client tests |

From the repository root, `make examples-check-all` runs the complete examples gate. To work on one example, follow its README instead. All five have offline test paths; live model credentials are not needed. Building the MCP App first requires Node.js/npm. Remote Envd and Docker demos require their respective runtimes.

For the quickest interactive application:

```bash
cd examples/agent-app
uv sync --locked
uv run agent-app-example "first turn" "second turn"
uv run pytest
```

## How to Use These Examples

Run the example, then read its source and tests for the integration boundary. Copy the smallest part that matches your application and replace demo policy and persistence with your own. Accepted contracts live in [`spec/`](../spec/README.md); task-oriented guides live in [`docs/`](../docs/).
