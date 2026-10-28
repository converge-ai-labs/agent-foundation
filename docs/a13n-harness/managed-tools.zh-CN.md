---
title: 受管理工具与调用策略
sidebarTitle: 受管理工具与策略
description: 编写受管理工具，按当前 Run 授权，并按需加入工具权限、风险审查和人工审批。
---

应用需要根据操作效果、资源和当前 Run 身份决定能否执行时，使用受管理工具。再通过工具权限选择哪些本地调用直接运行、请求审批或接受风险审查。

两种控制各有职责：`ToolPermissionsCapability` 选择 Agent 的权限策略；`InvocationPolicyCapability` 为受管理调用提供当前 Host 授权。它们都在 Host 进程内工作。操作系统隔离由具备隔离能力的 [Environment provider](environments.md) 提供；可信 Python 工具仍拥有进程权限。外部执行采用独立的[客户端工具](client-tools.md)流程。

## 编写并授权工具

先从只读操作开始。在[快速开始](getting-started.md)的 Python 环境中，将下面的完整离线程序保存为 `managed_tools_demo.py`。`TestModel` 会调用工具，无需账号或网络请求。

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

运行：

```bash
uv run python managed_tools_demo.py
```

程序打印 `ready`。按三个步骤理解这次调用：

1. `HarnessToolMetadata` 声明稳定身份、读取效果、重试语义和输出上限。
2. `read_policy` 在派发前检查声明。策略通过新的 `RunBindings` 传入，应用可为每个 Run 选择当前授权。
3. 工具结果经过受管理输出策略返回。断言检查 Model 收到的值。

让工具定义保持可复用，把当前资源权限和凭据放在 Run 绑定中。只有受管理元数据时，默认策略为允许且不重试派发；应用需要限制时，显式附加 evaluator。

## 选择工具权限

在 builder 的 `capabilities` 元组中，将一个 `ToolPermissionsCapability` 与工具 Capability 一起传入。它适用于普通本地函数工具和受管理工具。下面展示几种选择器；只有 Agent 已安装的工具会受到影响：

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

按预期行为选择模式：

| 模式      | 行为                               |
| --------- | ---------------------------------- |
| `inherit` | 保留工具声明的默认值和原生审批声明 |
| `allow`   | 无需权限审批或风险审查，继续执行   |
| `deny`    | 在自定义验证或资源解析前失败       |
| `ask`     | 为当前调用请求原生结构化审批       |
| `review`  | 调用匹配的审查器，再应用风险策略   |

规则匹配稳定 ID。受管理工具使用 `HarnessToolMetadata.tool_id`；普通工具使用 `tool/<toolset-id>/<original-name>`；本地 MCP 使用 `mcp/<source-id>/<original-name>`。各段采用百分号编码。重命名、ToolSearch 和 CodeAct 都保留身份。自定义命名 wrapper 无法保留身份时，在它之前使用 `ToolIdentityToolset`。

只采用最佳匹配：确切 ID、最长 `.*` 或 `/*` 前缀、`*`，最后是 `default`。可信代码未声明其他默认值时，工具默认 `allow`，不审查。安装审查器或配置风险规则本身不会选择 `review`。没有匹配审查器时，`review` 不增加限制，调用继续。

显式模式会覆盖原生 `requires_approval=True`。例如，`allow` 跳过声明式审批，`review` 采用审查器策略。要在宽泛覆盖规则下保留某个插件工具的原生声明，为它配置 `inherit`。不添加该 Capability 也会保留原生声明。工具函数、验证器和 wrapper 仍可在运行时抛出 `ApprovalRequired`；自动放行不会设置人工已批准标记。

所有模式都保留受管理授权及 Host/Environment 检查。提供方原生工具和外部客户端工具由各自的执行方负责。`web.search` 需要非 allow 权限时，选择 Host Web 搜索；活动的提供方原生搜索使用该策略会明确失败。

### 配置或替换审查器

为目标工具选择 `review`，并在同一个权限 Capability 中配置审查。下面使用逻辑名称 `review-model`；Host 必须将它解析为可用且具有凭据的 Model。构建时用生成的 `permissions` 替换前面的权限对象：

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

