---
title: Tools and connections
description: Configure built-in toolsets, remote MCP and Composio connections, web search/scrape providers, and platform tools in Service.
---

Service equips agents with **built-in toolsets** (Files, Shell, Web, Memory, Browser, Skills) and external **Connections** (remote MCP servers and Composio connectors).

## Built-in toolsets

Enable toolsets on each agent revision in Console or via `agent.config`:

| Toolset   | Purpose                                                     |
| --------- | ----------------------------------------------------------- |
| `files`   | Read, write, search, and list files in mounted environments |
| `shell`   | Run commands in mounted environments                        |
| `web`     | Search, scrape, fetch, and download public web pages        |
| `memory`  | Read/write File Memory and search/update Record Memory      |
| `browser` | Automate managed Chromium browsers                          |
| `skills`  | Discover and read `SKILL.md` packages                       |

### Platform tools

Enable platform tools when an agent should inspect or manage Service resources (such as runs, threads, or skills) within the caller's workspace permissions.

## Web search and scrape

Add third-party search and scrape providers (Tavily, Exa, Firecrawl, Brave Search, Jina, Perplexity, etc.) under **Workspace settings → Providers → Web**, then select them on the agent's `web` toolset.

## Connections

Connect external business systems under **Connections** in Console (or `/api/v1/connections`):

- **Remote MCP:** Connect Streamable HTTP MCP servers with OAuth, bearer tokens, or static headers. Use `config.tools` to allowlist specific tool names.
- **Disablement:** Disabling a connection immediately blocks subsequent tool calls, even for runs already in progress.

### MCP server suggestions

Operators can pre-populate recommended MCP servers in Console via `providers.mcp_servers` in Service configuration.

### Composio connections

Add a Composio provider under **Workspace settings → Providers → Connector** to connect OAuth-backed SaaS integrations as managed connections.
