---
title: 受管理工具与调用策略
sidebarTitle: 受管理工具与策略
description: 为每个本地工具设置权限与审查，并为 Host 管理的工具统一授权、凭据、审批和输出限制边界。
---

Host 需要统一授权、凭据、审批和输出限制时，使用托管工具。普通 Python 工具保留进程权限。

这不是操作系统沙箱。Provider 隔离和 Host 访问策略仍独立。客户端执行声明见[客户端工具](client-tools.md)。

## 选择工具权限

权限适用于普通本地 Pydantic 工具和受管理工具。在定义中添加 `ToolPermissionsCapability`；当前资源授权放在 `InvocationPolicyCapability`：

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

按稳定 ID 匹配：托管工具使用声明的 ID，普通工具使用 `tool/<toolset-id>/<original-name>`，本地 MCP 使用 `mcp/<source-id>/<original-name>`。段使用百分号编码；重命名、ToolProxy 和 CodeAct 保留 ID。自定义命名包装可使用 `ToolIdentityToolset`。

规则优先级依次为确切匹配、最长 `.*` 或 `/*` 前缀、`*`、`default`。`inherit` 使用工具默认值；可信代码未显式声明时为不审查的 `allow`。外部/提供方原生工具保留独立边界。仅有审查器和风险规则不会开启审查；需为待评估工具选择 `review`。`allow` 继续，`deny` 在自定义验证前失败，`ask` 请求人工审批，`review` 咨询匹配审查器。未配置或没有匹配审查器时，review 不增加限制。所有模式都不提供凭据，也不绕过 Host/Environment 策略。

### 配置或替换审查器

同一个 `ToolPermissionsCapability` 负责可选审查配置、审查器选择和单 Run 审查生命周期。模型审查器使用没有执行工具的独立 Agent：

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

审核器接收脱敏后的工具参数与任务上下文。`instruction` 添加通用指引，shell 专用指引在 shell 调用时覆盖通用指引。

审查器只返回 `risk` 和 `reason`。风险顺序 `low < medium < high < extra_high`；达到或超过阈值时，运行时执行配置动作。全局默认 `extra_high` 和 `deny`。只选最佳规则：确切 ID、最长前缀、`*`；缺失字段继承全局值，不继承更宽规则。`ToolReviewConfig` 包含该策略；代码实现的审查器也可用 `ToolReviewPolicy`。

每个审查请求都以 XML 渲染，完整保留经遮蔽的当前调用和原 schema，目标大小 16 KiB，硬上限 64 KiB。可选块明确省略，绝不截断当前操作。任务/修正文本、被动 Environment 信息、最多五次旧审查和八次近期动作提供有限上下文。最多 48 条扁平证据记录随已保存 Harness 状态跨 Run 延续，不含完整参数或结果。人工拒绝和观测派发结果与评估分开。历史仅供参考，不是可复用审批或外部成功证明。

要实现无需额外模型请求的可信审查器：

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

默认模型审查器的请求与 Agent 自己的模型请求一样记录。它产生一条来源为 `tool.review` 的模型用量记录，包含工具和调用 ID。Agent 的模型费用策略按审查器的 `model` 为其定价。自定义审查器可在 `ToolReviewResult.usage` 返回提供方回执，或在 `ToolReviewError` 保留已证实回执。共享检查在已有账本中记录所有工具（含 shell）的回执，使用 `tool.review` 和工具/调用 ID。已完成的 `HarnessExtensionEvent(kind="tool")`、`payload.type="tool_review_result"` 公开遮蔽结果，含风险/原因和用量，以及运行时单独计算的 `decision`。错误公开安全代码和实际决策，`result=null`。不要再次统计事件回执。缺少审查器不会产生审查调用或结果事件。

### 读取原生审批决策

通过只读 `AgentContext.tool_approval` 访问 `ToolApprovalContext`。当前调用外为 `None`，并发调用间隔离；公开不可变的 `tool_id`、`tool_call_id` 和 `approved`。自动 `allow` 不设置 `approved`。工具可基于原生决策请求确认：

```python
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ApprovalRequired


def export_report(ctx: RunContext[AgentContext]) -> str:
    approval = ctx.deps.tool_approval
    if approval is None or not approval.approved:
        raise ApprovalRequired(metadata={"reason": "Confirm report export"})
    return "Export confirmed"
```

结构化原生审批应用于整个调用，不分别建立审查器、权限和工具审批阶段。Host 可构造合法历史和原生结果；Harness 不验证其来源，也不要求历史资源/schema/参数证明。恢复时重新运行审查和当前策略，新拒绝或审查超时仍阻止执行。原生参数覆盖按当前 schema 验证。

