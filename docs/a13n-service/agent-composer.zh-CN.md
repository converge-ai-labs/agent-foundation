---
title: Agent Composer
description: 内置 agent，与你一起创建 agent 和新的 agent 修订版本，并在你批准后保存每一项。
---

Agent Composer 在普通对话中工作。它读取工作空间中的模型、skill、连接、环境模板和 agent，提出配置方案，并在每次写入获得批准后保存。

## 启动 Agent Composer

1. 打开 **Agents → 用 AI 创建**，或打开已有 agent 并选择 **用 AI 编辑**。
2. 检查新对话。**用 AI 编辑**会预填包含 agent 和所选版本的请求；补充希望修改的内容。
3. 选择**发送**以开始对话。提交批准前，检查每个拟执行的写入操作。

Console 会为拥有 `write` 权限的调用者准备 Agent Composer。仅拥有 `run` 权限的调用者可以使用 builder 已准备好的内置 agent。

通过 API 使用时，先准备它，再用返回的 agent [开始会话](agents-and-runs.md#start-a-conversation)：

```sh
curl -X POST "$A13N_URL/api/v1/agent-composer" \
  -H "Authorization: Bearer $A13N_API_KEY"
```

准备操作需要工作空间中的 `write` 权限。首次调用会创建工作空间中的 Composer 预设（`source: "builtin"`、`preset_kind: "composer"`），也可通过 `GET /api/v1/agents?source=builtin&preset_kind=composer` 查到。后续调用刷新名称和描述，只有配置变化时才添加修订版本。创建后，任何有 `run` 权限的人都可以用它启动运行。

## 选择模型

Agent Composer 需要一个已启用且调用者有权使用的模型。准备时按以下顺序选择：

1. 当前修订版本已使用的模型，只要它仍可用；
2. 否则，按部署的 [`composer.models`](configuration.md#execution) 列表顺序，选择上游名称匹配的首个可用模型（忽略 `vendor/` 前缀）；
3. 否则，按 key 选择工作空间中首个可用模型。

工作空间没有可用模型时，准备操作返回 `409 conflict`，原因为 `model_required`；Console 会提示你配置模型。[添加模型](models.md)后再次准备即可。

## 可执行的操作

Agent Composer 与外部客户端共用平台 MCP 工具。Service 为当前 run 提供短期凭据，你不需要创建 API key。操作同时受用户当前权限、run 接受时的授权上限和所选工作空间限制。

| 工具组   | 支持的操作                                                                 |
| -------- | -------------------------------------------------------------------------- |
| Agent    | 创建、读取、重命名、修改标签、归档，以及配置修订版本和默认版本管理。       |
| 模型     | 读取、创建和更新已配置的模型。                                             |
| 连接     | 读取、创建、更新、发现工具和测试连接。                                     |
| Skill    | 校验和导入上传包或公开 GitHub 来源、更新元数据和管理修订版本。             |
| 环境模板 | 读取、创建和更新供后续实例使用的模板。                                     |
| Provider | 读取 provider，通过 Console 私密凭据卡片创建和更新。                       |
| 执行查询 | 查询 session、thread、输入队列、run、attempt、展示内容、trace 和 finding。 |

读取默认直接执行。写入和连接测试使用[审批](agents-and-runs.md#waits-approvals-and-questions)，Console 展示实际调用供你批准或拒绝。批准不会增加平台权限。

修改 agent 时，Composer 读取所选修订版本，在完整新配置中保留无关字段，并使用最初读取的资源版本。发生并发修改时，它需要重新读取和审阅，不会偷偷替换成最新版本后继续写入。

内置 `platform-operations` skill 通过只读文件工具提供本地说明，不需要用户环境。Composer 可以在不知道 session ID 的情况下查找历史失败 run，并区分某次 attempt 失败与最终恢复成功。缺少 trace 证据时会明确说明。

Composer 不编辑 skill 包内文件、不操作 memory、不配置 plugin、不控制环境实例，也不启动或取消 run。它不会在聊天中索取秘密值。Provider 凭据在 Console 私密卡片中输入；连接凭据、自定义秘密请求头和浏览器 OAuth 使用已有的 Console 页面。写入结果不确定时，需要先查询状态再决定是否重试。

## 配置服务商

让 Composer 添加或更新 Model、Environment、Connector 或 Web Provider，它会打开卡片，展示拟保存的名称、类型和配置。在卡片中输入所需凭据，选择**保存服务商**。保存需要工作空间的 `write` 权限。然后回答其他待处理操作，并选择**提交回复**以继续对话。保存前选择**取消配置**不会执行写入。无法确认保存结果时取消，会向 agent 报告结果未知。

凭据直接提交到 Provider API，不会进入 agent 的消息、工具参数或结果。Agent 只会收到保存状态和服务商 ID。更新时凭据留空会保留已有凭据，并使用原始资源版本；发生冲突时需要重新准备请求。

保存和继续对话是两个步骤。确认保存成功后，重试**提交回复**不会再次保存。保存不会自动重试，创建也不支持幂等。如果无法确定保存结果，或在继续前刷新了页面，请先检查**服务商**页面，避免重试产生重复记录。浏览器 OAuth 授权仍在已有的 Console 页面进行。

通过 API 运行 Composer 的客户端需要处理原生 `configure_provider` client tool，或报告该功能不可用。它使用普通待处理调用和批量恢复约定，不需要额外的 Run 接口。

## 内置 agent 规则

Agent Composer 不能被编辑、添加修订版本、归档或设置头像（返回 `409 conflict`，原因为 `builtin`）；只有准备操作能修改它。模型按上述规则自动选择。可以复制它，创建可修改的自定义 agent。

## 在自己的 agent 中使用工具集

任何 agent 都可以通过高级配置 JSON 分别选择平台工具组和本地操作 skill：

```json
{
  "platform_tools": {
    "groups": ["agents", "models", "skills", "execution"],
    "read_permission": "allow",
    "write_permission": "ask"
  },
  "builtin_skills": ["platform-operations"]
}
```

两项选择默认均为空。权限可设为 `allow`、`ask` 或 `deny`。只启用 skill 不会获得操作权限。后续修改配置不会扩大正在执行的 run 的授权或工具范围。

内置 Skill 通过 Worker 的只读本地 Environment 读取随包发布的文档，不需要用户 Environment 或容器。它与用户 Skill 共用 Harness 的目录和读取事件机制，同时启用时显示在同一个目录中。Agent 配置 schema 随文档一起发布，位于 `/platform-skills/platform-operations/schemas`。`/platform-instance` 中的实例目录在进程启动时生成一次，供后续 attempt 共用。

读取所选 Skill 需要启用标准文件工具：`view`、`ls`、`glob` 和 `grep`。Composer 只启用这四个文件工具。内置文档挂载始终只读；显式挂载的用户 Environment 遵循同一套文件工具权限。

已有 agent 仍可使用旧的 `configuration` 工具集。它的 `create_agent_revision` 现在要求提供原始读取结果中的 `expected_version`；缺少该值时，需要重新读取并提交新的调用。历史 run 保留原有工具。
