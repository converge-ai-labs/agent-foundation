# Agent Environment Provider

`converge-agent-environment-provider` is the shared Environment provider library for Converge agents. The repository directory is `packages/agent-environment-provider`, the Python distribution is `converge-agent-environment-provider`, and the import package is `converge_agent_environment_provider`.

The package exposes the shared process-local runtime attachment boundary and explicit EIP session sources:

- `EIPEnvironmentAttachment` and the exhaustive `EnvironmentRuntimeAttachment` union;
- `StdioEIPSessionSource` for a private asyncio subprocess;
- `HttpEIPSessionSource` for an authenticated Host-dialed HTTP(S) endpoint;
- `AcceptedWebSocketEIPSessionSource` for an already-authenticated `websockets.ServerConnection` accepted by the Host.

Each source and attachment is single-use and creates one fresh initialized EIP session. The package owns carrier acquisition but not EIP method semantics, Harness operation adaptation, product authentication, or durable provider state. Provider Manager and built-in Docker/E2B lifecycle implementations remain separate package capabilities.

The package is a pure Python library and intentionally has no package-specific service image or Dockerfile. Repository sandbox-image work uses the shared [`deploy/containers/sandbox/Dockerfile`](../../deploy/containers/sandbox/Dockerfile).

## Versioning

Agent Environment Provider, `converge-agent-harness`, and `converge-agent-stream-protocol` form the Harness release group. A `release/harness-v<version>` tag publishes all three distributions at exactly the same version, where `<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`. Python package metadata represents the RC as `X.Y.ZrcN`.

The accepted architecture and compatibility contract are defined in the [Agent Environment Provider specification](../../spec/agent-environment-provider/README.md).
