# Agent Foundation

Agent Foundation is an open-source foundation for embedding Agents or hosting them as durable services. Its core surfaces are a Pydantic AI-based Agent Harness, a local Agent UI, Environment infrastructure, and the optional Foundation Service.

## Agent Packages

The local Agent stack consists of:

- `converge-agent-harness`, the process-local Agent runtime;
- `converge-agent-stream-protocol`, the shared Harness-to-AG-UI projection and validation package;
- `converge-agent-ui`, the local single-user Host with a bundled browser application and terminal UI.

The source directories omit the distribution prefix: `packages/agent-harness`, `packages/agent-stream-protocol`, and `packages/agent-ui`. Python imports use normalized underscore names: `converge_agent_harness`, `converge_agent_stream_protocol`, and `converge_agent_ui`.

Start with the [Agent Harness guide](agent-harness/index.md) to embed, build, run, extend, and resume a process-local Agent. See the [Agent Stream Protocol guide](agent-stream-protocol/index.md) for Harness-to-AG-UI observation, Host processors, snapshots, and reconstruction from source history.

Harness and Stream Protocol form one release group. A `release/harness-v<version>` tag publishes both at exactly the same version, and published Protocol metadata pins that Harness version. Agent UI advances independently through `release/agent-ui-v<version>` and pins both libraries to one reviewed Harness release. `<version>` is stable `X.Y.Z` or release-candidate `X.Y.Z-rc.N`; PyPI represents an RC as the equivalent PEP 440 version `X.Y.ZrcN`. The private `apps/harness-ui` browser source is compiled into the `converge-agent-ui` sdist and wheel; it is not published to npm and has no independent version or release workflow.

`converge-agent-ui` selects WebUI by default:

```console
converge-agent-ui
converge-agent-ui webui
converge-agent-ui tui
```

From a repository checkout, use the matching Make aliases:

```console
make agent-ui
make agent-ui tui
```

The Make targets forward to the package-provided command: the default form invokes `converge-agent-ui`, while the `tui` form invokes `converge-agent-ui tui`. Browser asset preparation remains part of the build pipeline. WebUI and TUI use the same application service, local session authority, Harness execution path, and Agent Stream Protocol AG-UI projection. A presentation surface does not own a separate Agent loop or continuation state.

## Architecture and Contribution

Read the [platform specification](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/README.md) for accepted architecture and subsystem boundaries. Read the [repository README](https://github.com/converge-ai-labs/agent-foundation/blob/main/README.md) for components and release channels, and [CONTRIBUTING.md](https://github.com/converge-ai-labs/agent-foundation/blob/main/CONTRIBUTING.md) before making changes.
