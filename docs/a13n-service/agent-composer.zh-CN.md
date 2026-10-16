---
title: Agent Composer
description: 通过对话创建 agent、配置工作空间资源和诊断运行。
---

Agent Composer 帮助你在 Console 中创建和修改 agent。描述想要的结果，检查配置方案，再批准写入。它也可以配置模型、连接和服务商，导入 skill，以及调查失败的运行。

## 启动 Agent Composer

工作空间需要一个你有权使用的[已启用模型](models.md)。准备 Composer 需要 `write` 权限；准备完成后，有 `run` 权限的调用者即可使用。

1. 打开 **Agents → 用 AI 创建**，或打开已有 agent 并选择**用 AI 编辑**。
2. 描述任务，例如：“创建一个将项目笔记整理成后续行动的 agent。使用已有模型，保存前先让我检查指令。”**用 AI 编辑**会预填 agent 和所选版本；补充希望修改的内容。
3. 选择**发送**。检查每项写入方案，批准或拒绝。完成所有待处理项后，选择**提交回复**继续。
4. 打开已保存的 agent，选择**试用 Agent**。用有代表性的输入测试，再回到 Composer 继续调整。

修改 agent 时，Composer 读取所选修订版本，在完整新配置中保留无关字段。写入使用最初读取的资源版本；遇到并发修改时，需要重新读取和审阅。

使用已有 Composer 或 Finding Agent 开始 Console 新会话时，如果你有 `write` 权限，Console 会在发送第一条消息前准备最新配置，包括从**试用 Agent**进入的会话。没有 `write` 权限，或准备操作返回权限不足时，继续使用已有默认版本；启动运行仍需要 `run` 权限。显式指定版本时跳过准备。其他准备错误会显示出来并停止发送。部署本身不会更新已保存的预设；API 客户端需要显式调用准备接口。

## 配置服务商

1. 让 Composer 添加或更新 Model、Environment、Connector 或 Web Provider。
2. 检查卡片中的名称、类型和配置方案。在私密卡片中输入凭据，选择**保存服务商**。保存需要工作空间的 `write` 权限。
3. 回答其他待处理操作，再选择**提交回复**继续对话。

凭据直接提交到 Provider API。Agent 只收到保存状态和服务商 ID；凭据不会进入消息、工具参数或结果。更新时凭据留空会保留已有凭据。更新使用原始资源版本，遇到冲突需要重新准备请求。

保存和继续对话是两个步骤。确认保存成功后，重试**提交回复**不会再次保存。如果无法确定保存结果，或在继续前刷新了页面，请先检查**服务商**页面：保存不会自动重试，再次创建可能产生重复记录。保存前选择**取消配置**不会写入；无法确认保存结果时停止，会报告结果未知。

连接凭据、自定义秘密请求头和浏览器 OAuth 授权使用已有的 Console 页面。通过 API 运行 Composer 的客户端需要处理原生 `configure_provider` client tool，或报告该功能不可用，沿用普通待处理调用和批量恢复约定。

## 可执行的操作

| 工具组   | 支持的操作                                                                 |
| -------- | -------------------------------------------------------------------------- |
| Agent    | 创建、读取、重命名、修改标签、归档，以及配置修订版本和默认版本管理。       |
| 模型     | 读取、创建和更新已配置的模型。                                             |
| 连接     | 读取、创建、更新、发现工具和测试连接。                                     |
| Skill    | 校验和导入上传包或公开 GitHub 来源、更新元数据和管理修订版本。             |
| 环境模板 | 读取、创建和更新供后续实例使用的模板。                                     |
| Provider | 读取 provider，通过 Console 私密凭据卡片创建和更新。                       |
| 执行查询 | 查询 session、thread、输入队列、run、attempt、展示内容、trace 和 finding。 |

Composer 使用平台 MCP 工具，Service 为运行提供短期凭据。读取默认直接执行；写入和连接测试使用[审批](agents-and-runs.md#waits-approvals-and-questions)。操作受用户当前权限、运行接受时的授权上限和工作空间限制。批准不会增加平台权限。

诊断时，提供运行 ID 或描述故障。Composer 可以在不知道 session ID 的情况下查找历史失败运行，并区分某次 attempt 失败与最终恢复成功。缺少相关 trace 证据时，答复会指出缺口。

[编辑 skill 包](skills.md)、[管理记忆](memory.md)、[配置 plugin](configuration-reference.md#plugins)、[管理环境实例](environments.md)或[控制运行](agents-and-runs.md)，请使用对应指南。这些操作不在 Composer 的工具范围内。写入结果不确定时，先查询资源状态再决定是否重试。

## 通过 API 准备

先准备 Composer，再用返回的 agent [开始会话](agents-and-runs.md#start-a-conversation)：

```sh
curl -X POST "$A13N_URL/api/v1/agent-composer" \
  -H "Authorization: Bearer $A13N_API_KEY"
```

首次调用创建工作空间预设（`source: "builtin"`、`preset_kind: "composer"`）。通过 `GET /api/v1/agents?source=builtin&preset_kind=composer` 可查到它。后续准备会刷新名称和描述，只有配置变化时才添加修订版本。

## 选择模型

准备时按以下顺序，选择已启用且调用者有权使用的模型：

1. Composer 当前修订版本的模型，只要它仍可用。
2. 按部署的 [`composer.models`](configuration.md#execution) 列表顺序匹配首个可用模型。匹配使用上游名称，忽略 `vendor/` 前缀。
3. 按 key 选择工作空间中首个可用模型。

没有可用模型时，准备操作返回 `409 conflict`，原因为 `model_required`。Console 会提供模型配置入口。[添加模型](models.md)后再次准备即可。

## 内置 agent 规则

Composer 的配置和模型选择由准备操作管理。编辑、添加修订版本、归档或设置头像会返回 `409 conflict`，原因为 `builtin`。复制 Composer 可创建可修改的自定义 agent。

## 在自己的 agent 中使用工具集

通过高级配置 JSON 选择平台工具组和本地操作 skill：

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

两项选择默认均为空。权限可设为 `allow`、`ask` 或 `deny`。选择工具组提供操作能力，选择 skill 提供说明。修改配置后，正在执行的运行保留接收时的授权和工具范围。

`platform-operations` skill 通过 Worker 的只读本地 Environment 读取随包发布的说明，不需要用户 Environment 或容器。它与用户 Skill 共用 Harness Skills 目录和读取机制。Agent 配置 schema 位于 `/platform-skills/platform-operations/schemas`；`/platform-instance` 中的部署目录在进程启动时生成一次，供各次 attempt 共用。

启用 `view`、`ls`、`glob` 和 `grep` 来读取 skill。Composer 只启用这四个文件工具。内置文档挂载始终只读；显式挂载的用户 Environment 遵循同一套文件工具权限。

已有 agent 仍可使用旧的 `configuration` 工具集。它的 `create_agent_revision` 要求提供原始读取结果中的 `expected_version`。缺少该值时，需要重新读取并提交新请求。历史运行保留原有工具。
