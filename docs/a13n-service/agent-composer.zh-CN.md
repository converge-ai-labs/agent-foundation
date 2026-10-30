---
title: Agent Composer
description: 描述 agent 任务，审阅配置变更，再到 Console 测试保存后的结果。
---

用 Agent Composer 将任务描述变成保存好的 agent。Composer 会读取工作空间资源、提出变更，并请求你批准写入。先创建一个项目笔记助手，测试输出，再回到同一段对话中改进。Composer 也能配置资源和诊断失败的运行。

## 启动 Agent Composer

准备 Composer 需要一个你有权使用的[已启用模型](models.md)，以及工作空间的 `write` 权限。准备完成后，有 `run` 权限的调用者即可使用。

1. 打开 **Agents → 用 AI 创建**。
2. 发送：“创建一个将项目笔记整理成后续行动的 agent。如果笔记写明了负责人，就在对应行动中列出。使用已有模型，保存前先让我检查指令。”
3. 检查提出的 agent 配置。批准或拒绝每项写入，完成全部待处理项后，选择**提交回应**继续。
4. 打开保存后的 agent，选择**试用 Agent**。发送几条笔记，其中一个行动有明确负责人，另一个尚未分配。检查回复是否正确保留这两种情况。
5. 要继续改进，打开 agent 并选择**用 AI 编辑**。Console 会预填 agent 和所选版本；描述你要的修改。Composer 读取该版本，在新配置中保留无关字段。

写入使用 Composer 最初读取的资源版本。如果其他人先修改了资源，让 Composer 重新读取，再审阅新方案。

在 Console 中与已有 Composer 或 Finding Agent 开始新对话时，如果你有 `write` 权限，Console 会在第一条消息前准备当前定义，包括从**试用 Agent**进入的会话。显式固定修订版本时跳过准备。没有 `write` 权限时使用已有默认版本；准备操作返回权限错误且已有默认版本时，也会回退到该版本。其他准备错误会显示出来并阻止发送。运行仍需要 `run` 权限。API 客户端需显式准备；仅部署新版本不会刷新已保存的预设。

## 配置服务商

在私密服务商卡片中填写凭据：

1. 让 Composer 添加或更新 Model、Environment、Connector 或 Web Provider。描述服务商类型和用途，不要把凭据粘贴到对话中。
2. 检查卡片中的名称、类型和配置。在卡片中填写凭据，选择**保存供应商**。这一步需要工作空间的 `write` 权限。
3. 确认保存成功后，完成其他待处理项并选择**提交回应**。Composer 会收到保存后的 provider ID，继续配置。

卡片将凭据直接发送到 Provider API。消息、工具参数、工具结果和恢复请求都不包含凭据。更新时凭据字段留空会保留已有凭据。版本冲突需要重新读取并提出新方案。

将保存和继续对话视为两个步骤。确认保存后，重试**提交回应**只会重试继续执行。如果保存未获确认，或在继续前刷新了页面，先到**供应商**页面检查，再决定是否重新创建：第一次请求可能已经成功。卡片不会自动重试保存。任何保存尝试前选择**取消配置**不会写入；未确认的保存尝试后停止，会报告结果未知。

