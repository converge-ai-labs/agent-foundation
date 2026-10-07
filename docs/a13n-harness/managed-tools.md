---
title: Managed tools and invocation policy
sidebarTitle: Managed tools and policy
description: Set permissions and review for every local tool, and give Host-managed tools one boundary for authorization, credentials, approvals, and bounded output.
---

Use managed tools when your Host needs shared authorization, credentials, approvals, and output limits. Ordinary Python tools retain the process's authority.

This is not an operating-system sandbox. Provider isolation and Host access policy remain separate. Client-executed declarations belong in [Client-side tools](client-tools.md).

## Select tool permissions

Permissions work for ordinary local Pydantic tools as well as managed tools. Add `ToolPermissionsCapability` to the definition; keep fresh resource authorization in `InvocationPolicyCapability`:

```python
from a13n_harness.tools import ToolPermissions, ToolPermissionsCapability

permissions = ToolPermissionsCapability(
    ToolPermissions(
        default="inherit",
        rules={
            "environment.shell_exec": "review",
            "filesystem.remove": "ask",
            "tool/reporting/*": "allow",
            "mcp/untrusted-source/*": "deny",
        },
    )
)
```

Match stable IDs: managed tools use their declared IDs, ordinary tools use `tool/<toolset-id>/<original-name>`, and local MCP uses `mcp/<source-id>/<original-name>`. Segments are percent-encoded; renaming, ToolProxy, and CodeAct preserve these IDs. Custom naming wrappers can use `ToolIdentityToolset`.

Exact rules win over the longest `.*` or `/*` prefix, then `*`, then `default`. `inherit` uses the tool default, which is `allow` without review unless trusted code explicitly declares otherwise. External/provider-native tools retain their separate boundaries. A reviewer and its risk rules do not enable review by themselves; select permission `review` for the tools you want assessed. `allow` continues, `deny` fails before custom validation, `ask` requests human approval, and `review` consults a matching reviewer. If no reviewer is configured or matches, review adds no restriction. None of these modes supplies credentials or bypasses Host/Environment policy.

### Configure or replace the reviewer

The same `ToolPermissionsCapability` owns optional review configuration, reviewer selection, and the per-Run review lifecycle. Its model-backed reviewer uses a separate Agent with no execution tools:

```python
from a13n_harness.capabilities import (
    ToolReviewConfig,
    ToolReviewPolicy,
    ToolReviewRule,
    ToolRiskLevel,
)

permissions = ToolPermissionsCapability(
    ToolPermissions(rules={"environment.shell_exec": "review", "tool/reporting/*": "review"}),
    review=ToolReviewConfig(
        model="review-model",
        instruction="Treat private customer data exports as high risk.",
        shell_instruction="Treat irreversible shell operations as extra-high risk.",
        risk_threshold=ToolRiskLevel.EXTRA_HIGH,
        on_flagged="deny",
        rules={
            "tool/reporting/*": ToolReviewRule(
                risk_threshold=ToolRiskLevel.HIGH, on_flagged="approval_required"
            ),
        },
        timeout_seconds=30,
        on_error="approval_required",
    )
)
# Pass this one permissions Capability in HarnessBuilder.build(..., capabilities=(...)).
# Your Host's Run Model resolver resolves the logical "review-model" selection.
```

The reviewer receives redacted tool arguments and task context. `instruction` adds general guidance; shell-specific guidance overrides it for shell calls.

Reviewers return only `risk` and `reason`. Risk order is `low < medium < high < extra_high`; the runtime applies the configured action at or above the threshold. Defaults are global `extra_high` and `deny`. One best rule wins: exact ID, longest prefix, then `*`; missing fields inherit global values, not broader rules. `ToolReviewConfig` contains this policy; code-first reviewers may instead use `ToolReviewPolicy`.

Each review request renders the redacted current call and original schema intact as XML, targeting 16 KiB with a 64 KiB hard limit. Optional blocks are omitted explicitly, never by truncating the current operation. Task/correction text, passive Environment information, up to five earlier reviews, and eight recent actions supply bounded context. At most 48 flat evidence records travel with saved Harness state across Runs; they contain no full arguments or results. Human denials and observed dispatch outcomes are distinguished from assessments. History is advisory, not reusable approval or proof of successful external effects.

