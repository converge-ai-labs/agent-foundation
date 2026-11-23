---
title: MCP
description: Connect stdio and Streamable HTTP MCP servers in Harness with Run-scoped headers, host-owned client reuse, and tool-search grouping.
---

Attach Model Context Protocol (MCP) servers to a Harness Agent using Pydantic AI's `MCP` / `ContextualMCP` capabilities through `AgentDefinition.capabilities` or `RunBindings.capabilities`.

## Connect a server

- **Stdio servers:** Launch a local command with explicit arguments and environment variables.
- **Streamable HTTP servers:** Connect to a remote `/mcp` endpoint with per-Run headers (such as bearer tokens or tenant IDs) resolved in `ContextualMCP`.

## Host-owned connections and tool search

- **Connection reuse:** By default, MCP clients open and close with each Run. Hosts can manage long-lived client pools and pass connected clients into `RunBindings` when avoiding reconnect latency.
- **Large catalogs:** Pass an `MCP` or `ContextualMCP` Capability directly to a [ToolSearchGroup](tool-search.md#group-a-run-bound-mcp-capability) so schemas are loaded on demand while preserving per-Run header resolution.
- **Permissions:** Apply tool allowlists and [managed tool permissions](managed-tools.md#select-tool-permissions) on top of MCP tools without modifying the server.
