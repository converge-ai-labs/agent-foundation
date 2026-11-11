# Harness UI Connector example

This source example adds a trusted local periodic Connector to a WebUI App. It demonstrates public SDK registration, structured setup, conditions validated by Pydantic, plugin-owned matching, and an atomic cursor/input commit. It uses the repository's Python environment rather than a separate example distribution.

## Run

From the repository root, after preparing the normal Harness UI development environment:

```bash
uv run --locked python examples/harness-ui-connectors/example_connector.py --config /path/to/a13n-harness-ui.yaml
```

Use a separate demo data root. Open the local login URL printed by the server. In **Settings → Connections → Connectors**, add **Periodic example**, configure a label and interval, then enable **Deliver external input to Bot** and add a Tick rule. The `every` condition matches every Nth local tick. Bot must have a configured Model and be enabled to consume the retained input. Collection itself needs no Model or network account.

The example changes only its Connector configuration and state. Enabling delivery can start Bot Runs and incur Model usage. Disable or delete the connection when finished.

## Test offline

```bash
uv run --locked pytest examples/harness-ui-connectors/test_example.py -q
```

The tests cover declaration/form compatibility and the matching policy. The Harness UI package's Connector integration tests cover native HTTP MCP, private transport references, configuration fencing, state/input atomicity, ownership and setup cancellation.

## Integrate

`Periodic` is a no-argument `Definition`. The launcher passes it through `HarnessUiIntegrations(connectors=(Periodic(),))`. A separately installed distribution can instead expose a no-argument factory:

```toml
[project.entry-points."a13n_harness_ui.connectors"]
example-periodic = "example_connector:Periodic"
```

The Host renders rules and persists them; `matches()` owns their meaning. The producer reads a codec-versioned cursor, prepares normalized external input, then commits cursor advancement and input together. A failed commit never advances that cursor. Real integrations perform external reads before this short commit and implement their own recovery policy.

See [Connectors](../../docs/a13n-harness-ui/connectors.md) and the [accepted contract](../../spec/a13n-harness-ui/13-connectors.md). The built-in GitHub provider subclasses the developer-facing `RemoteMcp` Controller library to reuse native tools and typed service access. It owns its account/token setup and fixes its transport policy in code. The library has no catalog entry or generic MCP setup form; arbitrary MCP servers retain their separate MCP configuration.
