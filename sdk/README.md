# a13n Service SDKs

The a13n Service SDKs live under one standalone `sdk/` boundary. They are intentionally excluded from the root Python and Rust workspaces so each language can evolve, validate, version, and release independently.

| Language   | Distribution or module                                | Source directory | Release tag                         |
| ---------- | ----------------------------------------------------- | ---------------- | ----------------------------------- |
| Python     | `a13n`                                                | `sdk/python`     | `release/a13n/python/<version>`     |
| Go         | `github.com/converge-ai-labs/agent-foundation/sdk/go` | `sdk/go`         | `release/a13n/go/<version>`         |
| Rust       | `a13n`                                                | `sdk/rust`       | `release/a13n/rust/<version>`       |
| TypeScript | `@converge.ai/a13n`                                   | `sdk/typescript` | `release/a13n/typescript/<version>` |

The `a13n-service-cli` remote CLI is a companion to these SDKs, not another SDK distribution. It is an independent Cargo project at `sdk/rust/a13n-service-cli` with package name `a13n-service-cli`, its own lock file, and no membership in the root Rust workspace or the Rust SDK project. Its current implementation has only help/version, with no network commands or authentication flags. Future network commands belong on typed Rust SDK operations, not a separate HTTP client. The CLI does not manage a13n Service processes or access service implementation internals.

`<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`. Python package metadata normalizes an RC to `X.Y.ZrcN`, and TypeScript RCs publish under the npm `rc` dist-tag rather than `latest`.

SDK language versions and implemented coverage are independent:

| Client           | Current coverage                                                                                          |
| ---------------- | --------------------------------------------------------------------------------------------------------- |
| TypeScript       | Complete checked Native HTTP API, Workspace binding, Run SSE, and notification WebSocket                  |
| Python, Go, Rust | Generated complete Native HTTP bindings, plus the compatible Search Provider facade and Workspace binding |
| Companion CLI    | Help/version only                                                                                         |

The three Search facades preserve search-only Agent configuration/Run override fields; their separate generated models cover the full ordinary HTTP contract. SSE and WebSocket recovery remain implemented only by TypeScript. See [Service SDKs](../docs/a13n-service/sdks.md) for examples, constructor options, timeouts, retries, errors, pagination, binary transfer, and shutdown boundaries.

All languages consume `sdk/openapi.json`. `make sdk-generate` exports Service and regenerates the bindings; `make sdk-generated-check` checks the same result without changing committed files. Local pre-commit regeneration and CI consistency are described in [Native SDK generation](codegen/README.md).

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

The TypeScript generated Native types preserve these distinctions. Python, Go, and Rust preserve unknown Service-owned fields in their search-only wrappers but do not locally validate the full external-tool configuration. Do not translate the removed alias-keyed shape. See [External tools](../docs/a13n-service/external-tools.md) for request examples and execution semantics.
