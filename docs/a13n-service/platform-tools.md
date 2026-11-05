---
title: Internal platform-tool reference
description: Understand agent-side authority, configuration deltas, and the external management boundary.
---

Use this reference when configuring [Agent Composer](agent-composer.md) or your own platform tools. To connect an external client, use [management MCP](mcp.md).

## Internal agent tools

Service-managed agents use a separate internal platform-tool catalog. The Host supplies its Run credential to `/api/v1/platform-tools/`; external workspace keys use the management connection described below. Internal tools take direct task fields and versioned deltas, return business results, and never fall back to raw API tools. For example, changing an agent model submits `agent_id`, the observed `expected_version` and `model`; the Service preserves other configuration. Schedule tools construct invocation from task instructions and execution choices. Connection secrets remain in trusted configuration or authorization flows. Internal Run credentials cannot use external management MCP or raw product HTTP as a bypass.

For older saved agents, follow the [platform-tool cutover](upgrade-notes.md#internal-platform-tools) before upgrading.

For Agent configuration deltas, naming `base_revision_id` also requires an explicit `make_default`; omission returns `invalid_argument`. Missing required versions fail validation and stale versions return `precondition_failed`. A conflict requires rereading and reviewing a new change, not silently refreshing an approved version.

External clients use [management MCP](mcp.md), with HTTP envelopes and `request_body` arguments.
