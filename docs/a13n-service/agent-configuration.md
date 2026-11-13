---
title: Configure an agent
description: Save revisions, select tools, configure delegation, and extend an agent.
---

Start with an agent that already answers a short message in Console. Use this page to add tools, approvals, delegation or plugins, save a revision, and test the changed behavior before selecting it for other callers.

## Agent configuration

Add capabilities for a concrete task after the first model-only conversation works. The manual editor exposes configuration directly; [Agent Composer](agent-composer.md) helps build it through conversation.

### Set approvals and automatic review

1. Expand a toolset under **Tools** and choose **Allow**, **Ask**, **Automatic review**, or **Deny** for its tools. Connections offer the same choices.
2. For automatic review, open **Configure review**, choose a review model, and set the risk threshold and action: deny or ask a person. Lower-risk calls proceed.
3. Use **Tool review rule** for a tool-specific policy. Advanced settings control shell review instructions, failure handling, timeouts and model parameters.
4. Select **Apply to draft**, then **Save changes**. Cancelling the panel discards its draft edits.

Review requires a model. Defaults deny extra-high risk and ask a person when review fails; review timeouts deny execution.

### Configuration fields

Choose the setting that supplies the capability you need:

| Task                                 | Configuration                                                                                                                                                   |
| ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Select behavior and model            | `model`, `instructions`, `model_settings`, `model_characteristics`; see [Models](models.md)                                                                     |
| Give the agent tools                 | `toolsets`, `connection_tools`, `tool_search`; see [Tools and connections](tools.md)                                                                            |
| Load reusable instructions           | `skills: [{skill_id, revision_id}]`; see [Skills](skills.md)                                                                                                    |
| Ask the application or a person      | `client_tools`, `user_questions`; see [client tools](agent-configuration.md#client-tools-and-questions)                                                         |
| Delegate work                        | `subagents`; see [subagents](agent-configuration.md#subagents)                                                                                                  |
| Review tool calls or interpret media | `reviewer`, `media_understanding`; see [media understanding](models.md#media-understanding)                                                                     |
| Require structured output            | `output_spec`: one JSON Schema or 2–32 named `variants`; omission returns text                                                                                  |
| Bound retries                        | `retries.tools` and `retries.output`, each 0–100                                                                                                                |
| Supply a working environment         | `default_environment_id`, `default_environment_template_id`, `lazy_environment`; see [Environments](environments.md)                                            |
| Retain knowledge                     | `memory`, `memory_mounts`; see [Memory](memory.md)                                                                                                              |
| Tune long-running work               | `background_compaction`, `execution`; see [compaction](agent-configuration.md#context-compaction) and [execution policy](execution-options.md#execution-policy) |
| Extend the runtime                   | `plugins`; see [Console plugins](agent-configuration.md#configure-plugins-in-console)                                                                           |

Saving validates references against the workspace. Omitted skill and subagent revision IDs are pinned to their current defaults. Models, connections, providers and templates remain live resources resolved when used. `lazy_environment` defaults to true: instances prepare on first use. Setting it false prepares them before Harness starts; execution connections still open on first use.

## Context compaction

Service summarizes older conversation context as it grows. Synchronous compaction is the default. To generate summaries in the background, open **Advanced → Context compaction**, enable **Enable background compaction**, and save the revision:

```json
{"background_compaction": {"enabled": true, "blocking_threshold": 0.95}}
```

Ordinary threads can then continue while a summary is generated, using the latest model response's usage to select the mode. At the blocking threshold, the run waits for existing work or compacts synchronously if the compaction trigger is reached. A trigger at or above the blocking threshold leaves no asynchronous interval. Native token counting and explicit output reserves are not required for background progress. A ready summary can apply in this run or a later run of the same thread; disabling new background work still permits retained summaries to apply. Inline and asynchronous subagents compact synchronously.

Summary requests count toward the originating run's request budget, and their usage may arrive after that run ends. Interrupting a run leaves its background summary running. Archiving the thread retires it; forks do not inherit pending summaries.

Context recovery is enabled by default, independently of agent revision compaction settings. After a native context-window overflow, Harness first tries normal compaction. A successful summary lets the run continue with compacted history.

If ordinary execution and applicable compaction both overflow, Service permits one bounded, lossy history reconstruction per run, including worker takeover. It commits the reconstructed history and consumed allowance before dispatch. History files under the primary environment's `/workspace/.a13n/history/<thread-id>/` are best effort and last only as long as that environment. Unavailable storage or readers do not block reconstruction.

> [!WARNING]
> Every lossy reconstruction requires immediate root-cause investigation, even after success. Check compaction configuration and unbounded tool output first. Console retains the warning and outcome separately from the final answer.

## Tool permissions

Use permissions to decide what happens when the model calls a tool:

| Permission | Result                                                          |
| ---------- | --------------------------------------------------------------- |
| `allow`    | Execute the call.                                               |
| `ask`      | End the run waiting for approval.                               |
| `review`   | Let the configured reviewer policy allow, deny or ask a person. |
| `deny`     | Refuse the call.                                                |
| `inherit`  | Use the tool's default; built-in tools default to `allow`.      |

Set built-in and platform permissions at `toolsets.<toolset>.tools.<tool>.permission`. Connections use `connection_tools[].permission` with per-tool `permissions` overrides. Client tools accept `inherit`, `allow` or `deny` at `client_tools[].permission`; allowed client calls still need the application's result.

## Client tools and questions

Declare a client tool when your application owns the operation. For example, add this fragment to the agent's config:

```json
{
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

When called, the run ends `waiting` and exposes the call in `pending.calls`. Execute the operation in your application and [resume with its result](agents-and-runs.md#resume-a-waiting-run).

Set `user_questions: true` to let the model use `ask_user_question`. Questions also appear in `pending.calls`. Answer the specific call through resume; a normal chat message stays in the inbox until the wait is resolved.

## Subagents

Configure `subagents` with workspace agent IDs. Each named edge can provide a description, revision pin, context-sharing choices and usage limits. Replace `ap_...` with a real agent ID in this fragment:

```json
{
  "subagents": {
    "mode": "async",
    "named": {
      "researcher": {
        "agent_id": "ap_...",
        "description": "Finds and summarizes sources.",
        "usage_limits": {
          "request_limit": 20
        },
        "environment": {
          "mode": "shared"
        }
      }
    },
    "dynamic": {
      "enabled": true
    }
  }
}
```

**Inline** is available for named-only policies: children execute inside the parent run and attempt. Their graph must not lead back to the delegating agent. Per-delegation limits can bound requests, tokens and tool calls.

**Async** creates child threads in the same session under the parent run's principal. The parent uses [delegation tools](../a13n-harness/delegation-and-codeact.md#asynchronous-children) to inspect, wait, steer, cancel or continue them. Child results enter the parent inbox and can steer its active run or start a later run. A child waiting for a person appears as `running` to these tools. Async edges apply only `usage_limits.request_limit`.

Async environments follow the edge: `shared` mounts the parent's environments, `dedicated` reserves one from `template_id`, and `none` mounts nothing. The child's own default environment template is unused. Deployment settings `worker.child_count` and `worker.child_depth` bound child count and nesting.

Async is the default. Enable `subagents.dynamic.enabled` in Console or the API to let a root Agent call `spawn_subagent` with task instructions and an initial prompt. Dynamic children share the parent's environments, select only subsets of its capabilities, and cannot delegate. `allowed_models`, `default_model` and `default_thinking` configure model choices; a spawn can choose an allowed model and thinking effort, including `false`.

Dynamic children retain a Thread snapshot, not a published Agent version. Archiving the source Agent blocks new child Runs and continuations but does not stop already accepted Runs. Unarchiving allows continuation of the same snapshot.

Dynamic children inherit the parent Run's request limit, if set, as a per-Run cap. Follow-ups retain that limit, execution settings, configuration and context: omission inherits, while explicit changes are rejected. Forking with the source Agent and no revision retains the snapshot and shared environments; `fresh_environments=true` is rejected. Select a published revision explicitly to fork into ordinary Agent execution.

## Configure Plugins in Console

1. Open the agent's **Capabilities → Plugins** and select **Add plugin**.
2. Choose a deployment-enabled plugin, assign a unique instance name, and edit its settings in the form or JSON editor.
3. Save the agent to validate the draft and publish a revision. Saving validates configuration without executing the plugin.

Multiple instances can use the same plugin. Renaming or removing one updates tool-discovery references. Presets are read-only. If a plugin becomes unavailable, its saved JSON stays visible; restore the plugin or remove the instance before saving.

Administrators install packages and configure `plugins.keys` consistently on Control and workers. The Control catalog at `GET /plugins` lists enabled plugins; worker installation must match it.

## Reuse media between tools

Enable the private object library in the agent revision:

```json
{"toolsets": {"object_store": {"enabled": true}}}
```

A screenshot or other native MCP media result keeps its original media and gains an `a13n-object://obj_...` reference. The agent can pass that exact string to an opted-in base64 consumer. Runtime supplies the original bytes without asking the model to generate base64. The object library belongs to the thread; automatically captured media does not become a workspace Asset. Forks and independent child threads do not inherit another thread's library.

To opt into [Object Reference Transport](../a13n-harness/capabilities.md#object-reference-transport), the Harness tool extension that substitutes references with base64 without replaying argument validation, select tools through the MCP connection's `config.object_ref_tools` rules:

```json
{
  "url": "https://example.com/mcp",
  "object_ref_tools": {"*": false, "*_upload": true, "send_image": true, "text_upload": false}
}
```

Rules match original, case-sensitive server tool names. `*` matches zero or more characters anywhere; other characters are literal. Exact names win, then patterns with more literal characters; equally specific patterns prefer false, independently of entry order. `"*"` sets the default; unmatched tools retain native declarations, defaulting to unsupported. These are the same [selection rules used by native toolsets](../a13n-harness/capabilities.md#object-reference-transport) and Harness UI. Use this opt-in only for string arguments accepting base64. Connection permissions and approvals still apply. Approval requests and history keep references; the actual target resolves them after permission checks. Expiry during an approval wait produces an unavailable-reference error, not automatic producer replay.

The tools `asset_ref`, `asset_store`, `asset_list`, `asset_info` and `asset_delete` import authorized Environment files and manage the private catalog. `asset_store(ref=...)` promotes an available object without changing its ID. Stored objects survive temporary expiry and LRU eviction until explicitly deleted.

Operators configure temporary retention with `A13N_OBJECT_STORE__TEMPORARY_TTL_SECONDS` (86400), `A13N_OBJECT_STORE__MAX_TEMPORARY_OBJECTS` (32) and `A13N_OBJECT_STORE__MAX_TEMPORARY_BYTES` (67108864), per thread. Reads refresh idle TTL; list/info do not. Background sweeps reclaim expired objects even in inactive threads. The existing object backend per-object byte limit also applies. The toolset's `config.capture_media` and `config.max_object_bytes` configure Harness capture/import behavior independently of Host retention. See [Object Store](../a13n-harness/capabilities.md#reuse-original-media-with-object-store) for the shared transport contract.
