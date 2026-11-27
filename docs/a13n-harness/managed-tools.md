---
title: Managed tools and invocation policy
sidebarTitle: Managed tools and policy
description: Author managed tools with resource metadata, enforce per-run allow/ask/deny permissions, and add automated LLM risk review before execution.
---

Combine two complementary controls to govern local tool calls inside the Host process:

- **`ToolPermissionsCapability`:** Sets the Agent's permission mode (`allow`, `ask`, `deny`, `review`, `inherit`) across function tools, MCP tools, and managed tools.
- **`InvocationPolicyCapability`:** Evaluates fresh per-Run Host authorization, canonical resource access, and credential leases for `HarnessTool` definitions.

## Author and authorize a `HarnessTool`

Declare tool effects, idempotency, and output bounds in `HarnessToolMetadata`, then attach an `InvocationPolicyCapability` evaluator through fresh `RunBindings`:

```python title="managed_tools_demo.py"
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


async def main() -> None:
    tool = HarnessTool(
        application_status,
        harness_metadata=HarnessToolMetadata(
            tool_id="example.application-status",
            effects=frozenset({"read"}),
            credential_audiences=(),
            idempotency="read_only",
            output_policy=ToolOutputPolicy(
                max_inline_bytes=4096, max_output_bytes=4096, overflow="fail"
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
    print(result.output_or_raise())


asyncio.run(main())
```

Evaluators return `InvocationPolicyDecision.allow()`, `.deny(reason)`, or `.require_approval(reason, metadata=...)`. Optionally supply `resource_resolver` on `HarnessToolMetadata` and `credential_broker` / `grant_broker` on `InvocationPolicyCapability`.

## Select tool permissions

Attach `ToolPermissionsCapability` on the builder to match stable tool IDs (`HarnessToolMetadata.tool_id`, `tool/<toolset-id>/<name>`, or `mcp/<source-id>/<name>`):

```python
from a13n_harness.capabilities import ToolReviewConfig, ToolReviewRule, ToolRiskLevel
from a13n_harness.tools import ToolPermissions, ToolPermissionsCapability

permissions = ToolPermissionsCapability(
    ToolPermissions(
        default="inherit",
        rules={
            "environment.shell_exec": "review",
            "filesystem.remove": "ask",
            "tool/reporting/*": "review",
            "mcp/untrusted-source/*": "deny",
        },
    ),
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
    ),
)
```

| Mode      | Outcome                                                                  |
| --------- | ------------------------------------------------------------------------ |
| `inherit` | Keep the tool's declared default and native `requires_approval` setting  |
| `allow`   | Run without permission prompt or risk review                             |
| `deny`    | Reject before resource resolution (`ToolFailed` with JSON `error.code`)  |
| `ask`     | Suspend the Run with a deferred approval request (`deferred.approvals`)  |
| `review`  | Score risk (`low`..`extra_high`) via reviewer Model or custom `review()` |

## Handle approvals and bound output

- **Approvals:** Tools can also inspect `ctx.deps.tool_approval` or raise `ApprovalRequired`. Request approval before writing data, sending a request with side effects, or starting paid work. Approval can run the function again. Resume a suspended approval batch with `DeferredToolResume(first.deferred, first.deferred.build_results(approvals={call_id: True}))` (see [Human-in-the-loop](human-in-the-loop.md)).
- **Output bounds (`ToolOutputPolicy`):** Choose `overflow="spill"` (default—writes full redacted output to `.a13n/tmp/tool-results/` when an Environment mount is writable), `"truncate"`, or `"fail"`.

### Unattended Runs

Pass `RunBindings(unattended=True)` or `RunBindings.embedded(unattended=True)` to run without human input. The Run hides external-input tools and tools with an effective `ask` or `deny` permission before exposing them to the model, Tool Search, or CodeAct. Inline children inherit this mode. Other tools, including configured MCP and provider-native tools, remain available under the usual policies. No tool metadata opt-in is needed.

Argument-dependent checks still run. If a Shell review, invocation policy, or other check needs approval, the call fails. Unexpected requests for external input also fail. The Run neither grants approval nor suspends for a response.
