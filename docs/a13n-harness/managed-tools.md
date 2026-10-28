---
title: Managed tools and invocation policy
sidebarTitle: Managed tools and policy
description: Author a managed tool, apply current Run authorization, and add permissions, review, and approval when the workflow needs them.
---

Use a managed tool when your application needs to authorize an operation using its effects, resources, and current Run identity. Add tool permissions to choose which local calls run, require approval, or receive a risk review.

The two controls have different jobs: `ToolPermissionsCapability` selects the Agent's permission strategy; `InvocationPolicyCapability` supplies fresh Host authorization for managed calls. Both operate inside the Host process. Use an isolated [Environment provider](environments.md) for operating-system isolation; trusted Python tools retain process authority. External execution follows the separate [Client tools](client-tools.md) workflow.

## Author and authorize a tool

Start with a read-only operation. In the Python environment from [Getting started](getting-started.md), save this complete offline program as `managed_tools_demo.py`. `TestModel` calls the tool without an account or network request.

```python title="managed_tools_demo.py"
import asyncio
import json

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


async def main() -> None:
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
    output = json.loads(result.output_or_raise())
    assert output["application_status"] == "ready"
    print(output["application_status"])


if __name__ == "__main__":
    asyncio.run(main())
```

Run it:

```bash
uv run python managed_tools_demo.py
```

The program prints `ready`. Follow the call in three steps:

1. `HarnessToolMetadata` declares the stable identity, read effect, retry semantics, and output bound.
2. `read_policy` checks that declaration before dispatch. The policy is attached through fresh `RunBindings`, so an application can choose current authorization for each Run.
3. The tool returns its result through the managed output policy. The assertion checks the value received by the Model.

Keep the tool definition reusable. Put current resource access and credentials in the Run binding. Managed metadata alone uses a default allow policy with no dispatch retries; attach an evaluator when your application needs restrictions.

## Select tool permissions

Add one `ToolPermissionsCapability` to the builder's `capabilities` tuple alongside your tool Capability. It applies to ordinary local function tools as well as managed tools. This configuration illustrates several selectors; only tools actually installed in the Agent are affected:

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

Choose a mode for the outcome you need:

| Mode      | What happens                                                     |
| --------- | ---------------------------------------------------------------- |
| `inherit` | Keep the tool's declared default and native approval declaration |
| `allow`   | Continue without permission approval or risk review              |
| `deny`    | Fail before custom validation or resource resolution             |
| `ask`     | Request native structured approval for this call                 |
| `review`  | Ask the matching reviewer, then apply its risk policy            |

Rules match stable IDs. Managed tools use `HarnessToolMetadata.tool_id`; ordinary tools use `tool/<toolset-id>/<original-name>`; local MCP uses `mcp/<source-id>/<original-name>`. Segments are percent-encoded. Renaming, ToolSearch, and CodeAct preserve identity. Use `ToolIdentityToolset` before a custom naming wrapper that cannot preserve it.

The best match wins: exact ID, longest `.*` or `/*` prefix, `*`, then `default`. Tools default to `allow` without review unless trusted code declares another default. Installing a reviewer or risk rules alone does not select `review`. With no matching reviewer, `review` continues without an added restriction.

An explicit mode overrides native `requires_approval=True`. For example, `allow` skips declaration-driven approval, while `review` follows the reviewer policy. To preserve one plugin tool's native declaration under a broad override, give that tool an `inherit` rule. Omitting the Capability also preserves native declarations. Tool bodies, validators, and wrappers can still raise `ApprovalRequired` at runtime; automatic allow never sets the human-approved flag.

All modes retain managed authorization and Host/Environment checks. Provider-native and external client tools have separate execution owners. If `web.search` needs a non-allow permission, select Host Web search; active provider-native search with that policy fails explicitly.

### Configure or replace the reviewer

Select `review` for the intended tools and configure the same permissions Capability. This construction uses a logical `review-model`; your Host must resolve it to an available Model with credentials. Add the resulting `permissions` to the build in place of the earlier permissions object:

```python
from a13n_harness.tools import ToolPermissions, ToolPermissionsCapability

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

The model-backed reviewer is a separate Agent with no execution tools. It receives redacted call arguments, the original schema, and bounded task context. `instruction` supplies general criteria; `shell_instruction` replaces those criteria for shell calls.

A reviewer returns a risk assessment with an optional reason. The runtime chooses the action. Risk order is `low < medium < high < extra_high`; at or above the threshold, `on_flagged` applies. Global defaults are `extra_high` and `deny`. One best rule wins using the same selector precedence; omitted fields inherit global values, not a broader rule.

For an application-owned assessment, implement `review()` instead of making another Model request. This example flags every matching reporting call as high risk and requests confirmation:

```python
from a13n_harness.tools import ToolPermissions, ToolPermissionsCapability
from a13n_harness.capabilities import ToolReviewPolicy, ToolRiskLevel

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