To implement a trusted reviewer without another model request:

```python
from a13n_harness import AgentContext
from a13n_harness.capabilities import (
    ToolReviewAssessment,
    ToolReviewRequest,
    ToolReviewResult,
)


class ExportReviewer:
    async def review(
        self, request: ToolReviewRequest, *, context: AgentContext
    ) -> ToolReviewResult:
        return ToolReviewResult(
            assessment=ToolReviewAssessment(
                risk="high",
                reason="Confirm the export destination before sending data.",
            ),
        )


permissions = ToolPermissionsCapability(
    ToolPermissions(rules={"tool/reporting/*": "review"}),
    reviewers={"tool/reporting/*": ExportReviewer()},
    policy=ToolReviewPolicy(risk_threshold=ToolRiskLevel.HIGH, on_flagged="approval_required"),
)
```

The default model reviewer's request is recorded like the Agent's own model requests. It produces a model usage record with source `tool.review` and the tool and call IDs. The Agent's model-cost policy prices it under the reviewer's `model`. A custom reviewer can return provider usage receipts in `ToolReviewResult.usage` or preserve proven receipts in `ToolReviewError`. The shared gate records them in the existing ledger with source `tool.review` and tool/call IDs for all tools, including shell. Completed `HarnessExtensionEvent(kind="tool")` events with `payload.type="tool_review_result"` expose the redacted result, including risk/reason and usage, plus a separate runtime-computed `decision`. Errors expose a safe code and effective decision with `result=null`. Do not account the event receipts a second time. Missing reviewers produce neither a review call nor a result event.

### Read the native approval decision

`ToolApprovalContext` is available through the read-only `AgentContext.tool_approval` accessor. It is `None` outside the active call, isolated between concurrent calls, and exposes immutable `tool_id`, `tool_call_id`, and `approved`. Automatic `allow` decisions never set `approved`. A tool can request confirmation using the native decision:

```python
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ApprovalRequired


def export_report(ctx: RunContext[AgentContext]) -> str:
    approval = ctx.deps.tool_approval
    if approval is None or not approval.approved:
        raise ApprovalRequired(metadata={"reason": "Confirm report export"})
    return "Export confirmed"
```

A structured native approval applies to the whole call, without separate reviewer, permission, or tool approval stages. The Host may construct legal history and native results; Harness does not authenticate their provenance or require historical resource/schema/argument proofs. Review and current policy run again on resume, so a fresh denial or review timeout still blocks execution. Native argument overrides are validated against the current schema.

Provider-native tools and external client tools are not local permission-gate calls. Select Host Web search if `web.search` needs a non-allow permission; an active provider-native search tool with that policy fails explicitly instead of claiming enforcement.

## Author and authorize a tool

This complete offline example wraps a read-only function in managed metadata and supplies a fresh Run policy. `TestModel` exercises the tool without an API key:

```python
import asyncio

from a13n_harness import HarnessBuilder, RunBindings
from a13n_harness.tools import (
    HarnessTool,
    HarnessToolMetadata,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    ToolOutputPolicy,
)
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.models.test import TestModel


def application_status() -> str:
    """Read the application's status."""
    return "ready"


async def read_policy(invocation, metadata, *, context):
    if metadata.effects <= {"read"}:
        return InvocationPolicyDecision.allow()
    return InvocationPolicyDecision.deny("Only reads are allowed in this Run.")


async def main():
    tool = HarnessTool(
        application_status,
        harness_metadata=HarnessToolMetadata(
            tool_id="example.application-status",
            effects=frozenset({"read"}),
            credential_audiences=(),
            idempotency="read_only",
            output_policy=ToolOutputPolicy(
                max_inline_bytes=4096,
                max_output_bytes=4096,
                overflow="fail",
            ),
        ),
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        model=TestModel(),
        output_type=str,
        capabilities=(Capability(tools=[tool], id="application-tools"),),
    )
    result = await executable.run(
        "Read application status",
        bindings=RunBindings.embedded(
            capabilities=(InvocationPolicyCapability(evaluator=read_policy),)
        ),
    )
    assert result.status == "completed"
    print(result.output_or_raise())


asyncio.run(main())
```