连接凭据、自定义秘密请求头和浏览器 OAuth 使用对应 Console 页面。API 客户端需要处理原生 `configure_provider` client tool，或通过普通[待处理调用和批量恢复流程](agents-and-runs.md#waits-approvals-and-questions)报告该功能不可用。

## 可执行的操作

| 工具组   | 支持的操作                                                                               |
| -------- | ---------------------------------------------------------------------------------------- |
| Agent    | 创建、读取、重命名、修改标签、归档，以及管理修订版本和默认版本。                         |
| 模型     | 读取、创建和更新已配置的模型。                                                           |
| 连接     | 读取、创建、更新、发现工具和测试连接。                                                   |
| Skill    | 校验和导入上传包或公开 GitHub 来源、更新元数据和管理修订版本。                           |
| 环境模板 | 读取、创建和更新供后续实例使用的模板。                                                   |
| Provider | 读取 provider；通过 Console 私密凭据卡片创建和更新。                                     |
| 定时计划 | 预览、创建和修改计划；启用、暂停、归档、触发，以及查询触发记录。                         |
| 执行查询 | 查询 session、thread、输入队列、run、attempt、展示内容、trace 和 finding；提交 finding。 |

Composer 使用带有短期运行凭据的平台 MCP 工具。读取默认直接执行；写入和连接测试需要[审批](agents-and-runs.md#waits-approvals-and-questions)。每项操作受用户当前权限、运行接受时的授权上限和工作空间限制。批准不会增加平台权限。

创建[定时计划](schedules.md)时，提供时间、时区、目标 agent 和输入。先检查时间预览，再批准创建。新计划默认暂停；明确要求启用后，才开始未来的定时执行。计划会在本次对话结束后继续保留你的执行授权。暂停或归档计划可停止未来工作；后续运行仍遵循各自的工具审批规则。

诊断时，提供运行 ID 或描述故障。Composer 可以在不知道 session ID 的情况下查找失败运行，区分某次 attempt 失败与后来恢复成功，并指出缺少的 trace 证据。写入结果不确定时，让它先检查已保存资源，再决定是否重试。

[编辑 skill 包](skills.md)、[管理记忆](memory.md)、[配置 plugin](configuration-reference.md#plugins)、[管理环境实例](environments.md)或[控制运行](agents-and-runs.md)，请使用对应指南。这些操作不在 Composer 选定的工具范围内。

## 通过 API 准备

使用装有 `curl` 和 `jq` 的终端。将 `A13N_URL` 设为 Service 源地址，不含 `/api/v1`；将 `A13N_API_KEY` 设为[工作空间 API 密钥](identity.md#api-keys)，其所属主体需具有 `write` 和 `run` 权限。工作空间必须已有可用模型。

```sh
curl --fail-with-body -sS -X POST "$A13N_URL/api/v1/agent-composer" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -o composer.json
jq '{id, source, preset_kind, default_revision_id}' composer.json
```

响应应包含 `source: "builtin"`、`preset_kind: "composer"`、`id` 和 `default_revision_id`。将该 `id` 作为[开始会话](agents-and-runs.md#start-a-conversation)中的 `agent_id`，再发送上面 Console 示例中的任务。准备操作创建或刷新 agent，不会启动运行。

以后可用 `GET /api/v1/agents?source=builtin&preset_kind=composer` 查找预设。重复准备会更新部署维护的名称和描述，并将受保护配置的变更保存为候选版本。你选定的默认版本和模型保持不变。

## 选择模型

首次准备按以下顺序，选择已启用且调用者有权使用的模型：

1. 按部署的 [`composer.models`](configuration.md#execution) 列表优先级匹配。匹配使用上游名称最后一个 `/` 之后的部分。
2. 没有匹配项时，按 key 选择工作空间中首个可用模型。

没有可用模型时，首次准备返回 `409 conflict`，原因为 `model_required`。按 Console 的模型设置链接或[添加模型](models.md)指南完成配置，再次准备。后续准备会保留选定模型，即使它已不可用；请在 Composer 的 Agent 页面选择替代模型。

## 内置 agent 规则

要调整 Composer，打开其 Agent 页面，修改模型或允许的模型参数，再保存修订版本。可编辑参数包括推理强度、最大输出 token、`temperature` 和 `top_p`。指令输入框保存你的补充指令；官方指令在只读折叠区显示。

使用**版本 → 设为默认**选择或回退修订版本。准备操作会保留该选择和你的补充指令。受保护配置的更新以候选版本提供，由你决定是否选用。其他配置、身份、头像和归档仍受保护；需要自由编辑时，复制 Composer。

每次尝试都会使用当前 Worker 提供的官方指令，包括恢复和等待后的继续执行。保存版本固定的是你的补充指令，而非官方文本。折叠区预览当前部署的指导内容；某次执行实际使用的文本可在模型请求 trace 中查看。

通过 API 编辑时，先读取选定默认版本和原始 head ETag。保留完整 `config`，仅修改允许的模型、参数和 `config.instructions` 补充指令，再使用该 ETag 创建修订版本。版本响应通过只读 `builtin_instructions` 展示当前官方文本。复制和导出会将当前官方文本与用户补充合并为独立的自定义 agent。

## 在自己的 agent 中使用工具集

自定义 agent 使用 `platform-operations` skill 时，需要模型和主环境，Composer 的副本也一样。运行前设置默认环境模板，或选择会话环境；skill 会按普通[同步策略](skills.md#runtime-synchronization)复制到该环境。

1. 打开 agent 的工具，在**高级 / 平台功能**下选择[平台工具](tools.md#platform-tools)。
2. 启用 `view`、`ls`、`glob` 和 `grep`，让 agent 能读取 skill。
3. 在高级配置 JSON 中选择 `platform-operations` 和所需平台组。将以下字段合并到 agent 的完整配置，保留模型和环境设置：

```json
{
  "toolsets": {
    "agents": {
      "enabled": true,
      "tools": {
        "archive_agent": { "enabled": false }
      }
    },
    "models": { "enabled": true },
    "skills": { "enabled": true },
    "execution": { "enabled": true }
  },
  "builtin_skills": ["platform-operations"]
}
```

这段局部配置选择 agent、模型、skill 和执行查询工具，同时禁用 agent 归档。合并时保留其他已选工具集，包括文件工具。保存修订版本后，试一个只读任务，例如“列出此工作空间中可用的 agent”。检查 agent 是否读取 skill 并返回工作空间中的结果。

平台组和内置 skill 默认不选中。工具提供操作能力，skill 提供说明。每个工具保留自己的 `permission`；`inherit` 允许读取，写入前询问。配置修改作用于后续运行；当前运行保留接收时的授权和工具选择。

内置 Composer 通过 Worker 的只读挂载读取同一份随包发布的 skill，不需要用户环境或容器。Schema 目录为 `/platform-skills/platform-operations/schemas`，部署目录为 `/platform-instance`。Composer 只启用上述四个文件工具。内置文档挂载始终只读；显式挂载的用户环境遵循同一套文件工具权限。

### Computer 坐标模式

如果自定义 agent 还使用 Computer 工具，请在[环境的 Computer 配置](environments.md#computer-coordinate-mode)中选择像素或相对坐标模式。
