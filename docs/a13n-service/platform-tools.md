---
title: Internal platform-tool reference
description: Reference for how Service agents execute platform management tools under delegated caller authority and versioned configuration deltas.
---

Use this reference when configuring [Agent Composer](agent-composer.md) or your own platform tools. To connect an external client, use [management MCP](mcp.md).

## Internal agent tools

Service-managed agents use a separate internal platform-tool catalog. The Host supplies its Run credential to `/api/v1/platform-tools/`; external workspace keys use the management connection described below. Internal tools take direct task fields and versioned deltas, return business results, and never fall back to raw API tools. For example, changing an agent model submits `agent_id`, the observed `expected_version` and `model`; the Service preserves other configuration. Schedule tools construct invocation from task instructions and execution choices. Connection secrets remain in trusted configuration or authorization flows. Internal Run credentials cannot use external management MCP or raw product HTTP as a bypass.

For older saved agents, follow the [platform-tool cutover](upgrade-notes.md#internal-platform-tools) before upgrading.

For Agent configuration deltas, naming `base_revision_id` also requires an explicit `make_default`; omission returns `invalid_argument`. Missing required versions fail validation and stale versions return `precondition_failed`. On a version conflict, reread the current configuration and submit a freshly reviewed delta.

External clients use [management MCP](mcp.md), with HTTP envelopes and `request_body` arguments.

## Usage, Webhooks and environment credentials

The `usage` toolset exposes nine existing query operations, with workspace read access and admin checks for member attribution. The `subscriptions` toolset exposes three reads and three reviewed nonsecret writes; all require admin within the accepted Run authority. The `egress_credentials` toolset exposes only four metadata/application reads. They are opt-in for ordinary Agents and enabled in the current Composer definition.

Composer's native `configure_webhook` and `configure_egress_credential` client tools pause the Run for private Console forms. The current user executes writes through existing HTTP APIs; keys and tokens never enter tool arguments or results. Credential writes remain excluded from both internal and external MCP. Read [Composer](agent-composer.md#configure-webhooks-and-credentials) for the user flow, version conflicts and asynchronous application semantics.