Use this object instead of the model-backed configuration. The Agent still needs a reporting tool whose stable ID matches `tool/reporting/*`. The class supplies an assessment; permission selection and approval remain runtime decisions.

Review context targets 16 KiB and has a 64 KiB hard limit. The current redacted arguments and schema stay intact; optional XML blocks are omitted as whole blocks. Context includes task/correction text, passive Environment information, at most five earlier reviews, and eight recent actions. Saved State retains at most 48 flat evidence records, without full arguments or results. Those records distinguish assessments, human denials, and observed dispatch outcomes; they inform a fresh review rather than reuse an approval.

Read review costs from the existing usage ledger under `source="tool.review"`, correlated by tool and call IDs. The model-backed reviewer is priced under its own Model selection. Custom reviewers can return receipts in `ToolReviewResult.usage` or preserve known receipts in `ToolReviewError`. Tool events with `payload.type="tool_review_result"` expose the redacted assessment and runtime `decision`; errors expose a safe code, effective decision, and `result=null`. These events observe already-accounted usage. Missing reviewers produce neither a review call nor a result event.

### Read the native approval decision

A tool can inspect `ctx.deps.tool_approval` to require business confirmation. The accessor is call-local and read-only: it contains immutable `tool_id`, `tool_call_id`, and `approved`, or is `None` outside a call. Concurrent calls have separate contexts.

Save this second complete program as `approval_demo.py`. It demonstrates suspension and resume with a fixed demo approval. The function returns a confirmation string and sends no report:

```python title="approval_demo.py"
import asyncio
import json

from a13n_harness import AgentContext, AgentSpec, DeferredToolResume, HarnessBuilder
from pydantic_ai import RunContext
from pydantic_ai.capabilities import Capability
from pydantic_ai.exceptions import ApprovalRequired
from pydantic_ai.models.test import TestModel


async def export_report(ctx: RunContext[AgentContext]) -> str:
    """Confirm a demonstration export without sending data."""
    approval = ctx.deps.tool_approval
    if approval is None or not approval.approved:
        raise ApprovalRequired(metadata={"reason": "Confirm report export"})
    return "Export confirmed"


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        model=TestModel(),
        output_type=str,
        capabilities=(Capability(id="reporting", tools=[export_report]),),
    )
    first = await executable.run("Confirm the demonstration export")
    assert first.status == "suspended"
    assert first.state is not None and first.deferred is not None
    request = first.deferred.approvals[0].target_call
    assert request.tool_name == "export_report"
    print("Waiting for approval")

    # A fixed approval for this offline demo; collect the real user's decision in a Host.
    answers = first.deferred.build_results(approvals={request.tool_call_id: True})
    second = await executable.run(
        previous_state=first.state,
        deferred_resume=DeferredToolResume(first.deferred, answers),
    )
    output = json.loads(second.output_or_raise())
    assert output["export_report"] == "Export confirmed"
    print(output["export_report"])


if __name__ == "__main__":
    asyncio.run(main())
```

Run `uv run python approval_demo.py`. It prints `Waiting for approval`, then `Export confirmed`. The first Run closes with a pending approval; the second supplies a structured decision against the target call ID.

