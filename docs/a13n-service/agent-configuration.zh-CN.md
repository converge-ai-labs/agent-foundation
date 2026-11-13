---
title: 配置 agent
description: 保存修订版本、选择工具、配置委派并扩展 agent。
---

先在 Console 中确认 agent 能回答简短消息。按本页添加工具、审批、委派或插件，保存修订版本并测试行为，再供其他调用者使用。

## Agent 配置

先跑通纯模型对话，再按具体任务添加能力。手动编辑器直接提供配置项；[Agent Composer](agent-composer.md)可通过对话帮助构建配置。

### 设置审批与自动审查

1. 展开**工具**下的工具集，为工具选择**允许**、**询问**、**自动审查**或**拒绝**。连接提供相同选项。
2. 使用自动审查时，打开**配置审查**，选择审查模型，设置风险阈值及处理方式：拒绝或询问用户。低于阈值的调用继续执行。
3. 用**单独设置审查规则**配置特定工具。高级设置控制 Shell 审查标准、失败处理、超时和模型参数。
4. 选择**应用到草稿**，再选择**保存更改**。取消面板会丢弃其中的草稿修改。

审查必须指定模型。默认拒绝极高风险，审查失败时询问用户；审查超时则拒绝执行。

### 配置字段

按所需能力选择配置：