Keep stable tool metadata on the definition and current policy in the Run binding.

## Describe the real effect

`HarnessTool` is a thin native `Tool` helper; it accepts normal tool options plus a required `HarnessToolMetadata`. Metadata contains:

| Field                    | Responsibility                                                                 |
| ------------------------ | ------------------------------------------------------------------------------ |
| `tool_id`                | Stable tool identity, distinct from a model-facing name                        |
| `effects`                | Nonempty set of `read`, `write`, `delete`, `execute`, `external_communication` |
| `credential_audiences`   | Exact audiences for credentials needed at dispatch; at most 16                 |
| `idempotency`            | `none`, `read_only`, or supported `provider_key` semantics                     |
| `output_policy`          | Finite inline/total output bounds and overflow/redaction behavior              |
| `resource_resolver`      | Optional async resolution of validated arguments to canonical resources        |
| `superseded_by_tool_ids` | Explicit replacement identities used in surface resolution                     |

Set idempotency to match the real effect; a write or external send is not `read_only`.

`ToolResourceResolver(arguments, *, context)` resolves validated arguments to `CanonicalResource(namespace, kind, identifier)` under current Host authority.

## Evaluate current authority

`InvocationPolicyEvaluator` receives a `ToolInvocationContext`, tool metadata, and the current `AgentContext`. The invocation contains stable correlation, current instance/Run identity, normalized arguments and digest, canonical resources, optional idempotency key, and deadline. Return one of:

- `InvocationPolicyDecision.allow()`.
- `InvocationPolicyDecision.deny(reason)`.
- `InvocationPolicyDecision.require_approval(reason, metadata=...)`.

Reasons are bounded nonblank text when supplied; approval metadata is finite JSON bounded to 16 KiB. Put only information that is safe to show into reasons and approval metadata. A policy decision is about this prepared invocation, not a permanent grant to future Runs.

`InvocationPolicyCapability` requires an evaluator. Its optional collaborators are `credential_broker` and `grant_broker`; `strict_managed_tools` defaults to false and `max_dispatch_retries` defaults to 1 (range 0–3). Attach it through fresh Run bindings, not a serialized definition. Without an attached restrictive policy, do not assume managed metadata alone denies an operation. Strict managed mode rejects tools outside the managed boundary; it does not retroactively isolate Python code already running in the Host.

## Credentials and grants

After authorization, a `CredentialBroker.acquire(audience, invocation, *, context)` can return a `CredentialLease`. The lease carries an opaque value and optional async close callback. First-party adapters access invocation-scoped values through `current_invocation_scope()`; values must not be copied into model arguments, observations, or continuation state.

An `InvocationGrantBroker.issue(...)` can supply an `InvocationGrantRef`: an opaque grant ID, audience, claims digest, and timezone-aware expiry. The Host/provider owns issuance and verification. A serialized reference is not sufficient authority by itself.

Credential leases close after each invocation; the Host closes separately owned clients.

## Approvals and uncertain outcomes

Recheck current resource and policy before dispatch, even after approval. An approval does not reserve an Environment target.

With Host deferred support, approval suspends the Run and resumes through [deferred feedback](state-and-resume.md). [Built-in inline children](delegation-and-codeact.md#host-managed-feedback) disable deferred tools.

Retry only when the outcome and idempotency contract permit it. After a timeout or cancellation leaves a write uncertain, reconcile the provider result first.

## Bound tool output

`ToolOutputPolicy` requires `max_inline_bytes` (512–262,144) and `max_output_bytes` (positive, at most 512 MiB and not below the inline bound). `overflow` is `spill` by default, or `truncate` / `fail`; `redact` defaults to true.

Spilling needs current Environment output access. Redaction applies to managed results, not arbitrary logs or callbacks.

Temporary results use the default mount's `.a13n/tmp/tool-results/`. Override with an absolute `RunBindings.tool_result_directory`. If that directory is unavailable, the result has a bounded preview but no file path. Harness attempts to remove its Run-private subdirectories on cleanup; copy durable outputs elsewhere.

See [Environment tools](environments.md) for canonical resource and model-visible operation integration, and [Host embedding](hosting.md) for durable command ownership outside the process-local boundary.