模型审查器是没有执行工具的独立 Agent。它接收脱敏后的调用参数、原始 schema 和有限的任务上下文。`instruction` 提供通用标准；shell 调用使用 `shell_instruction` 替代通用标准。

审查器返回风险评估和可选原因，由运行时决定动作。风险顺序为 `low < medium < high < extra_high`；达到或超过阈值时执行 `on_flagged`。全局默认阈值为 `extra_high`，动作为 `deny`。按相同选择器优先级只取最佳规则；缺失字段继承全局值，不继承更宽泛的规则。

应用可自行实现 `review()`，无需再发 Model 请求。下面将所有匹配的 reporting 调用评为高风险，并请求确认：

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

用这个对象替代模型审查配置。Agent 还需要安装稳定 ID 匹配 `tool/reporting/*` 的工具。这个类提供评估，权限选择和审批仍由运行时处理。

审查上下文目标大小为 16 KiB，硬上限为 64 KiB。当前脱敏参数和 schema 保持完整；可选 XML 块按整块省略。上下文包含任务或修正文本、被动 Environment 信息、最多五次历史审查和八次近期动作。保存的 State 最多保留 48 条扁平证据，不含完整参数或结果。这些记录区分评估、人工拒绝和观测到的派发结果，用于新一轮审查，不用于复用审批。

从已有用量账本中读取 `source="tool.review"` 的审查费用，通过工具和调用 ID 关联。模型审查器按自身选择的 Model 计价。自定义审查器可在 `ToolReviewResult.usage` 返回回执，或在 `ToolReviewError` 中保留已知回执。`payload.type="tool_review_result"` 的工具事件公开脱敏评估和运行时 `decision`；错误公开安全错误码、实际决策及 `result=null`。事件中的用量已经入账。缺少审查器时，不产生审查调用或结果事件。

### 读取原生审批决策

工具可检查 `ctx.deps.tool_approval`，要求业务确认。该只读访问器只属于当前调用，包含不可变的 `tool_id`、`tool_call_id` 和 `approved`；调用外为 `None`。并发调用使用各自的上下文。

将下面的第二个完整程序保存为 `approval_demo.py`。它用固定的演示批准展示挂起与续接。工具只返回确认文本，不发送报告：

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

运行 `uv run python approval_demo.py`，先打印 `Waiting for approval`，再打印 `Export confirmed`。第一个 Run 以待审批状态结束；第二个 Run 按目标调用 ID 提交结构化决策。

