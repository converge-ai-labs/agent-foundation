---
title: Agent Composer
description: 内置 agent，与你一起创建和编辑 agent，并在你批准后保存每次修改。
---

Agent Composer 在普通对话中工作。它读取工作空间中的模型、skill、连接、环境模板和 agent，提出配置方案，并在每次写入获得批准后保存。

## 启动 Agent Composer

在 Console 中打开 **Agents**，展开 **Create agent** 菜单并选择 **Create with AI** 。要修改已有 agent，打开详情页并选择 **Edit with AI** 。Console 会准备 Agent Composer，并打开与它的新对话。

通过 API 使用时，先准备它，再用返回的 agent [开始会话](agents-and-runs.md#start-a-conversation)：

```sh
curl -X POST "$A13N_URL/api/v1/agent-composer" \
  -H "Authorization: Bearer $A13N_API_KEY"
```

准备操作需要工作空间中的 `write` 权限。首次调用会创建工作空间中唯一的内置 agent（`source: "builtin"`），也可通过 `GET /api/v1/agents?source=builtin` 查到。后续调用刷新名称和描述，只有配置变化时才添加修订版本。创建后，任何有 `run` 权限的人都可以与它对话。

## 选择模型

Agent Composer 需要一个已启用且调用者有权使用的模型。准备时按以下顺序选择：

1. 当前修订版本已使用的模型，只要它仍可用；
2. 否则，按部署的 [`composer.models`](configuration.md#execution) 列表顺序，选择上游名称匹配的首个可用模型（忽略 `vendor/` 前缀）；
3. 否则，按 key 选择工作空间中首个可用模型。

工作空间没有可用模型时，准备操作返回 `409 conflict`，原因为 `model_required`；Console 会引导你配置模型。[添加模型](models.md)后再次准备即可。

## 可执行的操作

Agent Composer 使用 `configuration` 工具集。每个工具都以启动该运行的主体身份执行，受该主体的权限约束：

| 工具                    | 作用                                                                                  | 默认权限 |
| ----------------------- | ------------------------------------------------------------------------------------- | -------- |
| `find_resources`        | 列出 agent、模型、skill、连接或环境模板，每次 20 条。                                 | allow    |
| `read_resource`         | 读取一个资源；模型按 key，其余按 ID。Agent 返回默认修订版本，或指定的 `revision_id`。 | allow    |
| `describe_agent_config` | 返回 agent 配置 schema 和工具集目录。                                                 | allow    |
| `create_agent`          | 使用名称、描述和配置创建 agent，返回 `agent_id` 和 `default_revision_id`。            | ask      |
| `create_agent_revision` | 将完整配置添加为 agent 的新修订版本，默认将其设为默认修订版本。                       | ask      |

每次写入都会暂停运行，等待你的[审批](agents-and-runs.md#waits-approvals-and-questions)；Console 显示拟执行的调用，供你批准或拒绝。验证错误或缺少权限等拒绝结果会返回 Agent Composer，由它说明。

修改 agent 时，Agent Composer 读取你指定的修订版本，否则读取默认修订版本，然后写入完整的新修订版本，保留所有未要求修改的字段。它只使用工具返回的模型 key 和资源 ID。

Agent Composer 不能重命名 agent、修改标签、归档 agent，也不能管理连接和 provider。它不会索取凭据值：请先自行配置连接，再让 Agent Composer 使用。

## 内置 agent 规则

Agent Composer 不能被编辑、添加修订版本、归档或设置头像（返回 `409 conflict`，原因为 `builtin`）；只有准备操作能修改它。可以复制它，创建可修改的自定义 agent。

## 在自己的 agent 中使用工具集

任何 agent 都可以在修订版本中启用 `configuration` 工具集。写入工具默认设为 `ask`；修订版本可以为每个工具设置其他[权限](agents-and-runs.md#tool-permissions)。
