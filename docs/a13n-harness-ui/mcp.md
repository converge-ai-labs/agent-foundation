# MCP servers

Configure a command or Streamable HTTP server, then select its resource ID on an Agent. Creating a server file registers it; selecting its ID on an Agent or in defaults enables it. Use [Command reference](command-reference.md) for Harness UI shell commands. MCP command transports launch external programs.

## Copy a JSON configuration

Create `mcp/servers.json` beside your selected root configuration. Common client-style `mcpServers` objects work directly:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/workspace"]
    },
    "docs": {
      "type": "http",
      "url": "https://mcp.example.com/mcp",
      "headers": {
        "Authorization": "Bearer example-token",
        "X-Workspace": "${WORKSPACE_ID}"
      }
    }
  }
}
```

Replace the illustrative endpoint, path, and token. Enable these entries with `mcp_servers: [mcp-filesystem, mcp-docs]` on an Agent or under root `defaults`. Creating the JSON file alone does not enable them.

A `mcpServers` file can define several servers. Its names normalize to `mcp-` IDs: `My_Server` becomes `mcp-my-server`. Duplicate IDs across files reject the configuration, so check the normalized IDs before adding a second file.

For a command entry, use `command`, optional `args`, and optional `env`. For a remote entry, use `url` and optional `headers`. Optional `type` accepts `stdio` for commands and `http` or `streamable-http` for remote endpoints. Remote connections use Streamable HTTP, not legacy SSE. OAuth login, JSONC comments, trailing commas, `disabled`, and unrelated client-specific fields are not supported. `mcpServers` is a common client convention, not a universal MCP protocol configuration standard.

Both `.yaml` and `.json` can also contain the single-resource format shown below; both accept the `mcpServers` wrapper. Existing YAML files continue to work without migration.

## Literal values and environment references

Command `env` (or canonical `transport.environment`) and remote `headers` accept:

| Value                   | Behavior                                                        |
| ----------------------- | --------------------------------------------------------------- |
| `"example-token"`       | A literal string, including ordinary non-secret settings        |
| `"${API_TOKEN}"`        | Read an environment variable when a Run starts                  |
| `"Bearer ${API_TOKEN}"` | Substitute `${NAME}` occurrences in a string                    |
| `{"env": "API_TOKEN"}`  | Existing explicit environment reference; works in YAML and JSON |

Empty literal strings and whitespace are preserved. References require non-empty variables in the **Harness UI process** environment; exporting in another shell does not change an already-running process. Expansion is one pass, only for `${NAME}` with a valid environment-variable name; there is no shell execution or default-value syntax. These substitutions apply only to environment/header values, not commands, arguments, or URLs.

Direct token configuration is supported; environment references are optional. Keep credential-bearing source files private and out of version control. Harness UI does not copy MCP source text or literal environment/header values into `config show`, accepted generations, or Run compositions: it retains source locations and digests, then reads values at Run startup. A captured Run requires that literal-bearing source file to remain present and byte-identical until client construction. Editing it is supported for newly captured Runs, but an older captured Run or child continuation may fail with `mcp_source_changed`; use current configuration for a new Run. Already constructed ordinary clients keep their Run-local values. Opted-in [MCP Apps connections](mcp-apps.md) outlive a Run but recheck current binding and credentials before follow-up operations. Environment references can rotate without editing the source file.

After editing, run `a13n-harness-ui config validate` and start a new session if you changed default MCP selections. Validation does not connect to servers or verify credentials.

## Command transport

Create `mcp/github.yaml`:

```yaml
schema_version: "1"
kind: mcp_server
id: mcp-github
name: GitHub
transport:
  command: npx
  arguments: ["-y", "@modelcontextprotocol/server-github"]
  environment:
    GITHUB_TOKEN:
      env: GITHUB_TOKEN
```

This illustrative server requires its executable/package and access token to be available. Harness UI does not validate external service entitlement by making a test call during file parsing. Review the command and package before enabling it.

## Remote transport

Create `mcp/docs.yaml` using your actual MCP endpoint:

```yaml
schema_version: "1"
kind: mcp_server
id: mcp-docs
name: Documentation service
transport:
  url: https://mcp.example.com/mcp
  headers:
    Authorization:
      env: DOCS_MCP_AUTHORIZATION
```

The environment variable contains the entire header value expected by the server, including any authentication scheme. Alternatively, use `"Bearer ${DOCS_MCP_TOKEN}"` to interpolate only the token. URLs must be credential-free HTTPS; plain HTTP is allowed only for a literal loopback host with no configured headers. Authenticated redirects cannot weaken transport or leak headers to another origin.

## Enable a server

In an Agent file:

```yaml
mcp_servers: [mcp-github, mcp-docs]
```

Or in root YAML:

```yaml
defaults:
  mcp_servers: [mcp-github]
```

An Agent's `mcp_servers: null` inherits the root defaults; `[]` explicitly selects none. Existing sessions retain exact sticky selections. Ordinary clients/processes are constructed fresh for Runs and are not saved into continuations. Opted-in [MCP Apps](mcp-apps.md) use App-owned connections that can stay interactive after Run completion; they are still never serialized into continuations. Model Sandbox selection does not imply that an arbitrary external MCP command or remote service is sandboxed.

## MCP field reference

The shared fields are `schema_version: "1"`, `kind: mcp_server`, unique `mcp-` `id`, and `name`. `transport` accepts exactly one form:

| Form    | Fields                                                                                                                    |
| ------- | ------------------------------------------------------------------------------------------------------------------------- |
| Command | Required `command`; `arguments` defaults to `[]`; `environment` defaults to `{}`, values are strings or `{env: VARIABLE}` |
| Remote  | Required `url`; `headers` defaults to `{}`, values are strings or `{env: VARIABLE}`                                       |
