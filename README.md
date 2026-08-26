# Agent Foundation

Agent Foundation is an open-source cloud foundation by Converge AI for building agents and multi-agent systems. It combines an embeddable Agent Harness, hosted agent services, and built-in observability.

## Status

The project is currently in its architecture and specification phase. Public APIs and implementation details are not yet stable.

## Planned Components

- `agent-harness`: a reusable process-local agent harness built on Pydantic AI 2, distributed as `converge-agent-harness`
- [`agent-environment-provider`](packages/agent-environment-provider/README.md): the shared Environment provider library scaffold, distributed as `converge-agent-environment-provider`
- [`agent-stream-protocol`](packages/agent-stream-protocol/README.md): shared Harness-to-AG-UI projection and validation, distributed as `converge-agent-stream-protocol`
- [`agent-ui`](packages/agent-ui/README.md): a local single-user Host with a bundled [Harness UI](apps/harness-ui/README.md), default WebUI, and TUI, distributed as `converge-agent-ui`
- `logging`: shared pretty and structured logging, distributed as `converge-logging`
- `agent-envd`: an Environment Interaction Protocol provider distributed as the `converge-agent-envd` Rust package
- [`agent-envd-client`](packages/agent-envd-client/README.md): the matching low-level Python EIP client, distributed as `converge-agent-envd-client`
- [`foundation-service`](packages/foundation-service/README.md): an optional hosted control and execution service distributed as `converge-foundation-service`, with private [Foundation Web](apps/foundation-web/README.md) assets bundled into its container image
- [Foundation Service SDKs](sdk/README.md): standalone Python, Go, Rust, and TypeScript packages that will expose the hosted service API after its contract stabilizes

Applications will be able to embed the harness directly, use the hosted service, or replace providers through documented capability and protocol boundaries.

## Release Channels

- Harness releases use `release/harness-v<version>` tags and publish `converge-agent-environment-provider`, `converge-agent-harness`, and `converge-agent-stream-protocol` at exactly the same version. Published Harness metadata pins Provider, and published Protocol metadata pins Harness.
- Agent UI releases independently through `release/agent-ui-v<version>` tags. Each release pins Provider, Harness, and Protocol to one reviewed Harness release. Harness UI is compiled into the Agent UI sdist and wheel and has no independent npm artifact or release.
- Foundation releases version the repository root, logging, and hosted service together; they publish the logging and hosted service Python packages plus the versioned foundation-service image. They select compatible published Harness libraries rather than republishing them.
- agent-envd releases publish the `converge-agent-envd` crate, `converge-agent-envd-client` Python package, platform binaries, and the matching versioned sandbox image.
- SDK releases are independent per language under `release/sdk/<language>/<version>` tags.
- Every `main` revision publishes `dev` service and sandbox images to GHCR.

`<version>` is either stable `X.Y.Z` or release-candidate `X.Y.Z-rc.N`. RCs publish real registry artifacts and GitHub prereleases without advancing Docker or npm `latest`; Python package metadata and artifacts use the normalized `X.Y.ZrcN` spelling.

Workspace directory names omit the project prefix (`packages/agent-ui`), Python distribution names add the hyphenated `converge-` prefix (`converge-agent-ui`), and import packages normalize it with underscores (`converge_agent_ui`). The same rule applies to Agent Environment Provider, Agent Harness, and Agent Stream Protocol.

## Examples

Start with the runnable, tested [examples](examples/README.md) to see public extension points in complete integration paths. The [integration package examples](examples/plugins/README.md) show installed entry-point and explicit code composition for Environment provider factories and Harness plugins, and require no model credentials.

## Documentation

- Start with the [published user documentation](https://agent-foundation-docs.converge.ai/).
- Read the [platform specification](spec/README.md) for the current architecture and design boundaries.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for issue workflow, local setup, testing, and pull-request guidelines. Service implementation, persistence, migration, streaming, logging, and container standards are defined in [DEVELOPMENT.md](DEVELOPMENT.md).

## Maintainers

Review ownership and semantic routing are defined in [MAINTAINERS.md](MAINTAINERS.md).

## License

Licensed under the [Apache License 2.0](LICENSE).

Copyright 2026 Converge AI.
