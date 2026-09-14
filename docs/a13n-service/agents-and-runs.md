# Agents, Threads, and Runs

Service stores Agent definitions and accepts durable work. A client submits an input, receives an acceptance receipt, then observes the Run. This is different from calling `ExecutableAgent.run()` in the process-local Harness SDK.

## Prerequisites

You need a running [configured Service](configuration.md), a Workspace credential with the required permissions, and an enabled [Model](models.md) accessible to that Workspace. These examples use Native HTTP and may invoke a billable model; they are not offline tests.

Set `SERVICE_URL` to the server origin, `WORKSPACE` to the allowed Workspace ID/key, and `A13N_API_KEY` to a valid application key. Do not combine the key with a browser cookie. Use new idempotency keys for distinct intentions and the same key/body only to reconcile the original command.

## Create an Agent

Save the following as `agent.json`, replacing `primary` with an existing configured Model key:

```json
{
  "key": "docs-reviewer",
  "name": "Documentation reviewer",
  "config": {
    "model": {"model_key": "primary"},
    "instructions": "Review the supplied text. Explain concrete corrections.",
    "input_adapter": {"adapter_key": "native"},
    "protocol": {"public_name": "Documentation reviewer"}
  }
}
```

```bash
curl --fail-with-body "$SERVICE_URL/api/v1/workspaces/$WORKSPACE/agents" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: docs-create-agent-001' \
  --data-binary @agent.json
```

The `201` representation contains `agent` and `revision`. Save their returned IDs; do not fabricate IDs from the display name. The input adapter is the installed `native` adapter. This minimal Agent requires no Environment or external tools; its prompt should contain the material to review.

## Configure tool permissions and a reviewer

Each built-in Tool owns its permission in `toolsets`. Authored permissions default to persisted `inherit`; replace the reviewer placeholder with a real managed Model **ID**, not a Model key or provider route:

```json
{
  "toolsets": {
    "shell": {
      "enabled": true,
      "tools": {
        "exec": {"enabled": true, "permission": "review", "config": {}},
        "signal": {"enabled": true, "permission": "ask", "config": {}}
      }
    }
  },
  "reviewer": {
    "model": "REVIEW_MODEL_ID",
    "instruction": "Treat private customer data exports as high risk.",
    "shell_instruction": "Treat irreversible shell operations as extra-high risk.",
    "risk_threshold": "extra_high",
    "on_flagged": "deny",
    "rules": {
      "tool/reporting/*": {"risk_threshold": "high", "on_flagged": "approval_required"}
    },
    "timeout_seconds": 30,
    "on_error": "approval_required"
  }
}
```

Use actual stable tool IDs from the prepared surface, not display-name guesses. `inherit` uses the Tool's declared default, which is `allow` unless explicitly overridden by trusted Tool code. A reviewer or risk rule alone does not enable review; `allow` continues, `deny` blocks, `ask` requests human approval, and `review` consults the matching reviewer when configured. Without a matching reviewer, review adds no restriction. These settings never widen Service IAM or Environment authority. Code-first Harness consumers can still use stable-ID selectors; Service built-ins keep exact permission ownership in their Tool entries.

Run acceptance freezes configured reviewer Model execution settings alongside the main Model. Later Model edits do not change an accepted Run; credentials still resolve through current managed authentication. In `config_override`, each supplied Toolset entry replaces that whole entry while omitted entries inherit. Reviewer omission inherits and null clears it. A reviewer approval may be followed by a separate tool-policy approval through the normal waiting/feedback flow.

Review usage enters the existing accounting records with tool/call correlation. The stream exposes `tool_review_result` custom events with a completed risk/reason/usage result and independently computed decision, or a safe error code and effective decision. Treat those as observations, not another billable record or proof of execution.

For first-party Web access, each selected operation has independent `allow_domains` and `deny_domains`. A bare host includes its apex and subdomains; wildcard syntax is invalid and deny wins. Search always filters returned result URLs locally. Fetch and download check every redirect, while restricted Provider scrape requires explicit adapter support. These settings do not sandbox shell or remote MCP network access.

## Accept a Run

Save as `run.json`, replacing `AGENT_ID_FROM_CREATE` with `agent.id`:

```json
{
  "agent_id": "AGENT_ID_FROM_CREATE",
  "input": {
    "schema_version": "2",
    "content": [
      {"type": "text", "text": "Review this sentence: The application saves no configuration."}
    ]
  }
}
```

```bash
curl --fail-with-body "$SERVICE_URL/api/v1/workspaces/$WORKSPACE/runs" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: docs-start-run-001' \
  --data-binary @run.json
```

`202` returns an acceptance receipt with `session_id`, `thread_id`, `thread_version`, `run_id`, and `run_version`. Its `status` is `accepted`, **not completed**. The client does not hold a database transaction or worker connection open while the Run executes.

To pin an Agent revision, supply `agent_revision_id`. The optional `expected_current_revision_id` guards a current-head assumption. Read current state before choosing those preconditions; pinning and asking for the latest head are different intentions.

## Observe the result

Set `RUN_ID` from the receipt:

