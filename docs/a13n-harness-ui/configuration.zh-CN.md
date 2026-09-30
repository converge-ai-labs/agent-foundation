---
title: 配置参考
description: 根配置文件、加载优先级，以及修改何时生效。
---

Harness UI 使用 YAML 和 Markdown 文件。**根文件控制应用默认值；Model 和 Agent 文件控制 agent 行为。** 先运行 `a13n-harness-ui setup`，再按需编辑这些文件。

面向具体任务的示例见[常见配置用法](configuration-recipes.md)。本页提供根文件和加载规则参考。[内置配置 Skill](skills-and-content-plugins.md#built-in-configuration-skill) 为 Agent 提供与安装版本匹配的离线指引。

## 找到对应设置

| 要配置的内容                             | 打开的位置                          | 参考                                                                        |
| ---------------------------------------- | ----------------------------------- | --------------------------------------------------------------------------- |
| 启动检查和日志                           | `a13n-harness-ui.yaml` → `process`  | [进程设置](#process-settings)                                               |
| 默认 Agent、Environment、插件或 MCP      | `a13n-harness-ui.yaml` → `defaults` | [默认选择](#default-resource-selections)                                    |
| 主题和输出详细程度                       | `a13n-harness-ui.yaml` → `display`  | [显示设置](#display-settings)                                               |
| 提问、CodeAct、内置子级                  | 根 `tools` 和 `subagents`           | [内置工具](#built-in-tools-and-subagents)                                   |
| 提供方、API 密钥引用、端点、推理、上下文 | `models/*.yaml`                     | [Model 字段](models-and-authentication.md#model-file-reference)             |
| 指令、Capability、可见工具、子级         | `agents/*.yaml`                     | [Agent 字段](agents-and-subagents.md#agent-file-reference)                  |
| 本地根目录和远程工作环境                 | `projects/*.yaml`                   | [Project 字段](environments-and-projects.md#project-file-reference)         |
| 远程 Device 连接                         | `devices/*.yaml`                    | [Device 配置](environments-and-projects.md#add-device-working-environments) |
| 外部工具                                 | `mcp/*.yaml` 或 `mcp/*.json`        | [MCP 字段](mcp.md#mcp-field-reference)                                      |
| 已安装集成                               | `extensions/*.yaml`                 | [扩展字段](extensions-and-mcp.md#harness-plugin-and-run-extension-files)    |

资源引用使用 **`id`**，不用文件名或显示名称。例如，`defaults.agent: agent-coder` 选择 YAML 中 `id: agent-coder` 的 Agent。

## 定位并验证文件

```console
a13n-harness-ui config path
a13n-harness-ui config show --format json
a13n-harness-ui config validate
a13n-harness-ui --config /path/to/a13n-harness-ui.yaml config validate
a13n-harness-ui config subagents --format json
```

默认根文件为 `~/.a13n-harness-ui/a13n-harness-ui.yaml`。`--config PATH` 选择另一棵配置树，不会叠加到默认树上。全局选项放在子命令前。

根文件所在目录还包含：

| 路径                       | 用途                                            | 详细参考                                                               |
| -------------------------- | ----------------------------------------------- | ---------------------------------------------------------------------- |
| `AGENTS.md`                | 可选的全局上下文指引                            | [指引](agents-and-subagents.md#instructions-and-guidance)              |
| `models/*.yaml`            | 可复用 Model 和凭据引用                         | [Model](models-and-authentication.md#model-file-reference)             |
| `agents/*.yaml`            | Agent 定义和子级清单                            | [创建 Agent](agents-and-subagents.md#create-an-agent-from-files)       |
| `subagents/*.md`           | 轻量子级指令                                    | [Markdown 子级](agents-and-subagents.md#write-a-markdown-child)        |
| `projects/*.yaml`          | 本地根目录、Device 选择和创建默认值             | [Project](environments-and-projects.md#project-file-reference)         |
| `devices/*.yaml`           | 可复用 Device 连接和凭据引用                    | [Device](environments-and-projects.md#add-device-working-environments) |
| `extensions/*.yaml`        | Harness Plugin、Environment 配置、Run Extension | [扩展](extensions-and-mcp.md)                                          |
| `mcp/*.yaml`、`mcp/*.json` | MCP 服务器定义                                  | [MCP](mcp.md)                                                          |

只扫描当前层的小写 `.yaml` 或 `.md` 文件，以及 `mcp/` 中的 `.json`；忽略 `subagents/README.md`。文件名供人阅读；资源引用使用 `id`。一个文件定义一个资源，MCP 多服务器 `mcpServers` 格式除外。MCP environment/header 值支持字面值和环境引用，见 [MCP 配置](mcp.md)。支持版本中的未知新增字段会保留并发出警告，**不会应用**；不支持的 schema 版本、重复 ID/键、YAML alias/anchor 和无效引用仍会拒绝候选配置。不会递归扫描或合并祖先目录配置。

### 被跳过的 Capability

Agent 中的 Capability 缺失、含糊、无法加载或无效时，会产生警告，而不会阻止对话。Harness UI 只跳过该条目，保留有效条目（包括其他 `NativeTool` 条目），不修改 YAML。警告指出 Agent ID、Capability 键和原因。交互式 CLI 显示这些警告；`config validate` 和 Web API 的 App 状态通过 `capability_warnings` 公开。如果仅有这些问题，验证仍成功。

修正 Capability 名称或参数，必要时安装可信实现，或移除条目。显式配置的默认 Capability 无效时，保持跳过，不会换成权限更广的默认值。权限策略绝不会静默跳过：无效 `ToolPermissionsCapability`（包括缺少审查 Model）会拒绝验证和 Run 组合，不论根快捷配置是否开启。修复 Agent 策略、`security.shell_review` 或引用的 Model。Environment 权限、强制调用策略和工具开关仍然适用。

新 Run 只捕获有效选择。已捕获 Run 不会改变；运行时/模型提供方故障也不会转为配置警告。无效 YAML 结构、Model 资源、Environment 或 Plugin 配置仍需修复。

## 完整根配置

以下入门文档展示主要根设置。可选集成在后文说明。将资源 ID 换成自己的，或将选择保留为 `null`。

```yaml
schema_version: "1"
max_goal_iterations: 10
process:
  pricing_auto_update: true
  terminal_update_check: true
  log_level: INFO
  log_format: pretty
  max_object_bytes: 268435456
input:
  long_text_threshold_chars: 8000
memory:
  enabled: true
  auto_organize:
    enabled: true
    model: null
    instructions: ""
media_understanding:
  image: null
  video: null
  audio: null
defaults:
  project: null
  agent: null
  environment_profile: environment-native
  harness_plugins: []
  environment_run_extensions: []
  mcp_servers: []
display:
  theme: auto
  mode: concise
  show_status: true
  max_tool_result_lines: 5
  max_tool_argument_chars: 8192
tools:
  enable_ask_user_question: true
  interaction_timeout_seconds: 120
  enable_codeact: true
security:
  shell_review:
    enable: false
    risk_threshold: null
    on_flagged: null
    on_error: null
    model: null
subagents:
  include: []
webui:
  allowed_origins: []
  sidekick: {}
```

### 文件记忆

CLI 和 WebUI 默认开启文件记忆。跨对话偏好和稳定事实存于选中根 YAML 旁的 `memory/global/`，Project 专用文件存于 `memory/projects/<project-id>/`。没有 Project 的对话只使用全局记忆。`MEMORY.md` 是始终加载的简明索引；详细主题可使用独立文件。记忆文件不是配置资源或对话历史。更广的 Full Control 和人工文件系统访问权限不变。

在 **Settings → General → Memory** 中配置，或编辑：

```yaml
memory:
  enabled: true
  auto_organize:
    enabled: true
    model: model-primary
    instructions: Keep decisions concise and preserve useful source references.
```

用 `instructions` 设置摘要语言、主题分组等偏好。整理器会报告修改，保留不确定信息和用户更正。Agent 将记忆视为历史上下文。

**Organization model** 在 `model` 省略或为 null 时跟随 `defaults.agent` 的 Model。可选择 Model ID 覆盖。整理要求其中一个 Model 已配置；只使用限定范围的记忆工具，不使用 Agent 的其他工具或指令。设置向导会开启记忆和整理，同时保留现有选择。

自动整理**仅适用于 WebUI**。新的对话输入会为所属范围中的已修改文件提供并行后台维护。它不会读取旧对话或扫描其他 Project。每个范围保留一个只读 Memory Thread，与普通对话分开。其工具仅限范围内的记忆工具，不继承 Agent 的其他工具和指令。可能产生额外模型请求、消耗配额或产生费用。General 设置显示当前进程的可用性、活动、结果和记录用量。

记忆未变化、为空、忙碌或处于冷却期时，不发出后台模型请求。成功尝试冷却一小时；失败、取消或崩溃后需等待十五分钟才能重试。重试需要另一条输入触发。每次尝试最多十二个请求、五分钟。新输入不会中断活动整理器。关闭任一开关或停止服务器会取消维护，保留部分文件修改供后续重新尝试。已受理的前台 Run 保留捕获的记忆设置。

要保留记忆而不发出自动请求，设置 `auto_organize.enabled: false`。设置 `memory.enabled: false` 会停止为后续 Run 绑定记忆；两者都不删除文件。选中配置根目录的 `memory/` 需与应用数据根分开备份。`.a13n-memory/` 包含内部锁和整理状态，不是用户记忆。可选 Git 仅提供基于一次快照的有界 diff 提示；它不是必需的，不操作你的仓库，也不存储版本历史。

### 媒体理解

`media_understanding.image`、`.video` 和 `.audio` 选择已保存 Model ID，供活动 Model 无法原生接受媒体时作为文件 `view` 回退。每项默认 `null`，保留对应 Harness 环境回退。在 **Settings → Models** 或 `/model defaults` 中配置。优先级、能力要求和 Run 捕获行为见[媒体理解默认值](models-and-authentication.md#media-understanding-defaults)。

### WebUI 允许的 origin

`webui.allowed_origins` 在监听器已有绑定地址和回环访问规则上增加公共请求地址，默认 `[]`。每项为确切的 HTTP(S) origin（包含非默认端口），或允许任意请求地址的字面值 `"*"`。允许尾部 `/` 并规范化移除；默认端口（HTTP 的 `80`、HTTPS 的 `443`）会规范化。不支持路径、凭据、查询、片段和 `https://*.example.com` 等部分通配符。

```yaml
webui:
  allowed_origins:
    - "https://anui.wh1isper.top:8090/"
```

要显式关闭地址限制，使用 `allowed_origins: ["*"]`。这不会关闭 API 密钥身份验证或浏览器同源检查，也不是 CORS 白名单。即使配置两个 origin，也不能让它们彼此发起跨源 API 请求。已知公共地址时应优先列出确切值；`"*"` 会移除 Host 限制，包括 DNS 重绑定保护。已有绑定地址和回环访问独立于这些条目，仍然允许。

监听器在启动时捕获设置。编辑后重启 WebUI；接受配置重载不会改变活动监听器的访问边界。HTTPS 转发和代理信任见[反向代理](webui.md#reverse-proxies-and-public-addresses)。

### WebUI MCP Apps

交互式 MCP 结果需主动启用。设置 `webui.mcp_apps.enabled: true`，并在 `webui.mcp_apps.servers` 中选择服务器 ID。WebUI 自动将它们与通用 MCP 选择一同加入每个根级和子级 Agent，去重 ID，不修改 Agent 文件或已保存 Thread 选择。CLI 仍只支持通用 MCP。开启 Apps 或改变独立 origin 沙箱监听器后需重启 WebUI。字段参考、交互权限以及本地、Docker 和反向代理配置见 [MCP Apps](mcp-apps.md)。

### WebUI Sidekick

Sidekick 默认开启。设置向导在新配置文件中显式写入 `webui.sidekick: {}`。已有文件省略 `webui` 或 `sidekick` 时，也会开启但不改写文件。已有 `sidekick: null` 仍保持关闭；设置向导保留显式 null 和自定义 Agent/Model 选择。

在 **Settings → General → Sidekick** 中选择 **Enabled** ，可选 Agent 和默认 Model，再点击 **Save changes** 。这设置独立工作的偏好，不改变默认对话 Agent：

```yaml
webui:
  sidekick:
    agent: null              # Inherit the calling Agent
    model: model-worker      # Override its Model for the requested Run
```

使用已有资源 ID。设置 `agent: agent-worker` 可选择其他 Agent；两种选择均可指定默认 Model。省略/null `model` 则跟随所选 Agent 当前的 Model。空 `sidekick: {}` 开启继承 Agent 选择。Agent 创建 Sidekick Thread 时，Host 将配置的 Model 保存为 `default_model_id`，使后续消息和恢复轮次继续使用它。显式 `create_thread(model_id=...)` 或 Run 选择器只覆盖该 Run，不改变已保存默认值。Sidekick 设置改变或关闭时，不改写已有 Thread。选择 **Disabled** 或设置 `sidekick: null` 可关闭额外指令。保存不会启动任务。新 WebUI Run 获取此偏好；活动 Run 保留捕获的指令。终端 Run 和委派子级不受影响。无论 Sidekick 是否开启，WebUI 始终提供通用 Thread、Project、Agent 和 Model 发现工具。行为和交付限制见 [Thread 协作](webui.md#agent-collaboration-and-sidekick)。

### Shell 审查快捷配置

`security.shell_review.enable` 默认 `false`，不自动注入权限/审查器。设置向导通常初始化为 `true`。关闭表示不使用快捷配置，不表示移除显式 Agent 策略。

开启时，`risk_threshold` 接受 `low`、`medium`、`high` 或 `extra_high`，`model` 指向已配置 Model 资源。`on_flagged` 接受 `deny` 或 `approval_required`；`on_error` 接受 `deny`、`approval_required` 或 `allow`。省略/null 字段继承 Agent 审查策略；缺失时回退到 `extra_high`、实际 Agent Model、标记调用的 `approval_required` 和非超时错误的 `allow`。组合时显式快捷字段优先，保留 Agent 无关规则。此配置只为根级和子级 Agent 的 `environment.shell_exec` 启用审查。默认值、合并和故障行为见 [shell 审查用法](configuration-recipes.md#configure-tool-review)。设置影响后续 Run 捕获，不影响活动 Run。

### 进程设置

这些设置在应用启动时生效；修改后需重启。

| 字段                            | 默认值   | 含义                                                            |
| ------------------------------- | -------- | --------------------------------------------------------------- |
| `process.pricing_auto_update`   | `true`   | 下载更新的模型价格，用于后续 Agent 构建                         |
| `process.terminal_update_check` | `true`   | 终端启动时检查包更新；安装仍需确认                              |
| `process.log_level`             | `INFO`   | `CRITICAL`、`ERROR`、`WARNING`、`INFO` 或 `DEBUG`，规范化为大写 |
| `process.log_format`            | `pretty` | 非交互日志：`pretty` 或 `json`；交互诊断写入文件                |

`process.max_object_bytes` 默认 `268435456`（256 MiB），允许 1 KiB 至 1 GiB。它限制每个完整的**未压缩** 不可变存储对象，包括续接检查点；不是 Thread 磁盘配额或模型上下文限制。长期编程 Thread 在模型上下文压缩后仍保留显示历史和文件编辑证据。如果检查点超限，提高限制（例如 `536870912`，即 512 MiB）并重启应用后再继续。更大限制会增加序列化和验证的峰值内存。不会截断历史以适应限制；保存失败保留之前选中的检查点。降低限制可能使之前保存的大对象无法读取。

单次覆盖使用 `--no-update-check`。见[更新与日志](automation-and-troubleshooting.md#logs-updates-and-exit)。

### Goal 检查

根级 `max_goal_iterations` 默认 `10`，接受整数，限制 `/goal` 和 WebUI Goal 开关在首次响应后的额外检查。小于或等于零关闭自动后续检查。每个新 Goal 捕获其上限；修改不会重置挂起 Goal 或改变活动 Goal 的预算。原生请求、工具和 token 限制仍适用。完成和恢复行为见[围绕 Goal 持续工作](everyday-use.md#work-toward-a-goal)。

### 长文本输入

`input.long_text_threshold_chars` 默认 `8000`。超过该字符数的用户文本块会自动保存为保留的 UTF-8 文件。模型收到文件路径和读取指令，**不是内联预览或摘要**。短文本不变。正整数可修改阈值，`null` 保持全部文本内联：

```yaml
input:
  long_text_threshold_chars: null
```

策略适用于普通根消息，以及根 Run 活动期间补充的消息。每个 Run 捕获配置；修改影响后续 Run。终端粘贴折叠相互独立，提交前仍还原原文。

选中的 Agent 必须开启内置 `view` 工具，并有可读的 `thread-files` 挂载。缺少访问时，原文保持内联并提示；不会自动开启工具。文件保存失败会使提交执行失败或拒绝补充消息。生成输入文件计入现有的八个附件、每文件 10 MiB、每输入 20 MiB 限制；HTTP 请求限制仍适用。

重启和临时文件清理后，原文仍可通过 Thread 附件句柄访问。模型历史保留引用，不自动展开文件。通过工具读取仍消耗上下文，尤其是需要整份文档的任务。此设置不转换已有历史和图像。

### 默认资源选择

| 字段                                  | 默认值 | 含义                                                           |
| ------------------------------------- | ------ | -------------------------------------------------------------- |
| `defaults.project`                    | `null` | 应用调用方的可选 Project；省略表示无 Project。终端使用启动目录 |
| `defaults.agent`                      | `null` | 默认根 Agent；设置向导填写选中的 Agent                         |
| `defaults.environment_profile`        | `null` | Environment 配置；未选择时最终使用 `environment-native`        |
| `defaults.harness_plugins`            | `[]`   | 有序的确切 Harness Plugin ID                                   |
| `defaults.environment_run_extensions` | `[]`   | 有序的确切 Environment Run Extension ID                        |
| `defaults.mcp_servers`                | `[]`   | 有序的确切 MCP 服务器 ID                                       |

列表条目必须唯一。全局默认值初始化新会话，不会静默改写已有会话的持久资源选择。Agent 级 MCP 和 Harness Plugin 选择覆盖对应默认值。选中资源的有效文件修改仍可影响后续 Run 的行为。

常规设置只写入默认 Agent 和 Environment 配置，不创建 `project-local` 资源或 `defaults.project`：

```yaml
defaults:
  agent: agent-api-key
  environment_profile: environment-native
```

应用创建的无 Project 对话使用自己的 `thread-files/tmp/` 工作目录。它仍有附件、开启时的全局 Skill 和全局指引。终端在首次提示或显式恢复时从启动目录选择 Project。从其他目录恢复已有对话，会将其分配给启动目录的 Project，不改变历史或其他设置。

Agent 可通过仅支持文件的 `configuration` 挂载读写选中的配置目录。默认是 `~/.a13n-harness-ui`；使用 `--config` 时是所选 YAML 的父目录。这不是项目工作区，该挂载不授予 shell 执行权限。已有工作挂载暴露完全相同的目录时，复用其路由。资源修改在接受前验证，影响后续 Run；无效修改保留上一版已接受配置。进程设置需重启。挂载暴露整个所选目录，因此不要将敏感文件内容放入消息和日志。

### 显示设置

| 字段                              | 默认值    | 允许值 / 含义                                    |
| --------------------------------- | --------- | ------------------------------------------------ |
| `display.theme`                   | `auto`    | `auto`、`dark`、`light`                          |
| `display.mode`                    | `concise` | `concise`、`detailed`                            |
| `display.show_status`             | `true`    | 显示状态行                                       |
| `display.max_tool_result_lines`   | `5`       | 结果预览预算，1–200 行；按工具紧凑渲染时可能更少 |
| `display.max_tool_argument_chars` | `8192`    | 保留参数的显示预算，128–65536 个字符             |

启动时读取显示默认值。`--display` 和实时 `/mode` 覆盖配置模式。`/theme` 改变当前会话主题。这些选项只影响展示，不影响模型推理或权限。

### 内置工具与 subagent

| 字段                                | 默认值 | 含义                                                            |
| ----------------------------------- | ------ | --------------------------------------------------------------- |
| `tools.enable_ask_user_question`    | `true` | 在新解析的 Run 中包含原生 `ask_user_question`                   |
| `tools.interaction_timeout_seconds` | `120`  | 每次终端交互或完整 WebUI 根决策批次的正有限秒数；不限制模型执行 |
| `tools.enable_codeact`              | `true` | 包含原生 CodeAct runner 和显式 `store`/`load`/`forget` 状态工具 |
| `subagents.include`                 | `[]`   | 有序的内置名称：`code-reviewer`、`executor`、`explorer`         |

设置向导将三项 `tools` 字段显式写入所选根 YAML（默认 `~/.a13n-harness-ui/a13n-harness-ui.yaml`），用上述默认值补齐省略字段，保留现有值。

全局关闭开关优先于显式 Agent 能力选择。工具允许列表仍适用。交互到期不会选择答案或批准命令。[终端决策处理](everyday-use.md#approvals-and-questions)和 [WebUI 超时](webui.md#questions-and-approval-timeouts)分别说明等待和恢复生命周期。

全部内置角色使用 `[code-reviewer, executor, explorer]`；部分选择可用 `[explorer]` 等。高级设置提供全部或不选；常规设置使用入门选择。定义仍由包维护；包含选择不会写入 `subagents/*.md`。[内置 subagent](agents-and-subagents.md#built-in-subagents)说明继承和名称冲突。

## 优先级与修改生效时间

启动时，Agent 或实际审查器引用的 Model 缺失会阻止应用。错误指出文件、字段和 Model ID。修复引用或添加 Model 后重启。关闭的 shell 审查快捷配置不要求审查 Model。

运行中的 App 观察配置变化，并接受稳定、完整、有效的配置树。这与编辑器保存不同步。无效或不完整候选会保留上一版已接受配置，并产生诊断。`config validate` 主动检查配置树；`config show` 报告已接受配置，可能与磁盘无效文件不同。

新 Thread 的选择优先级为：显式创建/启动选择、选中 Project 默认值、选中 Agent 在 Plugin/MCP 维度的默认值、根 YAML 默认值，最后为内置 Environment 回退。集合替换整个列表；空列表不选任何项。已有 Thread 保留确切选择，直到显式 patch；修改 Project 默认值不会重新应用。见 [Project 默认值](environments-and-projects.md#defaults-for-new-conversations)。显式实时/会话选择仍作为所属操作或会话的覆盖。Agent 和 Model 选择不同：`/agent` 改变 Thread 的 Agent，不写 YAML；`/model` 改变实际 Model，并在本地状态中按 Project 记住，不改 YAML。`/model default` 清除偏好。显式启动 `--agent` 和非交互调用方不继承记住的 Model。

| 修改内容                                                          | 生效时间                                                     | 保持不变的内容                                          |
| ----------------------------------------------------------------- | ------------------------------------------------------------ | ------------------------------------------------------- |
| `process.*` 和启动路径                                            | 重启进程                                                     | 已打开 App 捕获的启动设置                               |
| `display.*`                                                       | 终端后端初始化；`/mode` 和 `/theme` 可修改实时展示           | 展示命令不会改写 YAML                                   |
| 默认 Agent、Project、Environment、插件、MCP 和 Run Extension 选择 | 初始化新 Thread，或使用受支持的显式选择变更                  | 已有 Thread 保留所选 ID                                 |
| 选中 Model/Agent/扩展资源的内容                                   | 新捕获的 Run 使用已接受资源                                  | 活动或已捕获组合不会重建                                |
| 工具开关和内置 subagent 选择                                      | 新解析的 Run 组合                                            | 已有 Run 工具/子级契约                                  |
| `input.long_text_threshold_chars`                                 | 根 Run 捕获，包括后续指导输入                                | 活动 Run 的输入策略                                     |
| `tools.interaction_timeout_seconds`                               | 打开终端交互时读取；WebUI 批次截止时间在 Run 受理时捕获      | 已有计时器、模型执行，以及 App 重启后保留的未回答检查点 |
| 含 MCP 字面值的源文件                                             | 新捕获使用新来源；旧捕获在构造客户端前验证来源               | 已构造客户端保留 Run 本地值；旧来源改变可能导致验证失败 |
| Skill 内容                                                        | 准备目录时使用 Run 当前来源集；通过 Environment 访问读取文件 | 目录成员在 Run 内固定，但文件字节不复制到不可变组合     |

恢复时根据当前资源还原 Thread 所选 Agent，并保留当前 TUI 的显式 Model 覆盖。后续终端启动优先使用恢复 Thread 保存的默认 Model；没有时加载启动 Project 上次手动选中的 Model。不会从 Thread 历史推断 Model。没有 Model 覆盖时，只有续接使用了实际 Thread 默认或 Agent Model，才会恢复已保存推理设置。`--resume` 不能与 Agent、Environment 或标题启动覆盖同时使用；先恢复，再显式改变选择。

多文件修改应保存整棵树、验证，并在接受后启动下一 Run。不要删除本地状态来强制重载。[MCP 捕获](mcp.md#literal-values-and-environment-references)和 [Skill 来源生命周期](skills-and-content-plugins.md#automatic-sources-and-precedence)说明文件内容边界。

## 数据根与环境变量

数据根存放本地会话、按 Project 的终端 Model 偏好、不可变捕获、日志、已存 API 密钥和已安装 Content Plugin。选择顺序为：

1. `--data-root PATH`。
2. `A13N_HARNESS_UI_DATA_ROOT`。
3. `<configuration-directory>/data`。

改变数据根会打开独立状态，不迁移旧会话。相对启动路径从启动目录解析。Project 根目录在展开 `~` 后必须为绝对路径；缺失根目录可以保存，但可用前不能用于 Run。

| 输入                                         | 用途                                                         |
| -------------------------------------------- | ------------------------------------------------------------ |
| `A13N_HARNESS_UI_DATA_ROOT`                  | 选择本地数据存储                                             |
| `authentication.env` 指定的变量              | 从 Harness UI 进程读取 Model API 密钥                        |
| MCP `environment` / `headers` 引用指定的变量 | MCP 环境和请求值                                             |
| `CODEX_HOME`                                 | 兼容 Codex 存储位置；默认 `~/.codex`                         |
| `GROK_AUTH_PATH`、`GROK_HOME`                | 兼容 Grok 文件存储位置                                       |
| `GROK_AUTH`                                  | 可识别的 Grok 内联模式，不支持共享可写登录；应切换为文件存储 |
| `COLORFGBG`                                  | 自动主题选择使用的被动终端元数据                             |

没有通用 `A13N_HARNESS_UI_*` 设置覆盖机制。`storage`、`envd_runtime` 和应用关闭超时是嵌入/运行时设置，**不是** 根 YAML 章节。Web 监听和身份验证选项是[进程本地 CLI 参数](webui.md)，不是资源配置。

仍接受旧输入键 `tools.ask_user_question_timeout_seconds`。保存配置使用 `tools.interaction_timeout_seconds`。编辑响应不重启 Host 计时器；到期会拒绝，不会批准或虚构结果。

## 出站 HTTP 代理

启动 Harness UI 前设置标准环境变量；无须 YAML 代理设置：

```bash
export http_proxy=http://127.0.0.1:8888
export https_proxy=http://127.0.0.1:8888
export no_proxy=localhost,127.0.0.1,::1
```

支持大写形式和 `ALL_PROXY`。选择和绕过匹配遵循 `httpx2`。Host 所有的 Web 搜索/抓取/获取/下载请求、远程 HTTPS MCP 连接和更新检查，以及 [Model HTTP 客户端](../a13n-harness/models.md#outbound-http-proxies)均遵循这些变量。改变环境后重启进程。容器运行时，代理地址必须能从容器访问。

配置的代理是可信出站基础设施。URL 验证、本地 DNS 预检查、TLS 验证、重定向检查和响应限制仍适用，但最终 DNS 解析和目的网络限制由代理负责。直接连接的 Web 请求（包括 `NO_PROXY` 绕过）保留已有 IP 检查和固定机制。没有自定义 IP CONNECT 协议，代理失败也不会回退为直连。

明文回环 MCP 和明文本地/provider 私有 Envd 附加仍直连。HTTPS Envd 附加遵循代理变量。第三方 SDK 所有的传输保留 SDK 代理行为；守护进程发起的 Envd 配对和反向 WebSocket 连接，与 Python HTTP 附加客户端相互独立。