提供方原生工具和外部客户端工具不经过本地权限检查。`web.search` 需要非 allow 权限时，选择 Host Web 搜索；使用该策略的活动提供方原生搜索会明确失败，不虚称已经强制执行。

## 编写并授权工具

完整离线示例为只读函数添加受管理元数据，并提供新的 Run 策略。`TestModel` 无需 API 密钥即可调用工具：

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

稳定工具元数据放在定义中，当前策略放在 Run binding 中。

## 描述真实副作用

`HarnessTool` 是轻量的原生 `Tool` 辅助类，接受普通工具选项和必需 `HarnessToolMetadata`。元数据包含：

| 字段                     | 职责                                                                          |
| ------------------------ | ----------------------------------------------------------------------------- |
| `tool_id`                | 稳定工具身份，与面向模型的名称不同                                            |
| `effects`                | 非空集合，取值 `read`、`write`、`delete`、`execute`、`external_communication` |
| `credential_audiences`   | 派发所需凭据的确切 audience，最多 16 个                                       |
| `idempotency`            | `none`、`read_only` 或支持的 `provider_key` 语义                              |
| `output_policy`          | 有限的内联/总输出上限及溢出/遮蔽行为                                          |
| `resource_resolver`      | 可选异步解析，将已验证参数转为规范资源                                        |
| `superseded_by_tool_ids` | 工具集合解析使用的显式替代身份                                                |

按真实效果设置幂等性；写入或外部发送不是 `read_only`。

`ToolResourceResolver(arguments, *, context)` 在当前 Host 权限下，将已验证参数解析为 `CanonicalResource(namespace, kind, identifier)`。

## 评估当前权限

`InvocationPolicyEvaluator` 接收 `ToolInvocationContext`、工具元数据和当前 `AgentContext`。调用上下文包含稳定关联、当前实例/Run 身份、规范化参数及摘要、规范资源、可选幂等键和截止时间。返回以下之一：

- `InvocationPolicyDecision.allow()`。
- `InvocationPolicyDecision.deny(reason)`。
- `InvocationPolicyDecision.require_approval(reason, metadata=...)`。

提供原因时，必须是有上限的非空文本；审批元数据为有限 JSON，最多 16 KiB。原因和审批元数据中只放可安全展示的信息。决策针对此次准备好的调用，不是未来 Run 的永久授权。

`InvocationPolicyCapability` 要求 evaluator，可选协作者为 `credential_broker` 和 `grant_broker`。`strict_managed_tools` 默认 false，`max_dispatch_retries` 默认 1（0–3）。通过新的 Run 绑定附加，不通过序列化定义。未附加限制策略时，不要认为仅有受管理元数据就会拒绝操作。严格模式拒绝边界外工具，不会追溯隔离已经在 Host 运行的 Python 代码。

## 凭据与授权

授权后，`CredentialBroker.acquire(audience, invocation, *, context)` 可返回 `CredentialLease`，携带不透明值和可选异步关闭回调。第一方 adapter 通过 `current_invocation_scope()` 访问调用范围值；不能将值复制到模型参数、观测或续接状态。

`InvocationGrantBroker.issue(...)` 可提供 `InvocationGrantRef`，包含不透明授权 ID、audience、声明摘要和带时区到期时间。Host/provider 负责签发和验证。仅有序列化引用不足以提供权限。

每次调用后关闭凭据 lease，独立客户端由 Host 关闭。

## 审批与不确定结果

即使已审批，派发前仍重新检查当前资源与策略。审批不预留 Environment 目标。

Host 支持 deferred 时，审批暂停 Run，再通过[延迟反馈](state-and-resume.md)续接。[内置内联子 Agent](delegation-and-codeact.md#host-managed-feedback)不支持 deferred 工具。

仅在结果确定性与幂等约定允许时重试。超时或取消使写入结果不确定时，先核对 provider 结果。

## 限制工具输出

`ToolOutputPolicy` 要求 `max_inline_bytes`（512–262,144）和 `max_output_bytes`（正数，最多 512 MiB，且不得低于内联上限）。`overflow` 默认 `spill`，也可为 `truncate` / `fail`；`redact` 默认 true。

写出溢出结果需要当前 Environment 输出权限。脱敏作用于托管结果，不覆盖任意日志或回调。

临时结果使用默认挂载的 `.a13n/tmp/tool-results/`。可用绝对路径 `RunBindings.tool_result_directory` 覆盖；目录不可用时仅返回有限预览，不返回文件路径。Harness 在清理时尝试移除自身 Run 私有子目录，持久输出应另存。

规范资源和面向模型的操作集成见 [Environment 工具](environments.md)；进程本地边界外的持久命令归属见 [Host 嵌入](hosting.md)。
