# a13n Service SDKs

The a13n Service SDKs live under one standalone `sdk/` boundary. They are intentionally excluded from the root Python and Rust workspaces so each language can evolve, validate, version, and release independently.

| Language   | Distribution or module                                | Source directory | Release tag                         |
| ---------- | ----------------------------------------------------- | ---------------- | ----------------------------------- |
| Python     | `a13n`                                                | `sdk/python`     | `release/a13n/python/<version>`     |
| Go         | `github.com/converge-ai-labs/agent-foundation/sdk/go` | `sdk/go`         | `release/a13n/go/<version>`         |
| Rust       | `a13n`                                                | `sdk/rust`       | `release/a13n/rust/<version>`       |
| TypeScript | `@converge.ai/a13n`                                   | `sdk/typescript` | `release/a13n/typescript/<version>` |

The `a13n-service-cli` remote CLI is a companion to these SDKs, not another SDK distribution. It is an independent Cargo project at `sdk/rust/a13n-service-cli` with package name `a13n-service-cli`, its own lock file, and no membership in the root Rust workspace or the Rust SDK project. Network commands use typed operations from `a13n`; they do not maintain a separate HTTP client. The CLI does not manage a13n Service processes or access service implementation internals.

`<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`. Python package metadata normalizes an RC to `X.Y.ZrcN`, and TypeScript RCs publish under the npm `rc` dist-tag rather than `latest`.

All four SDKs currently provide publishable `0.0.x` package shells only. They reserve stable package identities without committing the project to a generator, transport, or service contract before the API is ready. SDK language versions are independent.

Run the fast standalone SDK checks while iterating and the complete release checks before publishing:

```bash
make sdk-check
make sdk-check-all
make a13n-service-cli-check
make a13n-service-cli-check-all
```

The CLI releases as six platform-specific binary archives plus `SHA256SUMS` through `release/a13n-service-cli-v<version>`. It is not published to crates.io and has no mutable `latest` release selector.

## External tool selection wire contract

Service Agent configuration uses `connector_tools` and `mcp_tools` arrays. Entries contain `connector_connection_id` or `mcp_connection_id`, optional `tools`, and `defer_loading` (default `false`). An omitted or null entry-level `tools` selects all tools; an empty array selects none. Run overrides inherit omitted categories and replace supplied arrays, including clearing with `[]`. Category-level null, aliases, `exposure`, and inline credentials are invalid.

The package shells have no Agent client models to migrate. Future typed clients must preserve these distinctions and must not translate the removed alias-keyed shape. See [External tools](../docs/a13n-service/external-tools.md) for request examples and execution semantics.
