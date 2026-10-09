---
title: 配置参考
description: 根配置文件、加载优先级，以及修改何时生效。
---

先运行 `a13n-harness-ui setup`，再编辑生成的 YAML 和 Markdown 文件。根 YAML 保存应用设置和默认值。Model 文件保存连接和请求设置。Agent 文件保存指令、工具和 Model 选择。

具体示例见[常见配置用法](configuration-recipes.md)。本页提供根文件和加载规则参考；本页中的对话指 TUI 和 WebUI 中显示的根 Thread。[内置配置 Skill](skills-and-content-plugins.md#built-in-configuration-skill) 为 Agent 提供与安装版本匹配的离线指引。

## 找到对应设置

| 要配置的内容                                     | 打开的位置                          | 参考                                                                     |
| ------------------------------------------------ | ----------------------------------- | ------------------------------------------------------------------------ |
| 启动检查和日志                                   | `a13n-harness-ui.yaml` → `process`  | [进程设置](#process-settings)                                            |
| 默认 Agent、Environment、插件或 MCP              | `a13n-harness-ui.yaml` → `defaults` | [默认选择](#default-resource-selections)                                 |
| 主题和输出详细程度                               | `a13n-harness-ui.yaml` → `display`  | [显示设置](#display-settings)                                            |
| 提问、CodeAct、内置子级                          | 根 `tools` 和 `subagents`           | [内置工具](#built-in-tools-and-subagents)                                |
| 提供方、API 密钥引用、端点、推理、上下文         | `models/*.yaml`                     | [Model 字段](models-and-authentication.md#model-file-reference)          |
| 指令、Capability、可见工具、子级                 | `agents/*.yaml`                     | [Agent 字段](agents-and-subagents.md#agent-file-reference)               |
| 本地根目录和 Device 绑定                         | `projects/*.yaml`                   | [Project 字段](environments-and-projects.md#project-file-reference)      |
| 远程 Device 连接                                 | `devices/*.yaml`                    | [Device 配置](environments-and-projects.md#add-device-bindings)          |
| 外部工具                                         | `mcp/*.yaml` 或 `mcp/*.json`        | [MCP 字段](mcp.md#mcp-field-reference)                                   |
| Harness Plugin、Environment 配置和 Run Extension | `extensions/*.yaml`                 | [扩展字段](extensions-and-mcp.md#harness-plugin-and-run-extension-files) |

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
| `subagents/*.md`           | 轻量子级指令                                    | [Markdown subagent](agents-and-subagents.md#write-a-markdown-subagent) |
| `projects/*.yaml`          | 本地根目录、Device 选择和创建默认值             | [Project](environments-and-projects.md#project-file-reference)         |
| `devices/*.yaml`           | 可复用 Device 连接和凭据引用                    | [Device](environments-and-projects.md#add-device-bindings)             |
| `extensions/*.yaml`        | Harness Plugin、Environment 配置、Run Extension | [扩展](extensions-and-mcp.md)                                          |
| `mcp/*.yaml`、`mcp/*.json` | MCP 服务器定义                                  | [MCP](mcp.md)                                                          |

各目录只扫描一层，不递归或合并祖先目录。扩展名区分大小写：`subagents/` 接受 `.md`，但排除 `README.md`；其他目录接受 `.yaml`；`mcp/` 还接受 `.json`。每个文件定义一个资源，MCP 多服务器 `mcpServers` 格式除外。环境和 header 引用见 [MCP 配置](mcp.md)。

支持 schema 版本中的未知新增字段会保留并警告，不会应用。不支持的版本、重复 ID 或键、YAML alias 或 anchor，以及无效引用会拒绝候选配置。

### 被跳过的 Capability

Harness UI 跳过缺失或无效的 Agent Capability，保留有效条目，不修改 YAML。警告指出 Agent、Capability 和原因。TUI 显示警告，`config validate` 和 App 状态公开 `capability_warnings`。仅有这些警告不会使验证失败。

修正名称或参数、安装可信实现，或移除条目。无效显式默认值保持跳过，不换成权限更广的选择。权限策略不同：无效 `ToolPermissionsCapability` 或缺少审查 Model 会拒绝验证和组合，即使根快捷配置已关闭。修复 Agent 策略、`security.shell_review` 或其 Model。Environment 权限和工具开关仍适用。

新 Run 只捕获有效选择。已捕获 Run 不会改变；运行时/模型提供方故障也不会转为配置警告。无效 YAML 结构、Model 资源、Environment 或 Plugin 配置仍需修复。

## 入门根配置

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
mcp:
  host_owned_servers: []
  protocol_overrides: {}
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

文件记忆默认开启。全局事实存于选中 YAML 旁的 `memory/global/`，Project 事实存于 `memory/projects/<project-id>/`。无 Project 对话只使用全局记忆。`MEMORY.md` 保持为始终加载的短索引，详细主题放在独立文件。记忆不是对话历史，不限制 Full Control 或人工访问。

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

**Organization model** 省略或为 null 时跟随 `defaults.agent`，Model ID 可覆盖。必须配置 Model。整理器只使用范围内记忆工具，不使用 Agent 指令或其他工具。设置向导开启记忆和整理，保留现有选择。

在 WebUI 中，新输入可触发已修改记忆范围的整理。每个范围有只读 Memory Thread。整理不读取旧对话或其他 Project，可能消耗 Model 配额或产生费用。General 设置显示活动、结果和用量。

| 整理器状态                         | 下次尝试                               |
| ---------------------------------- | -------------------------------------- |
| 记忆未变化、为空、忙碌或处于冷却期 | 不发出 Model 请求                      |
| 尝试成功                           | 一小时后，由另一条输入触发             |
| 尝试失败、取消或中断               | 十五分钟后，由另一条输入触发           |
| 正在尝试                           | 最多十二个请求、五分钟；新输入不中断它 |

设置 `auto_organize.enabled: false`，保留记忆而不发出自动请求。设置 `memory.enabled: false`，停止为后续 Run 绑定记忆。关闭任一开关或停止服务器会取消维护，保留部分修改；两个开关都不删除文件。已受理的前台 Run 保留捕获的设置。

将选中 YAML 旁的 `memory/` 与应用数据分开备份。`.a13n-memory/` 保存内部锁和整理状态。可选 Git 提供有界 diff 提示，不存储版本历史，也不操作你的仓库。

### 媒体理解

`media_understanding.image`、`.video` 和 `.audio` 选择已保存 Model ID，供活动 Model 无法原生接受媒体时作为文件 `view` 回退。每项默认 `null`，保留对应的 Harness 环境变量回退。在 **Settings → Models** 或 `/model defaults` 中配置。优先级、模型输入能力要求和 Run 捕获行为见[媒体理解默认值](models-and-authentication.md#media-understanding-defaults)。

### WebUI 允许的 origin

`webui.allowed_origins` 在监听器已有绑定地址和回环访问规则上增加公共请求地址，默认 `[]`。每项为确切的 HTTP(S) origin（包含非默认端口），或允许任意请求地址的字面值 `"*"`。允许尾部 `/` 并规范化移除；默认端口（HTTP 的 `80`、HTTPS 的 `443`）会规范化。不支持路径、凭据、查询、片段和 `https://*.example.com` 等部分通配符。

```yaml
webui:
  allowed_origins:
    - "https://anui.wh1isper.top:8090/"
```

`allowed_origins: ["*"]` 移除 Host 限制，包括 DNS 重绑定保护，应优先使用确切 origin。这不是 CORS：认证和同源检查仍有效，已配置 origin 也不能相互调用 API。已有绑定地址和回环访问仍允许。

监听器在启动时捕获设置。编辑后重启 WebUI；接受配置重载不会改变活动监听器的访问边界。HTTPS 转发和代理信任见[反向代理](webui.md#reverse-proxies-and-public-addresses)。

### MCP 生命周期与协议

根 `mcp.host_owned_servers` 选择在 CLI 和 WebUI 中跨逻辑 Run 保留客户端的服务器 ID，不会将它们加入 Agent 工具选择。`mcp.protocol_overrides` 将已有服务器 ID 映射到 `auto`、`legacy` 或 `2026-07-28`；省略的 ID 使用 SDK 自动协商。默认值为 `[]` 和 `{}`，保留现有配置行为。见 [MCP 连接生命周期](mcp.md#connection-lifetime-and-protocol)和[人工输入](mcp.md#human-input-from-mcp-servers)。这些设置捕获供后续 Run 使用；实时客户端和输入答案不是配置或续接状态。

### WebUI MCP Apps

交互式 MCP 结果需主动启用。设置 `webui.mcp_apps.enabled: true`，并在 `webui.mcp_apps.servers` 中选择服务器 ID。WebUI 自动将它们与通用 MCP 选择一同加入每个根级和子级 Agent，去重 ID，不修改 Agent 文件或已保存 Thread 选择。CLI 仍只支持通用 MCP。开启 Apps 或改变独立 origin 沙箱监听器后需重启 WebUI。字段参考、交互权限以及本地、Docker 和反向代理配置见 [MCP Apps](mcp-apps.md)。

### WebUI Sidekick

Sidekick 为 WebUI 根 Agent 添加独立工作指令和 `create_thread` 默认值。省略设置或使用 `sidekick: {}` 表示开启，`sidekick: null` 关闭。向导为新文件写入 `{}`，保留已有关闭或自定义选择。

在 **Settings → General → Sidekick** 中选择 **Enabled** ，可选 Agent 和默认 Model，再点击 **Save changes** 。这设置独立工作的偏好，不改变默认对话 Agent：

```yaml
webui:
  sidekick:
    agent: null              # Inherit the calling Agent
    model: model-worker      # Default Model for newly created Sidekick Threads
```

使用已有 ID。设置 `agent: agent-worker` 选择其他 Agent。省略/null `model` 跟随该 Agent 当前的 Model；`sidekick: {}` 继承调用方 Agent。配置的 Model 会保存为新 Thread 的 `default_model_id`，供后续消息和恢复轮次使用。显式 `create_thread(model_id=...)` 或 Run 选择器只覆盖该 Run。

选择 **Disabled** 或设置 `sidekick: null` 移除额外指令。保存不启动任务，也不改写已有 Thread。偏好影响新 WebUI Run，不影响活动 Run、TUI Run 或委派子级。发现工具独立于 Sidekick，仍遵循各角色范围。Coordinator 和 Worker 限制及交付行为见 [Thread 协作](webui.md#agent-collaboration-and-sidekick)。

### Shell 审查快捷配置

`security.shell_review.enable` 默认 `false`，不自动注入权限/审查器。设置向导通常初始化为 `true`。关闭表示不使用快捷配置，不表示移除显式 Agent 策略。

开启后仅审查根及子级的 `environment.shell_exec`。显式快捷字段覆盖 Agent 的 shell 规则，其他规则保留。省略/null 字段先继承 Agent 审查设置，再用内置默认值。字段值、合并和故障处理见 [shell 审查用法](configuration-recipes.md#configure-shell-review)。修改影响后续 Run 捕获。

设置 `security.shell_review.guardian_credits: true`，可为实际启用的 shell 审查请求 Guardian 额度关联。默认值为 `false`。首次设置时，Codex 订阅连接写入 `true`，API 密钥和其他连接写入 `false`；选择已有 Model 时也遵循此规则。设置向导和 Add Agent 保留现有 shell 审查配置，Add Model 不修改它。

两个 Model 都必须使用 `openai-codex:` 或 `openai-responses:`。关联使用请求执行命令的调用所对应的提供方响应 ID，包括经由 CodeAct 或 ToolProxy 发起的命令。不支持的协议或缺少 ID 时，保留普通审查和诊断。

Guardian 关联不改变审查器、凭据、端点、service tier、风险策略或用量记录。提供方拒绝请求时遵循 `on_error`，不移除关联后重试；超时拒绝执行。API 密钥的 Responses 连接也可主动开启，但资格和实际费用由提供方决定。

### 进程设置

这些设置在应用启动时生效；修改后需重启。

| 字段                            | 默认值   | 含义                                                            |
| ------------------------------- | -------- | --------------------------------------------------------------- |
| `process.pricing_auto_update`   | `true`   | 下载更新的模型价格，用于后续 Run 的费用估算                     |
| `process.terminal_update_check` | `true`   | TUI 启动时检查包更新；安装仍需确认                              |
| `process.log_level`             | `INFO`   | `CRITICAL`、`ERROR`、`WARNING`、`INFO` 或 `DEBUG`，规范化为大写 |
| `process.log_format`            | `pretty` | 非交互日志：`pretty` 或 `json`；交互诊断写入文件                |

`process.max_object_bytes` 限制每个未压缩存储对象，包括检查点：默认 256 MiB（`268435456`），范围 1 KiB–1 GiB，不是 Thread 配额或上下文限制。检查点超限时提高限制并重启后继续；更大限制增加峰值内存。不会截断历史，保存失败保留上一检查点。降低限制可能无法读取较大的已存对象。

官方模型属性和 Harness 价格补充独立于 `process.pricing_auto_update` 刷新。该后台刷新默认开启，从仓库 GitHub `main` 读取一个完整验证的数据文件。启动前设置 `A13N_OFFICIAL_MODELS_AUTO_UPDATE=0` 可关闭。下载失败保留最后有效数据或包内数据，使用指数退避和抖动重试。刷新不改写已保存的 Model，也不改变活动 Run；PDF 输入仍需手动启用。离线运行应同时关闭此刷新和 `process.pricing_auto_update`。

单次覆盖使用 `--no-update-check`。见[更新与日志](automation-and-troubleshooting.md#logs-updates-and-exit)。

### Goal 检查

根级 `max_goal_iterations` 默认 `10`，接受整数，限制 `/goal` 和 WebUI Goal 开关在首次响应后的额外检查。小于或等于零关闭自动后续检查。每个新 Goal 捕获其上限；修改不会重置挂起 Goal 或改变活动 Goal 的预算。原生请求、工具和 token 限制仍适用。完成和恢复行为见[围绕 Goal 持续工作](everyday-use.md#work-toward-a-goal)。

### 长文本输入

`input.long_text_threshold_chars` 默认 `8000`。超过该字符数的用户文本块会自动保存为保留的 UTF-8 文件。模型收到文件路径和读取指令，**不是内联预览或摘要**。短文本不变。正整数可修改阈值，`null` 保持全部文本内联：

```yaml
input:
  long_text_threshold_chars: null
```

策略适用于普通根消息，以及根 Run 活动期间补充的消息。每个 Run 捕获配置；修改影响后续 Run。TUI 粘贴折叠相互独立，提交前仍还原原文。

选中的 Agent 必须开启内置 `view` 工具，并有可读的 `thread-files` 挂载。缺少访问时，原文保持内联并提示；不会自动开启工具。文件保存失败会使提交执行失败或拒绝补充消息。生成输入文件计入现有的八个附件、每文件 10 MiB、每输入 20 MiB 限制；HTTP 请求限制仍适用。

重启和临时文件清理后，原文仍可通过 Thread 附件句柄访问。模型历史保留引用，不自动展开文件。通过工具读取仍消耗上下文，尤其是需要整份文档的任务。此设置不转换已有历史和图像。

### 默认资源选择

| 字段                                  | 默认值 | 含义                                                           |
| ------------------------------------- | ------ | -------------------------------------------------------------- |
| `defaults.project`                    | `null` | 应用调用方的可选 Project；省略表示无 Project。TUI 使用启动目录 |
| `defaults.agent`                      | `null` | 默认根 Agent；设置向导填写选中的 Agent                         |
| `defaults.environment_profile`        | `null` | Environment 配置；未选择时最终使用 `environment-native`        |
| `defaults.harness_plugins`            | `[]`   | 有序的确切 Harness Plugin ID                                   |
| `defaults.environment_run_extensions` | `[]`   | 有序的确切 Environment Run Extension ID                        |
| `defaults.mcp_servers`                | `[]`   | 有序的确切 MCP 服务器 ID                                       |

列表条目必须唯一。全局默认值初始化新 Thread，不会静默改写已有 Thread 的持久资源选择。Agent 级 MCP 和 Harness Plugin 选择覆盖对应默认值。选中资源的有效文件修改仍可影响后续 Run 的行为。

常规设置只写入默认 Agent 和 Environment 配置，不创建 `project-local` 资源或 `defaults.project`：

```yaml
defaults:
  agent: agent-api-key
  environment_profile: environment-native
```

应用创建的无 Project 对话使用自己的 `thread-files/tmp/` 工作目录。它仍有附件、开启时的全局 Skill 和全局指引。TUI 在首次提示或显式恢复时从启动目录选择 Project。从其他目录恢复已有对话，会将其分配给启动目录的 Project，不改变历史或其他设置。

仅支持文件的 `configuration` 挂载暴露所选 YAML 的目录，不是 Project 根目录或 shell 位置。已有相同目录的挂载时复用。有效修改影响后续 Run，无效修改保留已接受配置。进程设置需重启。整个目录均可访问，不要分享敏感内容。

### 显示设置

| 字段                              | 默认值    | 允许值 / 含义                                    |
| --------------------------------- | --------- | ------------------------------------------------ |
| `display.theme`                   | `auto`    | `auto`、`dark`、`light`                          |
| `display.mode`                    | `concise` | `concise`、`detailed`                            |
| `display.show_status`             | `true`    | 显示状态行                                       |
| `display.max_tool_result_lines`   | `5`       | 结果预览预算，1–200 行；按工具紧凑渲染时可能更少 |
| `display.max_tool_argument_chars` | `8192`    | 保留参数的显示预算，128–65536 个字符             |

启动时读取显示默认值。`--display` 和实时 `/mode` 覆盖配置模式。`/theme` 改变主题，直到你退出。这些选项只影响展示，不影响模型推理或权限。

### 内置工具与 subagent

| 字段                                | 默认值 | 含义                                                             |
| ----------------------------------- | ------ | ---------------------------------------------------------------- |
| `tools.enable_ask_user_question`    | `true` | 在新解析的 Run 中包含原生 `ask_user_question`                    |
| `tools.interaction_timeout_seconds` | `120`  | 每次 TUI 交互或完整 WebUI 根决策批次的正有限秒数；不限制模型执行 |
| `tools.enable_codeact`              | `true` | 包含原生 CodeAct runner 和显式 `store`/`load`/`forget` 状态工具  |
| `subagents.include`                 | `[]`   | 有序的内置名称：`code-reviewer`、`executor`、`explorer`          |

设置向导将三项 `tools` 字段显式写入所选根 YAML（默认 `~/.a13n-harness-ui/a13n-harness-ui.yaml`），用上述默认值补齐省略字段，保留现有值。

全局关闭开关优先于显式 Agent Capability 选择。工具允许列表仍适用。交互到期不会选择答案或批准命令。[TUI 决策处理](everyday-use.md#approvals-and-questions)和 [WebUI 超时](webui.md#questions-and-approval-timeouts)分别说明等待和恢复生命周期。

全部内置角色使用 `[code-reviewer, executor, explorer]`；部分选择可用 `[explorer]` 等。高级设置提供全部或不选；常规设置包含全部三种。定义仍由包维护；包含选择不会写入 `subagents/*.md`。[内置 subagent](agents-and-subagents.md#built-in-subagents)说明继承和名称冲突。

## 优先级与修改生效时间

启动时，Agent 或实际审查器引用的 Model 缺失会阻止应用。错误指出文件、字段和 Model ID。修复引用或添加 Model 后重启。关闭的 shell 审查快捷配置不要求审查 Model。

运行中的 App 观察配置变化，并接受稳定、完整、有效的配置树。这与编辑器保存不同步。无效或不完整的修改会保留上一版已接受配置，并产生诊断。`config validate` 主动检查配置树；`config show` 报告已接受配置，可能与磁盘无效文件不同。

新 Thread 采用以下顺序中第一个匹配的选择：

1. 显式创建或启动选择。
2. 选中 Project 的默认值。
3. 选中 Agent 的 Plugin 和 MCP 默认选择。
4. 根 YAML 默认值。
5. 内置 Environment 回退。

列表替换而不合并，`[]` 不选任何项。已有 Thread 保留选择，直到明确修改；修改 Project 默认值不重新应用。见 [Project 默认值](environments-and-projects.md#defaults-for-new-conversations)。临时覆盖属于对应操作、TUI 进程或标签页。`/agent` 修改 Thread Agent，`/model` 按 Project 记住选择，`/model default` 清除，两者都不写 YAML。显式启动 `--agent` 和非交互调用忽略记住的 Model。

| 修改内容                                                          | 生效时间                                                     | 保持不变的内容                                          |
| ----------------------------------------------------------------- | ------------------------------------------------------------ | ------------------------------------------------------- |
| `process.*` 和启动路径                                            | 重启进程                                                     | 已打开 App 捕获的启动设置                               |
| `display.*`                                                       | TUI 后端初始化；`/mode` 和 `/theme` 可修改实时展示           | 展示命令不会改写 YAML                                   |
| 默认 Agent、Project、Environment、插件、MCP 和 Run Extension 选择 | 初始化新 Thread，或使用受支持的显式选择变更                  | 已有 Thread 保留所选 ID                                 |
| 选中 Model/Agent/扩展资源的内容                                   | 新捕获的 Run 使用已接受资源                                  | 活动或已捕获组合不会重建                                |
| 工具开关和内置 subagent 选择                                      | 新解析的 Run 组合                                            | 已有 Run 工具/子级契约                                  |
| `input.long_text_threshold_chars`                                 | 根 Run 捕获，包括后续引导输入                                | 活动 Run 的输入策略                                     |
| `tools.interaction_timeout_seconds`                               | 打开 TUI 交互时读取；WebUI 批次截止时间在 Run 受理时捕获     | 已有计时器、模型执行，以及 App 重启后保留的未回答检查点 |
| 含 MCP 字面值的源文件                                             | 新捕获使用新来源；旧捕获在构造客户端前验证来源               | 已构造客户端保留 Run 本地值；旧来源改变可能导致验证失败 |
| Skill 内容                                                        | 准备目录时使用 Run 当前来源集；通过 Environment 访问读取文件 | 目录成员在 Run 内固定，但文件字节不复制到不可变组合     |

恢复时根据当前资源还原 Thread 所选 Agent，并保留当前 TUI 的显式 Model 覆盖。后续 TUI 启动优先使用恢复 Thread 保存的默认 Model；没有时加载启动 Project 上次手动选中的 Model。不会从 Thread 历史推断 Model。没有 Model 覆盖时，只有续接使用了实际 Thread 默认或 Agent Model，才会恢复已保存推理设置。`--resume` 不能与 Agent、Environment 或标题启动覆盖同时使用；先恢复，再显式改变选择。

多文件修改应保存整棵树、验证，并在接受后启动下一 Run。不要删除本地状态来强制重载。[MCP 捕获](mcp.md#literal-values-and-environment-references)和 [Skill 来源生命周期](skills-and-content-plugins.md#automatic-sources-and-precedence)说明文件内容边界。

## 数据根与环境变量

数据根存放已保存对话、按 Project 的 TUI Model 偏好、不可变捕获、日志、已存 API 密钥和已安装 Content Plugin。选择顺序为：

1. `--data-root PATH`。
2. `A13N_HARNESS_UI_DATA_ROOT`。
3. `<configuration-directory>/data`。

改变数据根会打开独立状态，不迁移旧对话。相对启动路径从启动目录解析。Project 根目录在展开 `~` 后必须为绝对路径；缺失根目录可以保存，但可用前不能用于 Run。

| 输入                                         | 用途                                                         |
| -------------------------------------------- | ------------------------------------------------------------ |
| `A13N_HARNESS_UI_DATA_ROOT`                  | 选择本地数据存储                                             |
| `authentication.env` 指定的变量              | 从 Harness UI 进程读取 Model API 密钥                        |
| MCP `environment` / `headers` 引用指定的变量 | MCP 环境和请求值                                             |
| `CODEX_HOME`                                 | 兼容 Codex 存储位置；默认 `~/.codex`                         |
| `COPILOT_HOME`                               | 复用账户时使用的 Copilot CLI 存储位置；默认 `~/.copilot`     |
| `GROK_AUTH_PATH`、`GROK_HOME`                | 兼容 Grok 文件存储位置                                       |
| `GROK_AUTH`                                  | 可识别的 Grok 内联模式，不支持共享可写登录；应切换为文件存储 |
| `COLORFGBG`                                  | 自动主题选择使用的被动终端元数据                             |

没有通用 `A13N_HARNESS_UI_*` 设置覆盖机制。`storage`、`envd_runtime` 和应用关闭超时是嵌入/运行时设置，**不是** 根 YAML 章节。Web 监听和身份验证选项是[进程本地 CLI 参数](webui.md)，不是资源配置。

仍接受旧输入键 `tools.ask_user_question_timeout_seconds`。保存配置使用 `tools.interaction_timeout_seconds`。编辑响应不重启 Host 计时器；到期会拒绝，不会批准或虚构结果。

只有在明确接受未验证的目标证书时，才在启动前设置 `A13N_OUTBOUND_TLS_VERIFY=false`。未设置或设为 `true` 会保留验证。这个共享进程变量不是 YAML 字段；适用范围、例外及风险见[出站 TLS 验证](../a13n-harness/models.md#outbound-tls-verification)。

## 出站 HTTP 代理

启动 Harness UI 前设置标准环境变量；无须 YAML 代理设置：

```bash
export http_proxy=http://127.0.0.1:8888
export https_proxy=http://127.0.0.1:8888
export no_proxy=localhost,127.0.0.1,::1
```

支持大写形式和 `ALL_PROXY`。选择和绕过匹配遵循 `httpx2`。Host 所有的 Web 搜索/抓取/获取/下载请求、远程 HTTPS MCP 连接和更新检查，以及 [Model HTTP 客户端](../a13n-harness/models.md#outbound-http-proxies)均遵循这些变量。改变环境后重启进程。容器运行时，代理地址必须能从容器访问。

代理负责目标 DNS 和网络限制。Host Web 不预解析或固定目标 IP，直连和 `NO_PROXY` 请求也一样。URL/重定向检查、超时和响应限制仍有效。TLS 默认验证，见[运维控制的 TLS 开关](../a13n-harness/models.md#outbound-tls-verification)。代理失败不回退直连。

明文回环 MCP 和明文本地/provider 私有 Envd 附加仍直连。HTTPS Envd 附加遵循代理变量。第三方 SDK 所有的传输保留 SDK 代理行为；守护进程发起的 Envd 配对和反向 WebSocket 连接，与 Python HTTP 附加客户端相互独立。

## Run 配置

`a13n-harness-ui.yaml` 根级 `run_configuration` 为每次接受的根 Run 及其子 Run 选择同一不可变配置：

```yaml
run_configuration:
  allowed_hosts:
    - api.example.com
    - 'regex:(?:[a-z0-9-]+\.)*docs\.example\.com'
  extensions:
    example.reader: {images: true}
```

`allowed_hosts` 省略/null 允许全部目标，`[]` 拒绝全部。条目精确匹配规范化域名或 IP，或用 `regex:` 做完整主机名 Python 匹配。示例允许 `docs.example.com` 及其子域，不允许 `docs.example.com.evil.test`。YAML 单引号保留反斜杠，无效或空表达式使验证失败。匹配对象是规范化主机，不是 URL、路径或端口；不支持 glob 和 CIDR。见[主机规则](../a13n-harness/context.md#host-rules-and-regular-expressions)。应包含所需 Model、Web 和 MCP 主机。宿主拥有的请求检查声明主机及重定向，不解析 DNS 或固定 IP。API-key Model 和 Host Web/MCP 支持限制，内部传输不可检查的订阅连接会拒绝。根及子级捕获保持固定。带命名空间的 `extensions` 需消费者主动接入，不是 Capability 参数。Shell 和可信插件网络仍需 Environment 或部署隔离。
