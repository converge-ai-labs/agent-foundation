---
title: 配置智能体
description: 在 Service 中管理不可变智能体版本、配置工具权限与 LLM 风险审查、挂载子智能体、启用后台上下文压缩并绑定运行时插件。
---

在 Console 手动编辑器（或通过 [Agent Composer](agent-composer.md)）编辑智能体草稿，校验并发布带编号的不可变版本（`POST /api/v1/agents/{agent_id}/revisions`），测试通过后再设为默认版本。

## 智能体配置总览

<span id="configuration-fields"></span>

| 能力模块         | 智能体 `config` 字段                                                                   | 参考指南                                             |
| ---------------- | -------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| 模型与指令       | `model`、`instructions`、`model_settings`、`model_characteristics`                     | [模型](models.md)                                    |
| 工具与外部连接   | `toolsets`、`connection_tools`、`tool_search`                                          | [工具与连接](tools.md)                               |
| 技能包           | `skills: [{skill_id, revision_id}]`                                                    | [技能](skills.md)                                    |
| 客户端工具与提问 | `client_tools`、`user_questions`                                                       | [客户端工具与提问](#client-tools-and-questions)      |
| 子智能体委派     | `subagents`                                                                            | [子智能体](#subagents)                               |
| 风险审查与多模态 | `reviewer`、`media_understanding`                                                      | [媒体理解](models.md#media-understanding)            |
| 输出契约与重试   | `output_spec`（JSON Schema、`variants` 或 `alternatives`）、`retries: {tools, output}` | [高级执行选项](execution-options.md)                 |
| 默认运行环境     | `default_environment_template_id` 或 `default_environment_id`、`lazy_environment`      | [运行环境](environments.md)                          |
| 记忆与上下文压缩 | `memory`、`memory_mounts`、`background_compaction`                                     | [记忆](memory.md)、[上下文压缩](#context-compaction) |
| 运行时插件       | `plugins`                                                                              | [在 Console 配置插件](#configure-plugins-in-console) |

## 工具权限

<span id="set-approvals-and-automatic-review"></span>

在 `toolsets.<toolset>.tools.<tool>.permission` 与 `connection_tools[].permission`（支持单工具 `permissions` 覆盖）上设置 `allow`、`ask`、`review`、`deny` 或 `inherit`：

- **`ask`：** 将运行挂起为 `waiting`（出现在 `pending.approvals` 中）。
- **`review`：** 调用 `reviewer` 审查模型评估风险等级（`low`..`extra_high`），达到阈值时拒绝或请求人工审批（默认拒绝 `extra_high` 且在审查异常时请求人工确认）。

## 客户端工具与提问

在 `client_tools` 中声明由业务应用自行执行的工具，并设置 `user_questions: true` 开启 `ask_user_question`：

```json
{
  "user_questions": true,
  "client_tools": [{
    "name": "open_ticket",
    "description": "Open a support ticket in the customer's CRM.",
    "parameters_json_schema": {
      "type": "object",
      "properties": {"title": {"type": "string"}},
      "required": ["title"]
    }
  }]
}
```

两者触发后均进入 `pending.calls` 并使运行处于 `waiting` 状态；在应用侧执行或收集回答后[恢复等待中的运行](agents-and-runs.md#resume-a-waiting-run)。

## 子智能体

将工作区内的智能体挂载为 `async`（默认，在同一会话下创建独立子线程并使用[异步委派工具](../a13n-harness/delegation-and-codeact.md#asynchronous-children)）或 `inline` 子智能体，并可按需开启 `spawn_subagent` 动态创建：

```json
{
  "subagents": {
    "mode": "async",
    "named": {
      "researcher": {
        "agent_id": "ap_...",
        "description": "Finds and summarizes sources.",
        "usage_limits": {"request_limit": 20},
        "environment": {"mode": "shared"}
      }
    },
    "dynamic": {"enabled": true}
  }
}
```

异步边通过 `environment.mode` 选择 `"shared"`（共享父级环境）、`"dedicated"`（按 `template_id` 分配独立环境）或 `"none"`。

## 上下文压缩

Service 默认启用同步上下文压缩与溢出自动恢复。如需不阻塞对话的异步摘要生成，可在 **Advanced → Context compaction** 中开启：

```json
{"background_compaction": {"enabled": true, "blocking_threshold": 0.95}}
```

## 在 Console 配置插件

打开智能体的 **Capabilities → Plugins → Add plugin**，选择运维在 `plugins.keys` 中启用的插件（`GET /api/v1/plugins`），设置唯一实例名与参数后保存新版本。

## 在工具间复用媒体对象

在智能体版本中启用线程私有的对象库（`{"toolsets": {"object_store": {"enabled": true}}}`），使截图等二进制工具结果自动获得 `a13n-object://obj_...` 引用（配套 `asset_ref`、`asset_store`、`asset_list`、`asset_info`、`asset_delete` 工具），并可通过 [Object Reference Transport](../a13n-harness/capabilities.md#object-reference-transport) 直接传给已启用 `config.object_ref_tools` 规则的 base64 MCP 工具，无需让模型手动复制 base64 文本。
