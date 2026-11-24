---
title: 受管工具与调用策略
sidebarTitle: 受管工具与策略
description: 编写带资源元数据的受管工具，按运行强制执行 allow/ask/deny 权限，并在执行前接入自动化 LLM 风险审查。
---

在宿主进程内结合两层互补控制来治理本地工具调用：

- **`ToolPermissionsCapability`：** 统一配置普通函数工具、MCP 工具与受管工具的权限模式（`allow`、`ask`、`deny`、`review`、`inherit`）。
- **`InvocationPolicyCapability`：** 按次评估 `HarnessTool` 的宿主授权、规范化资源访问与凭据租约。

## 编写并授权 `HarnessTool`

在 `HarnessToolMetadata` 中声明副作用、幂等性与输出上限，并在每次 Run 中通过 `RunBindings` 挂载 `InvocationPolicyCapability` 评估器：

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

评估器返回 `InvocationPolicyDecision.allow()`、`.deny(reason)` 或 `.require_approval(reason, metadata=...)`。还可在 `HarnessToolMetadata` 上配置 `resource_resolver`，并在 `InvocationPolicyCapability` 上挂载 `credential_broker` / `grant_broker`。

## 选择工具权限与自动风险审查

在构建器中挂载 `ToolPermissionsCapability`，按稳定工具 ID（`HarnessToolMetadata.tool_id`、`tool/<toolset-id>/<name>` 或 `mcp/<source-id>/<name>`）匹配规则：

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

| 模式      | 效果                                                                |
| --------- | ------------------------------------------------------------------- |
| `inherit` | 保留工具声明的默认行为与原生 `requires_approval` 设置               |
| `allow`   | 直接执行，不触发权限审批或风险审查                                  |
| `deny`    | 在资源解析前拒绝（抛出带 JSON `error.code` 的 `ToolFailed`）        |
| `ask`     | 挂起当前 Run 并生成延迟审批请求（`deferred.approvals`）             |
| `review`  | 由审查模型或自定义 `review()` 评估风险（`low`..`extra_high`）后决策 |

## 处理审批与输出上限

- **人工审批：** 工具内部也可读取 `ctx.deps.tool_approval` 或抛出 `ApprovalRequired`。挂起后使用 `DeferredToolResume(first.deferred, first.deferred.build_results(approvals={call_id: True}))` 恢复运行（见[人机协作](human-in-the-loop.md)）。
- **输出截断与落盘（`ToolOutputPolicy`）：** 支持 `overflow="spill"`（默认，在存在可写环境挂载时将脱敏后的完整输出写入 `.a13n/tmp/tool-results/`）、`"truncate"` 或 `"fail"`。

### 无人值守运行

当运行在没有人工审批或问答的环境中执行时，传入 `RunBindings.embedded(unattended=True)`（或 `RunBindings(unattended=True)`）。无人值守模式会自动设置 `deferred_tools_supported=False`，传播到内联子智能体，并在直接调用、`ToolSearch` 与 `CodeAct` 中过滤工具面：

- 自定义工具需为非延迟定义、声明 `metadata={"a13n.harness.unattended": True}`（`UNATTENDED_TOOL_KEY`），且权限解析为 `allow`（且未启用 `InvocationPolicyCapability`）才会被准入。
- 配置为 `review` 权限的 `environment.shell_exec` 仍可使用；当审查器判定需要人工审批时，该次工具调用直接失败。
- 除 `ToolSearchTool` 外的提供商侧原生工具会被排除，任何意外触发的延迟调用都会直接失败而不会挂起运行。