```bash
curl --fail-with-body "$SERVICE_URL/api/v1/runs/$RUN_ID" \
  -H "Authorization: Bearer $A13N_API_KEY"

curl --fail-with-body "$SERVICE_URL/api/v1/runs/$RUN_ID/items" \
  -H "Authorization: Bearer $A13N_API_KEY"

curl --no-buffer --fail-with-body "$SERVICE_URL/api/v1/runs/$RUN_ID/stream" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H 'Accept: text/event-stream'
```

Use returned representations and their documented pagination; the examples do not assume a universal `data` envelope. Streaming is optional. A disconnected stream does not stop the accepted Run, and streamed partial text is not a durable successful outcome. [Streams and events](streams-and-events.md) explains replay gaps and reconnects.

## Understand the four objects

| Object                 | Purpose                                                         | Not equivalent to                                 |
| ---------------------- | --------------------------------------------------------------- | ------------------------------------------------- |
| Agent / Agent revision | Managed definition and immutable authored configuration history | A live Python Agent or provider credential        |
| Session                | Product-level grouping of conversation activity                 | A model HTTP connection                           |
| Thread                 | One advancing conversation/continuation branch                  | A single model request                            |
| Run                    | Accepted durable work and its frozen execution selection        | A transient observation stream                    |
| RunAttempt             | One worker execution/recovery attempt for a Run                 | A new user request or a separate permission grant |

Run status is `accepted`, `running`, `waiting`, `completed`, `failed`, or `cancelled`. A Run can have more than one Attempt. Read `/runs/{run_id}/attempts` for execution evidence rather than assuming one worker attempt per acceptance.

## Continue an existing Thread

`POST /workspaces/{workspace}/threads` can create an independent Thread, optionally selecting an Agent, Session, or Environment. `POST /threads/{thread_id}/runs` submits later input with **`expected_thread_version`**. It can also carry supported Agent/revision/config/Environment selectors.

The version prevents silently advancing a branch that changed after the caller read it. An accepted receipt returns the new correlation/version values. Follow the [exact request schema](api-reference.md#protocol-gateway), not the root-Run example with a guessed Thread field added.

## Waiting, steering, interruption, and successors

| Intent                           | Native operation                       | Important distinction                                           |
| -------------------------------- | -------------------------------------- | --------------------------------------------------------------- |
| Inspect unresolved decisions     | `GET /runs/{run_id}/pending-actions`   | Reading does not approve or execute anything                    |
| Supply waiting results/approvals | `POST /runs/{run_id}/feedback`         | Use the exact pending contract and current preconditions        |
| Add guidance to active work      | `POST /runs/{run_id}/steer`            | Admission and consumption are separate observations             |
| Inspect guidance status          | `GET /runs/{run_id}/steers/{steer_id}` | Do not resend blindly after an uncertain acknowledgement        |
| Interrupt work                   | `POST /runs/{run_id}/interrupt`        | Not rollback of completed side effects                          |
| Continue a source Run            | `POST /runs/{source_run_id}/continue`  | Creates an authorized successor under the continuation contract |
| Retry                            | `POST /runs/{run_id}/retry`            | Not an HTTP client's automatic retry of a mutation              |
| Fork                             | `POST /runs/{run_id}/fork`             | Creates separate lineage rather than overwriting history        |
| Inspect lineage                  | `GET /runs/{run_id}/lineage`           | IDs describe relationships, not access rights                   |

These command bodies differ. They carry the required version/digest/decision information and idempotency headers defined by their schemas. A normal prompt cannot be substituted for a pending approval or client-tool result. Preserve omitted/null semantics in [Run overrides](http-contracts.md#omitted-null-and-empty-values).

Thread queued-submission APIs also support reading, editing, deleting, reordering, and consuming admitted input. Queue order is durable product state, not a guarantee that client arrival order wins every race. Use the versions and operations in the [Native reference](api-reference.md#protocol-gateway), not local list mutation.

## Definition configuration and capture

Service `AgentConfig` contains Model selection, instructions, input adapter, protocol projection, optional output/retry policy, Plugins, Skills, external/client tools, secret requirements, asset publication, and named subagents. It is not Harness UI's YAML resource or the raw Harness `AgentSpec`.

Model defaults, Agent settings, and Run overrides merge at their documented top-level boundary. Acceptance freezes execution selection and child graph. Current authorization and credentials are still rechecked at execution/dispatch; a captured ID or snapshot is not restored authority.

Agent revisions are immutable. Updating, restoring, duplicating, enabling/disabling, and other lifecycle operations have their own preconditions; do not edit a historical revision or assume every configuration resource has revision history. Models and Model Providers, for example, are mutable versioned resources without Agent-style revisions.

### Hosted subagents

`subagent_mode` is `inline` by default or `async`. Both select from the named `subagents` graph. Inline children run inside the parent Attempt and borrow its Environment. Async children are independently scheduled Runs. Their own captured definitions control further delegation; native ingress context is not inherited.

A control-capable process reconciles sealed child results, configured cancellation, and eligible parent successors. Worker execution alone is insufficient for that maintenance. See [background ownership](background-tasks.md).

## Next steps

[Manage resources](resources.md), [select external tools](external-tools.md), [use a language SDK](sdks.md), or inspect the [complete Native API](api-reference.md). The companion `a13n-service-cli` currently has help/version only; it cannot execute this workflow.