| 任务               | 配置                                                                                                                                           |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| 选择行为与模型     | `model`、`instructions`、`model_settings`、`model_characteristics`；参阅[模型](models.md)                                                      |
| 提供工具           | `toolsets`、`connection_tools`、`tool_search`；参阅[工具与连接](tools.md)                                                                      |
| 加载可复用指令     | `skills: [{skill_id, revision_id}]`；参阅 [Skill](skills.md)                                                                                   |
| 向应用或用户提问   | `client_tools`、`user_questions`；参阅[客户端工具](agent-configuration.md#client-tools-and-questions)                                          |
| 委派工作           | `subagents`；参阅[子 agent](agent-configuration.md#subagents)                                                                                  |
| 审查调用或理解媒体 | `reviewer`、`media_understanding`；参阅[媒体理解](models.md#media-understanding)                                                               |
| 要求结构化输出     | `output_spec`：一个 JSON Schema 或 2–32 个命名 `variants`；省略时返回文本                                                                      |
| 限制重试           | `retries.tools` 和 `retries.output`，各为 0–100                                                                                                |
| 提供工作环境       | `default_environment_id`、`default_environment_template_id`、`lazy_environment`；参阅[环境](environments.md)                                   |
| 保留知识           | `memory`、`memory_mounts`；参阅[记忆](memory.md)                                                                                               |
| 调整长时间工作     | `background_compaction`、`execution`；参阅[压缩](agent-configuration.md#context-compaction)和[执行策略](execution-options.md#execution-policy) |
| 扩展运行时         | `plugins`；参阅 [Console 插件](agent-configuration.md#configure-plugins-in-console)                                                            |

保存时按工作空间校验引用。省略的 skill 和子 agent 修订版本 ID 会固定到当前默认版本。模型、连接、provider 和模板仍是实时资源，使用时重新解析。`lazy_environment` 默认为 true，首次使用时准备实例。设为 false 会在 Harness 开始前准备实例；执行连接仍在首次使用时打开。

## 上下文压缩

对话增长时，Service 将较早的上下文压缩为摘要，默认同步执行。要在后台生成摘要，打开**高级设置 → 上下文压缩**，启用**启用后台压缩**，再保存修订版本：

```json
{"background_compaction": {"enabled": true, "blocking_threshold": 0.95}}
```

普通线程可以在生成摘要期间继续执行，按最近一次模型响应的用量选择执行模式。达到阻塞阈值时，运行等待已有摘要工作，或在达到压缩触发线后同步压缩。触发线等于或高于阻塞阈值时没有异步区间。后台运行不要求原生 token 计数或显式输出预留。就绪摘要可以在当前运行或同一线程的后续运行中应用；关闭新后台工作后，保留的摘要仍可应用。内联和异步子 agent 使用同步压缩。

摘要请求计入原始运行的请求预算，用量可能在该运行结束后到达。中断运行会保留其后台摘要工作。归档线程会终止该摘要的后续使用；分叉不继承待处理摘要。

上下文恢复默认启用，独立于 agent 修订版本的压缩配置。发生原生上下文窗口溢出后，Harness 先尝试正常压缩。摘要成功后，运行使用压缩后的历史继续执行。

只有普通执行和适用的压缩请求都溢出时，Service 才允许有界、有损的历史重建，每次运行最多一次，worker 接管也沿用这一额度。恢复请求发出前会提交重建历史和已消耗额度。历史文件尽力保存在主环境的 `/workspace/.a13n/history/<thread-id>/`，保留时间取决于该环境生命周期；存储或读取工具不可用不会阻止重建。

> [!WARNING]
> 每次有损重建都需要立即调查根因，即使续跑成功。先检查压缩配置和工具输出未受限的问题。Console 将警告和恢复结果与最终回答分开保留。

## 工具权限

用权限决定模型调用工具时如何处理：

| 权限      | 结果                                     |
| --------- | ---------------------------------------- |
| `allow`   | 执行调用。                               |
| `ask`     | 运行结束并等待批准。                     |
| `review`  | 按已配置的审查策略允许、拒绝或询问用户。 |
| `deny`    | 拒绝调用。                               |
| `inherit` | 使用工具默认值；内置工具默认为 `allow`。 |

内置和平台工具在 `toolsets.<toolset>.tools.<tool>.permission` 设置。连接使用 `connection_tools[].permission`，并可通过 `permissions` 单独覆盖工具。客户端工具在 `client_tools[].permission` 接受 `inherit`、`allow` 或 `deny`；允许的客户端调用仍需要应用返回结果。

## 客户端工具与问题

由应用负责执行的操作可声明为客户端工具。例如，在 agent config 中加入：

```json
{
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

调用发生后，运行以 `waiting` 结束，并在 `pending.calls` 中提供该调用。在应用中执行操作，再[携带结果恢复](agents-and-runs.md#resume-a-waiting-run)。

设置 `user_questions: true` 后，模型可以调用 `ask_user_question`。问题也出现在 `pending.calls` 中。通过 resume 回应指定调用；普通聊天消息会留在收件箱中，直到等待解决。

## 子 agent

在 `subagents.named` 中配置工作空间 agent ID。每条命名调用边可指定描述、固定版本、上下文共享方式和用量限制。将下列片段中的 `ap_...` 替换为真实 agent ID：

```json
{
  "subagents": {
    "mode": "async",
    "named": {
      "researcher": {
        "agent_id": "ap_...",
        "description": "Finds and summarizes sources.",
        "usage_limits": {
          "request_limit": 20
        },
        "environment": {
          "mode": "shared"
        }
      }
    },
    "dynamic": {
      "enabled": true
    }
  }
}
```

仅命名子 agent 的配置可以选择**内联**：子 agent 在父运行及其尝试内部执行。调用图不能回到发起委派的 agent。每次委派可限制请求数、token 和工具调用。

**异步**在同一会话中创建子线程，使用父运行的主体。父 agent 通过[委派工具](../a13n-harness/delegation-and-codeact.md#asynchronous-children)检查、等待、引导、取消或继续子 agent。子结果进入父收件箱，可以引导活跃运行或启动后续运行。等待用户的子运行在这些工具中显示为 `running`。异步调用边只应用 `usage_limits.request_limit`。

异步环境由调用边决定：`shared` 挂载父环境，`dedicated` 从 `template_id` 预留环境，`none` 不挂载。子 agent 自身的默认环境模板不生效。部署设置 `worker.child_count` 和 `worker.child_depth` 限制子 agent 数量及嵌套深度。

默认执行模式为异步。在 Console 或 API 中启用 `subagents.dynamic.enabled` 后，根 Agent 可以用 `spawn_subagent` 按任务创建子 agent，提供任务指令和首条输入。动态子 agent 共享父环境，只能选择父 agent 已有能力的子集，不能再次委派。`allowed_models`、`default_model` 和 `default_thinking` 配置可选模型及默认值；spawn 时也可以指定允许的模型和 thinking effort，`false` 表示关闭思考。

动态子 agent 使用线程快照，不创建临时 Agent 版本。归档来源 Agent 会阻止新的子运行及续接，已接受的运行仍可完成；取消归档后可以继续原快照。

动态子运行继承父运行设置的请求次数上限，按每个 Run 单独计数。后续消息保留该上限、执行设置、configuration 和 context：省略时继承，显式修改会被拒绝。使用来源 Agent 且不指定 revision 的分叉保留快照与共享环境，不允许 `fresh_environments=true`；显式选择已发布版本可切换为普通 Agent 执行。

## 在 Console 中配置插件

1. 打开 agent 的**能力 → 插件**，选择**添加插件**。
2. 选择部署已启用的插件，设置唯一实例名，在表单或 JSON 编辑器中修改设置。
3. 保存 agent，验证草稿并发布修订版本。保存校验配置，但不执行插件。

多个实例可使用同一插件。重命名或移除实例会更新工具发现引用。预设只读。插件不可用时，其保存 JSON 仍然可见；保存前恢复插件或移除实例。

管理员应在 Control 和 worker 上安装一致的包并配置 `plugins.keys`。Control 的 `GET /plugins` 列出启用插件，worker 安装须与之匹配。

## 在工具间复用媒体

在 agent 修订版本中启用私有对象库：

```json
{"toolsets": {"object_store": {"enabled": true}}}
```

截图等原生 MCP 媒体结果保留原始媒体，并获得 `a13n-object://obj_...` 引用。agent 可以把准确的引用字符串传给已启用的 base64 消费工具。运行时提供原始字节，无需模型生成 base64。对象库属于当前 thread；自动捕获的媒体不会成为工作区 Asset。分叉和独立子 thread 不继承其他 thread 的对象库。

通过 [Object Reference Transport（对象引用传输）](../a13n-harness/capabilities.md#object-reference-transport) 接入 Harness 工具扩展，把引用替换成 base64，不重跑参数校验。通过 MCP 连接的 `config.object_ref_tools` 规则选择工具：

```json
{
  "url": "https://example.com/mcp",
  "object_ref_tools": {"*": false, "*_upload": true, "send_image": true, "text_upload": false}
}
```

规则按服务原始工具名区分大小写匹配。`*` 可在任意位置匹配零个或多个字符，其他字符按字面匹配。精确名称优先，其次是非 `*` 字符更多的模式；具体程度相同时 false 优先，与条目顺序无关。`"*"` 设置默认值；未匹配的工具沿用自身声明，未声明时默认不支持。这与[原生工具集](../a13n-harness/capabilities.md#object-reference-transport)及 Harness UI 使用同一套选择规则。只为字符串参数接受 base64 的工具启用。连接权限和审批仍然有效。审批请求和历史保留引用；真正调用目标时，在权限检查后解析。如果引用在审批等待期间过期，会报告对象不可用，不会自动重放生产工具。

`asset_ref`、`asset_store`、`asset_list`、`asset_info` 和 `asset_delete` 用于导入有权限读取的 Environment 文件，以及管理私有对象目录。`asset_store(ref=...)` 提升可用对象的保留级别而不改变 ID。已保留对象不受临时过期和 LRU 淘汰影响，直到显式删除。

运维人员可通过 `A13N_OBJECT_STORE__TEMPORARY_TTL_SECONDS`（86400）、`A13N_OBJECT_STORE__MAX_TEMPORARY_OBJECTS`（32）和 `A13N_OBJECT_STORE__MAX_TEMPORARY_BYTES`（67108864）配置每个 thread 的临时保留策略。读取刷新闲置 TTL；list/info 不续期。后台扫描也会回收不活跃 thread 的过期对象。现有对象后端的单对象字节上限仍然有效。工具集的 `config.capture_media` 和 `config.max_object_bytes` 配置 Harness 捕获和导入行为，与 Host 保留策略分开。共享传输规则见 [Object Store](../a13n-harness/capabilities.md#reuse-original-media-with-object-store)。
