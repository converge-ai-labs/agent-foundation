---
title: Tool search
description: Defer large tool catalogs behind searchable tool groups in Harness so models load only the schemas they need per turn.
---

When an Agent connects to dozens or hundreds of tools (such as large MCP servers), sending every schema on every turn wastes context and degrades tool selection. `ToolSearchCapability` exposes a compact search tool and loads matching schemas on demand.

## Enable tool search

Wrap one or more toolsets in `ToolSearchGroup` and pass them to `ToolSearchCapability`:

```python
from a13n_harness.capabilities.tool_search import ToolSearchCapability, ToolSearchGroup

tool_search = ToolSearchCapability(
    groups=(
        ToolSearchGroup(
            name="github",
            description="Issues, pull requests, and repository metadata.",
            sources=(github_toolset,),
        ),
    ),
)
```

At runtime, the Model sees the group descriptions and searches by keyword to activate specific tools for the current Run. Grouping only controls schema presentation—it never bypasses tool permissions or approval policies.

## Group a Run-bound MCP Capability

Pass a local `MCP` or `ContextualMCP` Capability directly as a `ToolSearchGroup` source to preserve per-Run header resolution and binding before tool preparation:

```python
ToolSearchGroup(
    name="docs_mcp",
    description="Internal documentation search and retrieval.",
    sources=(mcp_capability,),
)
```

## Host integration

Hosts should store stable source IDs, group names, and descriptions in configuration, resolve them through trusted factories at build time, and aggregate groups in one `ToolSearchCapability` (or let a plugin return its own `ToolSearchCapability` from `get_capabilities()`).