实际应用中，将固定的 `True` 换成从有权审批的人处收集的决策。将待处理请求和 State 一起保存，续接时提供新绑定，具体见[状态与恢复](state-and-resume.md#structured-suspension)。原生批准适用于整个调用，覆盖权限、审查、受管理策略和业务确认。当前审查与授权仍会重新执行：新的拒绝或审查超时可以阻止派发。参数覆盖会按当前 schema 验证。Host 负责历史和决策的来源，Harness 验证结构与关联。

## 描述真实副作用

`HarnessTool` 接受普通原生工具选项和必需的受管理元数据。按函数的实际行为选择各字段：

| 字段                     | 职责                                                                          |
| ------------------------ | ----------------------------------------------------------------------------- |
| `tool_id`                | 稳定工具身份，与面向模型的名称不同                                            |
| `effects`                | 非空集合，取值 `read`、`write`、`delete`、`execute`、`external_communication` |
| `credential_audiences`   | 派发所需凭据的确切 audience，最多 16 个                                       |
| `idempotency`            | `none`、`read_only` 或支持的 `provider_key` 语义                              |
| `output_policy`          | 有限的内联/总输出上限及溢出/遮蔽行为                                          |
| `resource_resolver`      | 可选异步解析，将已验证参数转为规范资源                                        |
| `superseded_by_tool_ids` | 工具集合解析使用的显式替代身份                                                |

写入或对外发送不能标记为 `read_only`。只有提供方实现了对应幂等语义，才能使用 `provider_key`。

需要按资源授权时，添加异步 `ToolResourceResolver(arguments, *, context)`。它接收已经验证的类型化参数，在当前 Host 权限下返回 `CanonicalResource(namespace, kind, identifier)`。evaluator 再根据这些资源做决策。解析失败会在授权和派发前终止调用。

## 评估当前权限

`InvocationPolicyEvaluator` 接收当前 `AgentContext`、工具元数据和 `ToolInvocationContext`。后者携带实例/Run 身份、调用关联、规范化 JSON 参数及摘要、规范资源，以及可选幂等键和截止时间。

为这次准备好的调用返回结果：

- `InvocationPolicyDecision.allow()`：继续。
- `InvocationPolicyDecision.deny(reason)`：停止派发。
- `InvocationPolicyDecision.require_approval(reason, metadata=...)`：请求确认。

原因和元数据应能安全展示。提供原因时，文本必须非空，最多 512 个字符；审批元数据为有限 JSON，最多 16 KiB。每个 Run 都按当前权限评估，不把旧决策当作永久授权。

只通过新的 `RunBindings.capabilities` 附加 `InvocationPolicyCapability`。它要求 evaluator，可选 `credential_broker` 和 `grant_broker`。`strict_managed_tools=False` 允许普通工具与受管理工具共存；设为 true 会拒绝包含非受管理工具的调用表。这限制的是可调用工具，不改变进程中已有 Python 代码的权限。`max_dispatch_retries` 默认 1，范围为 0–3；仍须满足下文的重试规则。

## 凭据与授权

不要把业务凭据放进模型参数。授权和审批通过后，Harness 为每个声明的 audience 调用 `CredentialBroker.acquire(audience, invocation, *, context)`。返回携带不透明值和可选异步关闭回调的 `CredentialLease`。adapter 用 `current_invocation_scope()` 读取调用范围内的值，再发出实际的提供方请求。

提供方还需要可验证的授权凭证时，实现 `InvocationGrantBroker.issue(...)`。它可返回 `InvocationGrantRef`，包含不透明授权 ID、audience、声明摘要和带时区的到期时间。签名、传输和验证由 Host/provider 负责；保存引用本身不构成权限。

Harness 在每次调用后关闭凭据 lease。单独持有的客户端由 Host 关闭。凭据值不应进入提示、结果、观测或保存的 State。

## 审批与不确定结果

审批使用上面的两轮 Run 流程。派发前重新解析资源并检查当前策略，审批不预留 Environment 目标。[内置内联子 Agent](delegation-and-codeact.md#host-managed-feedback)关闭延迟工具；子级需要人工反馈时，选择 Host 管理的子级生命周期。

重试前区分已知的提供方失败和不确定的副作用。只有提供方错误可重试、结果确定、工具声明 `read_only` 或受支持的 `provider_key` 语义，并且仍有重试预算时，受管理派发才会重试。超时、取消或提供方结果不明需要核对。重复写入前，先查询提供方结果或 Host 的派发记录。

## 限制工具输出

按 Model 所需的结果设置 `ToolOutputPolicy`。`max_inline_bytes` 范围为 512–262,144；`max_output_bytes` 为正数，最多 512 MiB，且不能小于内联上限。最终文本/JSON 边界还设有 20,000 字符硬上限。`redact=True` 为默认值，作用于结果文本/JSON，不覆盖任意日志或回调。

按用途选择溢出行为：

| `overflow`      | 内联输出过大时的结果                                 |
| --------------- | ---------------------------------------------------- |
| `fail`          | 有上限的工具失败                                     |
| `truncate`      | 明确标注的有限预览                                   |
| `spill`（默认） | 有限预览；存储与总量上限允许时，另附完整脱敏结果文件 |

溢出文件需要当前 Environment 的写权限，默认写入默认挂载 `.a13n/tmp/tool-results/` 下的 Run 私有目录。用绝对路径 `RunBindings.tool_result_directory` 选择其他位置。目标不可用时，预览不带文件路径。Harness 清理时尝试移除自己创建的 Run 私有目录；需要长期保留的产物应另存。

输出失败或截断发生时，工具可能已经完成操作，效果不会撤销。文件访问见 [Environment 工具](environments.md)；保留执行和结果的归属见 [Host 嵌入](hosting.md)。