In an application, replace the fixed `True` with the decision collected from the authorized person. Persist the pending request with its State and supply fresh bindings when resuming; follow [State and resume](state-and-resume.md#structured-suspension). A native approval covers the whole call, including permission, review, managed policy, and business confirmation. Current review and authorization still run again: a fresh denial or review timeout can block dispatch. Argument overrides are checked against the current schema. The Host owns the provenance of history and decisions; Harness validates their structure and correlation.

## Describe the real effect

`HarnessTool` accepts ordinary native tool options plus required managed metadata. Choose values that describe what the function actually does:

| Field                    | Responsibility                                                                 |
| ------------------------ | ------------------------------------------------------------------------------ |
| `tool_id`                | Stable tool identity, distinct from a model-facing name                        |
| `effects`                | Nonempty set of `read`, `write`, `delete`, `execute`, `external_communication` |
| `credential_audiences`   | Exact audiences for credentials needed at dispatch; at most 16                 |
| `idempotency`            | `none`, `read_only`, or supported `provider_key` semantics                     |
| `output_policy`          | Finite inline/total output bounds and overflow/redaction behavior              |
| `resource_resolver`      | Optional async resolution of validated arguments to canonical resources        |
| `superseded_by_tool_ids` | Explicit replacement identities used in surface resolution                     |

A write or external send must not be labeled `read_only`. Use `provider_key` only when the provider implements the declared idempotency semantics.

For resource-specific authorization, add an async `ToolResourceResolver(arguments, *, context)`. It receives validated typed arguments and returns `CanonicalResource(namespace, kind, identifier)` values under current Host authority. The evaluator then decides against those resources. A resolver failure stops the call before authorization and dispatch.

## Evaluate current authority

An `InvocationPolicyEvaluator` receives the current `AgentContext`, tool metadata, and a `ToolInvocationContext`. The latter carries instance/Run identity, call correlation, normalized JSON arguments and digest, canonical resources, and optional idempotency key and deadline.

Return the outcome for this prepared call:

- `InvocationPolicyDecision.allow()` to continue.
- `InvocationPolicyDecision.deny(reason)` to stop dispatch.
- `InvocationPolicyDecision.require_approval(reason, metadata=...)` to request confirmation.

Use display-safe reasons and metadata. Reasons, when supplied, are nonblank and at most 512 characters; approval metadata is finite JSON bounded to 16 KiB. Evaluate each Run against current authority rather than treating an earlier decision as a permanent grant.

Attach `InvocationPolicyCapability` only through fresh `RunBindings.capabilities`. It requires an evaluator and accepts optional `credential_broker` and `grant_broker`. `strict_managed_tools=False` allows ordinary tools alongside managed ones; set it to true to reject an unmanaged tool surface. This selects the callable surface, not the authority of Python already running in the process. `max_dispatch_retries` defaults to 1 and accepts 0–3; the retry rules below still apply.

## Credentials and grants

Keep business credentials out of model arguments. After authorization and approval, Harness asks `CredentialBroker.acquire(audience, invocation, *, context)` for each declared audience. Return a `CredentialLease` with an opaque value and optional async close callback. An adapter reads these task-local values with `current_invocation_scope()` and uses them for the actual provider call.

If the provider also needs a verifiable authorization grant, implement `InvocationGrantBroker.issue(...)`. It can return an `InvocationGrantRef` containing an opaque grant ID, audience, claims digest, and timezone-aware expiry. The Host/provider owns signing, transport, and verification; storing the reference does not establish authority.

Harness closes credential leases after each invocation. The Host closes any separately owned clients. Keep credential values out of prompts, results, observations, and saved State.

## Approvals and uncertain outcomes

For approval, use the two-Run flow above. Resources and current policy are resolved again before dispatch; approval does not reserve an Environment target. [Built-in inline children](delegation-and-codeact.md#host-managed-feedback) disable deferred tools, so choose a Host-owned child lifecycle when a child needs human feedback.

For retries, distinguish a known provider failure from an uncertain effect. Managed dispatch retries only a retryable, known-outcome provider failure when the tool declares `read_only` or supported `provider_key` semantics and the retry budget permits it. Timeout, cancellation, or unknown provider outcome requires reconciliation. Inspect the provider's result or the Host's dispatch record before repeating a write.

## Bound tool output

Set `ToolOutputPolicy` for the result the Model needs. `max_inline_bytes` accepts 512–262,144; `max_output_bytes` is positive, at most 512 MiB, and at least the inline limit. The final text/JSON boundary also has a 20,000-character hard ceiling. `redact=True` is the default and applies to result text/JSON, not arbitrary logs or callbacks.

Choose overflow behavior deliberately:

| `overflow`        | Result when inline output is too large                                                                    |
| ----------------- | --------------------------------------------------------------------------------------------------------- |
| `fail`            | A bounded tool failure                                                                                    |
| `truncate`        | An explicit bounded preview                                                                               |
| `spill` (default) | A bounded preview and, when storage and the total limit permit, a file containing the full redacted value |

Spill files require current Environment write access. By default they go in a Run-private directory beneath the default mount's `.a13n/tmp/tool-results/`. Set an absolute `RunBindings.tool_result_directory` to choose another sink. If the sink is unavailable, the preview has no file path. Harness attempts to remove its own Run-private directories during cleanup; save durable artifacts elsewhere.

Output failure or truncation occurs after the tool may have acted. It does not undo the effect. See [Environment tools](environments.md) for file access and [Host embedding](hosting.md) for retained execution and result ownership.
