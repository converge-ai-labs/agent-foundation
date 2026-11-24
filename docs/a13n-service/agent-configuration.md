---
title: Configure an agent
description: Manage immutable agent revisions, configure tool permissions and LLM risk review, attach subagents, enable context compaction, and bind plugins in Service.
---

Configure capabilities on an agent draft in Console (or [Agent Composer](agent-composer.md)), validate and publish a numbered revision (`POST /api/v1/agents/{agent_id}/revisions`), and test it before promoting it to default.

## Agent configuration

<span id="configuration-fields"></span>

| Capability                | Agent `config` fields                                                                  | Guide                                                     |
| ------------------------- | -------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| Model & instructions      | `model`, `instructions`, `model_settings`, `model_characteristics`                     | [Models](models.md)                                       |
| Tools & connections       | `toolsets`, `connection_tools`, `tool_search`                                          | [Tools and connections](tools.md)                         |
| Skills                    | `skills: [{skill_id, revision_id}]`                                                    | [Skills](skills.md)                                       |
| Client tools & questions  | `client_tools`, `user_questions`                                                       | [Client tools and questions](#client-tools-and-questions) |
| Delegation                | `subagents`                                                                            | [Subagents](#subagents)                                   |
| Review & media fallbacks  | `reviewer`, `media_understanding`                                                      | [Media understanding](models.md#media-understanding)      |
| Output contract & retries | `output_spec` (JSON Schema, `variants`, or `alternatives`), `retries: {tools, output}` | [Execution options](execution-options.md)                 |
| Default environment       | `default_environment_template_id` or `default_environment_id`, `lazy_environment`      | [Environments](environments.md)                           |
| Memory & compaction       | `memory`, `memory_mounts`, `background_compaction`                                     | [Memory](memory.md), [Compaction](#context-compaction)    |
| Runtime plugins           | `plugins`                                                                              | [Configure plugins](#configure-plugins-in-console)        |

## Tool permissions

<span id="set-approvals-and-automatic-review"></span>

Set `allow`, `ask`, `review`, `deny`, or `inherit` on `toolsets.<toolset>.tools.<tool>.permission` and `connection_tools[].permission` (with per-tool `permissions` overrides):

- **`ask`:** Pauses the run as `waiting` (`pending.approvals`).
- **`review`:** Evaluates risk (`low`..`extra_high`) with the configured `reviewer` model and applies your threshold policy (deny or ask a human; defaults deny `extra_high` and ask on review error).

## Client tools and questions

Declare application-executed tools in `client_tools` and enable `user_questions: true` for `ask_user_question`:

```json
{
  "user_questions": true,
  "client_tools": [{
    "name": "open_ticket",
    "description": "Open a support ticket in the customer's CRM.",
    "parameters_json_schema": {
      "type": "object",
      "properties": {"title": {"type": "string"}},
      "required": ["title"]
    }
  }]
}
```

Both pause the run in `pending.calls`; execute or answer them in your app and [resume the waiting run](agents-and-runs.md#resume-a-waiting-run).

## Subagents

Attach workspace agents as `async` (default—creates child threads in the same session using [async delegation tools](../a13n-harness/delegation-and-codeact.md#asynchronous-children)) or `inline` subagents, and optionally enable on-demand `spawn_subagent`:

```json
{
  "subagents": {
    "mode": "async",
    "named": {
      "researcher": {
        "agent_id": "ap_...",
        "description": "Finds and summarizes sources.",
        "usage_limits": {"request_limit": 20},
        "environment": {"mode": "shared"}
      }
    },
    "dynamic": {"enabled": true}
  }
}
```

Async edges set `environment.mode` to `"shared"`, `"dedicated"` (with `template_id`), or `"none"`.

## Context compaction

Synchronous compaction is enabled by default alongside automatic overflow recovery. Enable non-blocking background summarization under **Advanced → Context compaction**:

```json
{"background_compaction": {"enabled": true, "blocking_threshold": 0.95}}
```

## Configure Plugins in Console

Under **Capabilities → Plugins → Add plugin**, select any plugin factory enabled by the operator in `plugins.keys` (`GET /api/v1/plugins`), give the instance a unique name, configure its settings, and save the revision.

## Reuse media between tools

Enable the thread-scoped private object library (`{"toolsets": {"object_store": {"enabled": true}}}`) so screenshots and binary tool outputs receive `a13n-object://obj_...` references (`asset_ref`, `asset_store`, `asset_list`, `asset_info`, `asset_delete`) and can pass directly into opted-in base64 MCP tools (`config.object_ref_tools`) via [Object Reference Transport](../a13n-harness/capabilities.md#object-reference-transport) without round-tripping base64 through model tokens.
