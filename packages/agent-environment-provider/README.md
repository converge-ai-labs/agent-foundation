# Agent Environment Provider

`converge-agent-environment-provider` is the shared Environment provider library for Converge agents. The repository directory is `packages/agent-environment-provider`, the Python distribution is `converge-agent-environment-provider`, and the import package is `converge_agent_environment_provider`.

This package is currently a scaffold. It exposes only distribution version metadata; Provider, Manager, Docker, E2B, and EIP behavior will be added separately after their public contracts are implemented and reviewed.

The package is a pure Python library and intentionally has no package-specific service image or Dockerfile. Repository sandbox-image work continues to use the existing root [`Dockerfile.sandbox`](../../Dockerfile.sandbox).

## Versioning

Agent Environment Provider, `converge-agent-harness`, and `converge-agent-stream-protocol` form the Harness release group. A `release/harness-v<version>` tag publishes all three distributions at exactly the same version, where `<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`. Python package metadata represents the RC as `X.Y.ZrcN`.

The accepted architecture and compatibility contract are defined in the [Agent Environment Provider specification](../../spec/agent-environment-provider/README.md).
