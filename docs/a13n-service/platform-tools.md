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

## Connect an existing environment

Enable the `environments` toolset to offer `list_connectable_environments` and `connect_environment`. The tools use the current Run's delegated authority and do not expose credentials. Discovery returns existing usable instances and configured operations; it does not start them or verify readiness.

Connect with an environment ID and a new alias, for example `connect_environment(environment_id="env_...", alias="dev")`. This adds a named mount to the current Run. To also save it for later ordinary Runs, set `persist_to_thread=true` and supply the Thread's observed `expected_version` from `get_thread`. A stale version or alias conflict rejects both writes. Existing mounts and the default environment are preserved.

The new alias becomes available only at the next root model request, after the current tool batch finishes. Do not use it in the same batch, including sequential orchestration. On the next model turn, check the current environment context before calling Shell, Files or Computer tools against that alias. No additional user message is required. Acceptance is not remote readiness: first use may still prepare the environment or report an unavailable target. Agent tool settings and environment permissions continue to apply.

Query saved mounts through `get_run` and `read_content` on `content.environment_mounts`. This is accepted configuration, not a live connection check. Recovery retains accepted additions; already-started background operations retain their original environment scope.

Upgrade all Controls and Workers and apply the database guard migration before enabling this toolset. Older Workers cannot apply live additions, even though they can read the mount records.
