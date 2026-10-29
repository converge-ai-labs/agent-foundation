---
title: 配置参考
description: 找到配置所属文件、检查修改，并查阅根设置。
---

已知道要修改哪个设置时，使用本页。首次连接模型请按[设置指南](setup.md)操作；完整的编辑和测试步骤见[常见配置用法](configuration-recipes.md)。

Harness UI 区分三个位置：**配置目录**描述资源，**数据目录**保存对话和凭据，**Project** 选择工作目录。在仓库中启动 CLI，不会将仓库变成配置目录。

## 定位并验证文件

1. 找到选中的根 YAML：

   ```console
   a13n-harness-ui config path
   ```

   默认为 `~/.a13n-harness-ui/a13n-harness-ui.yaml`。下面列出的资源文件位于它的父目录中。

2. 编辑对应文件，然后检查整棵配置树：

   ```console
   a13n-harness-ui config validate
   a13n-harness-ui config show --format json
   ```

   验证会报告配置错误和 Capability 警告，不测试提供方连接。`config show` 显示已接受的配置；磁盘修改无效时，运行中的 App 可以继续使用上一版有效配置。

3. 按[修改生效时间](#what-wins-and-when-edits-apply)应用修改，再测试对应行为。

使用另一棵配置树时，对该树执行的每条命令都应在子命令前传入 `--config`：

```console
a13n-harness-ui --config /path/to/a13n-harness-ui.yaml config validate
```

路径必须以小写 `.yaml` 结尾。它选择完整配置树，不叠加到默认配置上。

## 找到对应设置

表中路径均相对于根 YAML 所在目录。资源选择使用 YAML 中的 **`id`**，不用文件名或显示名称。

| 文件或目录                   | 保存的内容                                                                                 |
| ---------------------------- | ------------------------------------------------------------------------------------------ |
| `a13n-harness-ui.yaml`       | 进程设置、默认值、显示、工具开关及下文的根级配置                                           |
| `models/*.yaml`              | [提供方连接、凭据引用和请求设置](models-and-authentication.md#model-file-reference)        |
| `agents/*.yaml`              | [指令、Model 选择、Capability 和子级](agents-and-subagents.md#agent-file-reference)        |
| `subscription_groups/*.yaml` | [有序订阅账户和选择策略](models-and-authentication.md#account-store-locations)             |
| `projects/*.yaml`            | [工作根目录、Device 绑定和创建默认值](environments-and-projects.md#project-file-reference) |
| `devices/*.yaml`             | [远程 Device 连接](environments-and-projects.md#add-device-bindings)                       |
| `extensions/*.yaml`          | [Harness Plugin、Environment 配置和 Run Extension](extensions-and-mcp.md)                  |
| `mcp/*.yaml` 或 `mcp/*.json` | [外部工具服务器](mcp.md)                                                                   |
| `subagents/*.md`             | [轻量子级指令](agents-and-subagents.md#write-a-markdown-subagent)                          |
| `AGENTS.md`                  | [全局指引](agents-and-subagents.md#instructions-and-guidance)                              |

加载器只扫描直属文件，不递归、不合并祖先目录、不跟随资源符号链接。扩展名区分大小写；排除 `subagents/README.md`。每个文件定义一个资源，MCP 多服务器 `mcpServers` 格式除外。

支持的 schema 版本会保留未知新增字段并警告，但不应用这些字段。不支持的版本、重复 ID 或键、YAML anchor/alias，以及无效引用会使候选配置被拒绝。

### 被跳过的 Capability

普通 Agent Capability 缺失或无效时会被跳过，警告指出 Agent、Capability 和原因。有效条目仍可使用，源 YAML 保持不变。TUI、App 状态和 `config validate` 都会显示 `capability_warnings`；仅有警告不会使验证失败。可修正名称或参数、安装可信实现，或移除条目。

权限策略采用更严格的规则。无效 `ToolPermissionsCapability`、无效合并后 shell 审查策略，或缺少必需审查 Model，都会使验证和组合失败。关闭根快捷配置不会关闭显式 Agent 策略。无效显式默认值不会被替换成范围更广的选择。运行时提供方故障仍作为运行时错误处理。

## 优先级与修改生效时间

新 Thread 使用以下顺序中第一个适用的选择：

1. 显式创建或启动选择。
2. 选中 Project 的默认值。
3. 选中 Agent 的 Harness Plugin 或 MCP 默认值。
4. 根 YAML 默认值。
5. 内置 Environment 回退。

列表替换而不合并；`[]` 表示不选。已有 Thread 保留所选 ID，直到显式修改。编辑已选资源会改变后续 Run 的解析结果；修改全局默认值主要影响新 Thread。

| 修改内容                                                       | 应用方式                                          |
| -------------------------------------------------------------- | ------------------------------------------------- |
| Model、Agent、扩展、工具开关或内置子级选择                     | 保存有效配置树，开始新 Run                        |
| 默认资源 ID                                                    | 创建新 Thread，或显式修改已有 Thread 的选择       |
| `process`、`envd`、`browser`、监听器 origin 或 MCP Apps 监听器 | 重启应用                                          |
| `display`                                                      | 重启 TUI；`/mode` 和 `/theme` 可改变当前展示      |
| 长文本传递策略                                                 | 开始新根 Run；后续 steering 输入使用该 Run 的策略 |
| 交互超时                                                       | 打开新的 TUI 交互，或受理新的 WebUI Run           |

App 异步接受稳定、完整的配置修改。无效或只保存了一部分的配置树会保留上一版有效配置，并报告诊断。保存所有关联文件后再验证。活动 Run 保留捕获的配置；不要通过删除对话状态来重载。启动时，缺少 Agent 或实际审查器引用的 Model 会阻止启动：修复错误中指明的引用后再重启。

**TUI 选择。** `/agent` 修改 Thread Agent。`/model` 记住按 Project 的选择；`/model default` 清除该选择。两者都不写 YAML。显式启动 `--agent` 和非交互调用忽略记住的 Model。TUI 恢复会按 Project → Agent → 全局顺序重新应用当前 MCP 默认值；其他已保存资源选择保留。见 [Project 默认值](environments-and-projects.md#defaults-for-new-conversations)。

**恢复。** Thread Agent 按当前资源解析。当前 TUI 中的显式 Model 覆盖保留；后续 TUI 启动先使用 Thread 保存的默认 Model，再使用启动 Project 记住的 Model。不会从历史推断 Model。未覆盖 Model 时，只有续接使用了实际 Thread 默认或 Agent Model，才恢复保存的推理设置。先恢复，再修改选择：`--resume` 不能与 Agent、Environment 或标题启动覆盖同时使用。

**文件内容。** 已构造的 MCP 客户端保留原值；重建旧捕获时会验证含字面值的来源，来源变化可能使验证失败。Skill 目录成员按 Run 固定，Skill 文件仍通过 Environment 读取，不复制进捕获。见 [MCP 来源](mcp.md#literal-values-and-environment-references)和 [Skill 生命周期](skills-and-content-plugins.md#automatic-sources-and-precedence)。

## 入门根配置

完成设置后，保留生成的根文件，只编辑需要的字段。手工配置时，这个小文档可选择一个**已存在**的 `agent-coder` 资源：

```yaml title="a13n-harness-ui.yaml — minimal example"
schema_version: "1"
defaults:
  agent: agent-coder
  environment_profile: environment-native
subagents:
  include: [code-reviewer, executor, explorer]
```

按[文件创建 Agent 指南](agents-and-subagents.md#create-an-agent-from-files)创建该 Agent 和 Model，或替换为自己的 Agent ID。省略字段采用下文的默认值。本页后续 YAML 块均为**要合并到根文件中的配置片段**；已有同名配置段时应合并字段，不要重复添加映射。

### 默认资源选择

| `defaults` 下的字段          | 默认值 | 用途                                          |
| ---------------------------- | ------ | --------------------------------------------- |
| `agent`                      | `null` | 根 Agent；设置向导会选择一个                  |
| `project`                    | `null` | 应用调用方的可选 Project；TUI 使用启动目录    |
| `environment_profile`        | `null` | 本地执行配置；最终回退为 `environment-native` |
| `harness_plugins`            | `[]`   | 有序且唯一的 Harness Plugin ID                |
| `environment_run_extensions` | `[]`   | 有序且唯一的 Run Extension ID                 |
| `mcp_servers`                | `[]`   | 有序且唯一的 MCP 服务器 ID                    |

设置向导写入默认 Agent 和 Environment，不创建 `project-local` 资源。应用创建的无 Project 对话在自己的 `thread-files/tmp/` 中工作，拥有附件，以及启用时的全局指引/Skill。TUI 在首次输入或显式恢复时根据启动目录选择 Project。从另一个目录恢复会重新分配 Project，不替换历史或其他设置。

仅支持文件的 `configuration` 挂载暴露选中 YAML 的目录。已有相同目录的挂载时会复用；它不设定 shell 工作目录。整个目录均可访问，包括其中的敏感文件。

### 显示设置

| `display` 下的字段        | 默认值    | 取值或范围                      |
| ------------------------- | --------- | ------------------------------- |
| `theme`                   | `auto`    | `auto`、`dark`、`light`         |
| `mode`                    | `concise` | `concise`、`detailed`           |
| `show_status`             | `true`    | 显示状态行                      |
| `max_tool_result_lines`   | `5`       | 1–200 行预览；紧凑渲染可能更少  |
| `max_tool_argument_chars` | `8192`    | 保留用于显示的 128–65536 个字符 |

`--display` 和 `/mode` 覆盖模式。`/theme` 持续到退出。显示设置影响展示，不改变推理或权限。

### 内置工具与 subagent

| 根字段                                    | 默认值      | 用途                                                  |
| ----------------------------------------- | ----------- | ----------------------------------------------------- |
| `tools.enable_ask_user_question`          | `true`      | 包含原生提问工具                                      |
| `tools.enable_codeact`                    | `true`      | 包含 CodeAct 及 `store`/`load`/`forget`               |
| `tools.interaction_timeout_seconds`       | `120`       | 每次 TUI 交互或 WebUI 根决策批次的正有限秒数          |
| `tools.execution.mode`                    | `inline`    | `inline` 等待；`auto` 为支持的通用工具启用定时交接    |
| `tools.execution.foreground_wait_seconds` | `5.0`       | 非负有限等待时间；支持小数秒                          |
| `input.steering_mode`                     | `immediate` | `immediate` 中断等待；`queued` 等待安全边界           |
| `subagents.include`                       | `[]`        | 有序唯一名称：`code-reviewer`、`executor`、`explorer` |

常规设置包含全部三个内置子级。高级设置提供全部或不选；编辑列表可选择子集。定义由包维护，不会复制 Markdown 文件。用 `a13n-harness-ui config subagents` 检查包含情况。见[子级继承和冲突](agents-and-subagents.md#built-in-subagents)。

全局关闭的工具开关覆盖显式 Agent Capability 选择；工具允许列表仍生效。设置向导补齐省略的工具字段，保留已有值。旧键 `tools.ask_user_question_timeout_seconds` 仍可输入，保存使用 `interaction_timeout_seconds`。超时不会编造答案或批准命令；编辑答案不会重新计时。见 [TUI 决策](everyday-use.md#approvals-and-questions)和 [WebUI 决策](webui.md#questions-and-approval-timeouts)。

要缩短前台等待，合并：

```yaml title="a13n-harness-ui.yaml — tool execution"
tools:
  execution:
    mode: auto
    foreground_wait_seconds: 0.05
```

这表示等待 50 ms，不是 50 ms 执行期限。支持进程的顶层 Shell 调用在**两种模式**下都会在等待结束后返回回执；快速命令直接返回结果。仅支持前台的 Shell 使用通用策略。即时 steering 可提前释放两种等待，原始调用继续按捕获的设置执行。

存活的 `exec-*` Shell 执行可在同一 App 内跨 Run 控制。CodeAct 内的 Shell 返回原生结果和 Run 本地进程引用；只有外层程序可交接。工具记录和有界完成历史保留在 App 内存中。见[执行状态与取消](webui.md#tool-executions-and-immediate-steering)。

### Shell 审查快捷配置

按 [shell 审查用法](configuration-recipes.md#configure-shell-review)选择并测试审查器。根 `security.shell_review.enable` 默认 `false`。启用后，根及子级 Run 的 `environment.shell_exec` 接受审查。显式快捷字段覆盖 Agent 的 shell 规则，包括 `allow`、`deny` 或 `ask`；其他规则和审查指令保留。

省略/null 字段先继承 Agent 策略，再采用以下默认值：

| `security.shell_review` 下的字段 | 继承后的默认值      | 取值                                                          |
| -------------------------------- | ------------------- | ------------------------------------------------------------- |
| `risk_threshold`                 | `extra_high`        | `low`、`medium`、`high`、`extra_high`；达到或超过该级别时标记 |
| `model`                          | 实际 Agent Model    | 已保存 Model ID                                               |
| `on_flagged`                     | `approval_required` | `deny`、`approval_required`                                   |
| `on_error`                       | `allow`             | 非超时错误使用 `deny`、`approval_required` 或 `allow`         |

审查器超时始终拒绝执行。`allow` 继续执行剩余权限检查；人工审批有独立交互超时。合并后策略无效或必需 Model 引用无效会使验证失败。关闭快捷配置保留显式 Agent 策略。显式策略需使用一个带嵌套 `review` 的 `ToolPermissionsCapability`，并为评估的工具设置 `review` 权限；只定义审查器不会启用审查。

**Guardian 关联。** `guardian_credits: true` 请求提供方额度关联；默认 `false`。新设置为 Codex 订阅连接开启，为 API 密钥/其他连接关闭，选择已有 Model 时也如此。设置向导和 Add Agent 保留已有 shell 审查设置；Add Model 不修改它。

两个 Model 都必须使用 `openai-codex:` 或 `openai-responses:`。关联使用请求方的提供方响应 ID，包括经 CodeAct/ToolSearch 发起的调用。协议不支持或缺少 ID 时保留普通审查和诊断。关联不改变凭据、端点、service tier、策略或用量记录。提供方拒绝时按 `on_error` 处理，不移除关联重试；超时拒绝。API 密钥 Responses 可主动开启，资格和费用由提供方决定。

### 长文本输入

`input.long_text_threshold_chars` 默认 `8000`。更长的用户文本块会变为保留的 UTF-8 附件；Model 收到路径和读取指令。正整数可改变阈值，`null` 保持文本内联：

```yaml title="a13n-harness-ui.yaml — keep long text inline"
input:
  long_text_threshold_chars: null
```

选中 Agent 需要 `view` 和可读的 `thread-files` 挂载。否则文本保持内联并提示。保存失败会使提交失败或拒绝补充消息。生成文件共用附件限制：八个文件、每个 10 MiB、每次输入 20 MiB，以及 HTTP 请求限制。

策略覆盖根输入和后续 steering，使用该 Run 捕获的设置。原文在重启和临时文件清理后仍可通过附件句柄访问。模型历史保留引用；工具读取仍消耗上下文。已有历史、图像和 TUI 粘贴折叠不变。

### Goal 检查

`max_goal_iterations: 10` 允许 `/goal` 或 WebUI Goal 开关在首次响应后额外检查十次。小于或等于零关闭自动后续检查。新 Goal 捕获其上限；修改不重置挂起 Goal，也不改变活动预算。请求、工具和 token 限制同样生效。见 [Goal 行为和恢复](everyday-use.md#work-toward-a-goal)。

### 后台压缩

带 `compaction` Capability 的 Agent 可以在根 Run 继续时总结早期历史。默认值：

```yaml title="a13n-harness-ui.yaml — background compaction"
background_compaction:
  enabled: true
  blocking_threshold: 0.95
```

普通触发线为 90% 上下文占用。后台执行需要已验证的剩余容量。精确计数的输入占用达到 95% 时，Run 等待已有工作或同步压缩；输出预留可能要求更早等待。`blocking_threshold` 大于零且不超过一。容量未知/不足且没有后台操作时使用同步压缩；后台失败也会在当前用量限制下回退到同步压缩。子级同步压缩。

已完成摘要先保存，再在安全的模型调用边界应用，或由后续 Run 连同新输入一起恢复。摘要完成不会直接改写空闲历史。重启恢复完成结果，不重放中断的摘要请求。

清理在启动时及每小时执行。已处理摘要先随检查点标记，再删除；未使用工作七天后过期，活跃 Run/请求受到保护，对话历史保留。嵌入式 App 可修改 `StorageSettings.compaction_retention_seconds`。关闭后台模式影响后续 Run；已保存未过期摘要仍可使用，Model 预算不变。

### 上下文恢复

上下文恢复在根 Agent 和子 Agent 中默认启用，独立于 `compaction` Capability。发生原生上下文窗口溢出后，Harness 先尝试正常压缩。摘要成功后，Run 使用压缩后的历史继续执行。这是运行时行为；Agent YAML 单独配置主动压缩。

只有普通执行和适用的正常压缩都溢出时，Harness UI 才尝试有界、有损的历史重建，每个 Run 最多一次。历史会尽力保存在 Thread 的 `history/` 目录，不受临时文件清理影响；存储或读取工具不可用不会阻止重建。用量和请求限制仍然生效。重建后再次溢出会结束 Run。

重建警告在成功后仍然可见。每次重建都要立即调查，先检查压缩配置和工具输出未受限的问题。恢复顺序和限制见[上下文恢复](../a13n-harness/context.md#context-recovery)。

### 文件记忆

记忆默认开启。在选中 YAML 旁，`memory/global/` 保存跨 Project 事实，`memory/projects/<project-id>/` 保存 Project 事实。无 Project 对话只使用全局记忆。`MEMORY.md` 保持短索引，详细主题分文件保存。记忆是历史上下文，与对话历史和 Full Control 权限分开。

在 **Settings → General → Memory** 选择整理 Model 和指令，或合并：

```yaml title="a13n-harness-ui.yaml — memory organization"
memory:
  enabled: true
  auto_organize:
    enabled: true
    model: model-primary
    instructions: Keep decisions concise and preserve useful source references.
```

使用已有 Model ID。省略/null `model` 跟随 `defaults.agent`；必须有已配置 Model。整理器获得范围内记忆工具，不获得 Agent 的其他工具或指令。它保留不确定性和用户更正，并报告修改。

WebUI 输入可触发已变化记忆的整理。每个范围有只读 Memory Thread；整理不读取旧对话或其他 Project。General 设置显示结果和用量，Model 调用可能产生费用。

| 状态                     | 下次尝试                             |
| ------------------------ | ------------------------------------ |
| 空、未变化、忙碌或冷却中 | 不请求 Model                         |
| 成功                     | 一小时后，由新输入触发               |
| 失败、取消或中断         | 十五分钟后，由新输入触发             |
| 正在尝试                 | 最多十二次请求、五分钟；新输入不中断 |

关闭 `auto_organize.enabled` 保留记忆但停止自动请求。关闭 `memory.enabled` 停止为后续 Run 附加记忆。任一开关关闭或服务器停止都会取消维护，保留部分修改。文件保留；活动前台 Run 保留原设置。

将 `memory/` 与应用数据分开备份。`.a13n-memory/` 保存锁和整理器状态。可选 Git 提供有界 diff 提示，不保存版本历史，也不修改你的仓库。

### 媒体理解

`media_understanding.image`、`.video` 和 `.audio` 选择已保存 Model ID，供活动 Model 无法原生读取该媒体时的文件 `view` 使用。每项默认 `null`，保留对应 Harness 环境变量回退。在 **Settings → Models** 或 `/model defaults` 配置；见[选择规则和输入要求](models-and-authentication.md#media-understanding-defaults)。

### 进程设置

修改以下字段后需重启：

| `process` 下的字段      | 默认值      | 用途                                                          |
| ----------------------- | ----------- | ------------------------------------------------------------- |
| `pricing_auto_update`   | `true`      | 刷新后续费用估算使用的价格                                    |
| `terminal_update_check` | `true`      | 显示启动包更新提示；不会自动安装                              |
| `log_level`             | `INFO`      | `CRITICAL`、`ERROR`、`WARNING`、`INFO`、`DEBUG`；规范化为大写 |
| `log_format`            | `pretty`    | 非交互日志使用 `pretty` 或 `json`；交互诊断写文件             |
| `max_object_bytes`      | `268435456` | 未压缩存储对象上限；1 KiB–1 GiB                               |

默认每对象 256 MiB，包括检查点，不是 Thread 配额或上下文预算。检查点超限时提高上限，重启后继续。更大限制增加峰值内存。保存失败保留上一检查点，不截断历史；降低限制可能使较大的已存对象无法读取。

官方 Model 属性和 Harness 价格补充另有从 GitHub `main` 获取的后台刷新。启动前设置 `A13N_OFFICIAL_MODELS_AUTO_UPDATE=0` 可关闭。下载失败保留有效/包内数据，并退避加抖动重试。刷新不改写 Model 或活动 Run；PDF 输入仍需手动启用。离线使用需同时关闭此刷新和 `pricing_auto_update`。`--no-update-check` 关闭单次调用的包检查。见[日志与更新](automation-and-troubleshooting.md#logs-updates-and-exit)。

### 本地 envd 运行时

在 **Settings → Environments → Local runtime** 配置，或合并：

```yaml title="a13n-harness-ui.yaml — local runtime"
envd:
  executable: null
  computer_use: false
  monitoring_interval_seconds: 60
```

重启后生效。保存不会下载、启动或请求系统权限。`executable: null` 选择已安装客户端要求的托管版本。自定义/源码构建需使用名为 `a13n-envd` 的绝对路径（Windows 为 `a13n-envd.exe`）；`~` 在 Host 展开。自定义二进制不会下载。省略 `envd` 保留嵌入应用的可执行文件设置，桌面访问保持关闭。

`computer_use` 允许桌面能力运行时；Agent 还需选择 `environment-desktop`。`monitoring_interval_seconds` 为 App 采集及内置本地 Sandbox/Envd/Desktop 采样设置 10–60 秒间隔，默认 60。外部 daemon 和自定义启动配置保留自身间隔。浏览器每分钟刷新；历史保留一小时。见[运行时管理](environments-and-projects.md#manage-local-envd)。

### Host 浏览器

`browser.mode` 默认 `auto`：WebUI 启动时准备共享浏览器，并为符合条件的 Full Control Run 自动提供工具。Agent 实际浏览或你选择 **Open browser** 时才创建对话页面。要关闭此功能，将模式设为 `disabled`。修改模式后保存并重启 Harness UI。

保留 `browser.executable` 和 `browser.vnc_executable` 的默认值 `null`，启动时便会自动选择已安装的兼容程序。自动选择无须保存配置或重启。Harness UI 负责探测程序；缺少的 Host 组件需要你自行安装。

如需指定程序，在 **Settings → General → Host browser** 中选择，或将下面的示例合并到根 YAML。将绝对路径替换为实际安装路径。`vnc_executable` 仅适用于 Linux：

```yaml title="a13n-harness-ui.yaml — Host browser"
browser:
  executable: /usr/bin/chromium
  vnc_executable: /usr/bin/Xtigervnc
```

修改覆盖项后保存并重启 Harness UI。选择 **Automatic** 可清除覆盖。显式路径无效时会阻止启动，不会回退到其他程序。

浏览器自动化需要 Linux 或 macOS WebUI Host、电脑共享，以及 Auto 模式下的 Full Control Run。macOS 自动化需安装 Chromium。可选的只读预览需要 macOS 14 或更高版本，并给运行 Harness UI 的应用授予屏幕录制权限。预览只显示当前对话拥有的浏览器窗口。**Open Mac Screen Recording settings** 打开 Host 设置，由你在那里授权。缺少预览权限时仍可自动化。macOS 不使用 VNC 密码，也不提供远程输入。

所有 Project 共用一个浏览器实例/Profile，位于 `<data_root>/browsers/profile`，无单独 Profile 路径设置。按[首次浏览器设置](webui.md#first-time-setup)完成 Host 准备和首次导航。保持 Chromium 沙箱开启；明确需要关闭它的部署可使用[显式沙箱选项](command-reference.md#webui)。

### WebUI 允许的 origin

`webui.allowed_origins: []` 不在监听器绑定地址和回环规则之外添加公共地址。要允许反向代理地址，添加其确切 HTTP(S) origin 并重启：

```yaml title="a13n-harness-ui.yaml — public request address"
webui:
  allowed_origins:
    - https://agents.example.com
```

非默认端口有区别。默认端口和尾部 `/` 会规范化。凭据、路径、查询、片段和部分通配符无效。字面值 `"*"` 允许任意请求地址，移除 Host/DNS 重绑定保护；优先使用确切 origin。

这控制请求地址准入，不是 CORS。认证和同源检查保留；列出的 origin 不能相互调用 API。配置重载不改变活动监听器。见[反向代理](webui.md#reverse-proxies-and-public-addresses)。

### WebUI Sidekick

**Settings → General → Sidekick** 控制独立工作的默认选择。省略设置或 `sidekick: {}` 开启；`sidekick: null` 关闭。为新 Sidekick Thread 选择默认 Model：

```yaml title="a13n-harness-ui.yaml — Sidekick defaults"
webui:
  sidekick:
    agent: null
    model: model-worker
```

使用已保存 ID。null/省略 `agent` 继承调用方；null/省略 `model` 跟随该 Agent 的 Model。配置的 Model 成为新 Thread 的持久默认值，供后续输入使用。显式 `create_thread(model_id=...)` 或 Run 选择器只覆盖对应 Run。

保存后影响新的 WebUI 根 Run。保存不启动工作、不改变已有 Thread，也不改变 `defaults.agent`、TUI Run 或委派子级。设置向导保留关闭/自定义选择。关闭移除 Sidekick 指令；发现工具仍按角色范围独立可用。见[协作与交付](webui.md#agent-collaboration-and-sidekick)。

### MCP 生命周期与协议

根 `mcp.host_owned_servers` 默认 `[]`：选中的服务器客户端可在 CLI/WebUI 中跨逻辑 Run 存续，但该列表不选择 Agent 工具。`mcp.protocol_overrides` 默认 `{}`，将已有 ID 映射到 `auto`、`legacy` 或 `2026-07-28`；省略的 ID 使用 SDK 协商。后续 Run 捕获修改。实时客户端和人工答案是运行时状态。见 [MCP 连接生命周期](mcp.md#connection-lifetime-and-protocol)和[人工输入](mcp.md#human-input-from-mcp-servers)。

### WebUI MCP Apps

通过 `webui.mcp_apps.enabled: true` 及 `webui.mcp_apps.servers` 中的服务器 ID 开启交互结果。WebUI 将这些服务器与通用 MCP 选择一同加入根和子级 Agent，去重而不改写 Agent 文件或 Thread 选择。CLI 只支持通用 MCP。开启 Apps 或改变独立 origin 监听器时需重启。完整本地/代理设置和交互权限见 [MCP Apps 指南](mcp-apps.md)。

## 数据根与环境变量

数据根保存对话、TUI Model 偏好、捕获、日志、API 密钥和 Content Plugin。选择顺序：

1. `--data-root PATH`。
2. `A13N_HARNESS_UI_DATA_ROOT`。
3. `<configuration-directory>/data`。

改变位置会打开独立状态，不迁移对话。相对启动路径按启动目录解析。Project 根目录展开 `~` 后必须为绝对路径；保存的缺失根目录必须先变为可用，Run 才能使用。

| 变量或引用                         | 用途                                    |
| ---------------------------------- | --------------------------------------- |
| `authentication.env`               | 从 Harness UI 进程环境读取 Model 密钥   |
| MCP `environment` / `headers` 引用 | 解析服务器环境或请求值                  |
| `CODEX_HOME`                       | Codex 一次性迁移来源；默认 `~/.codex`   |
| `COPILOT_HOME`                     | Copilot CLI 账户复用；默认 `~/.copilot` |
| `GROK_AUTH_PATH`、`GROK_HOME`      | Grok 一次性迁移来源                     |
| `GROK_AUTH`                        | 不导入内联 Grok 凭据；请原生登录        |
| `COLORFGBG`                        | 自动主题选择的被动终端元数据            |

没有通用 `A13N_HARNESS_UI_*` 覆盖机制。`storage`、`envd_runtime` 和关闭超时属于嵌入/运行时设置，不是根 YAML。监听器/认证选项使用 [CLI 参数](webui.md)。

## 出站 HTTP 代理

在启动 Harness UI 的环境中设置代理变量，再重启。Bash 示例：

```bash
export http_proxy=http://127.0.0.1:8888
export https_proxy=http://127.0.0.1:8888
export no_proxy=localhost,127.0.0.1,::1
```

支持大写形式和 `ALL_PROXY`；选择/绕过遵循 `httpx2`。Host Web 请求、远程 HTTPS MCP、更新和 [Model HTTP 客户端](../a13n-harness/models.md#outbound-http-proxies)遵循这些变量。容器需要容器内可达的代理地址。代理失败不会直连重试。

代理负责目标 DNS/网络限制。Host Web 检查 URL、重定向、期限和响应大小，不预解析或固定目标 IP，直连和 `NO_PROXY` 也如此。TLS 验证默认开启；仅在明确接受未验证证书时设置 `A13N_OUTBOUND_TLS_VERIFY=false`。见 [TLS 范围和风险](../a13n-harness/models.md#outbound-tls-verification)。

明文回环 MCP 和明文本地/provider 私有 Envd 附加保持直连。HTTPS Envd 附加遵循代理。第三方 SDK 传输保留自身行为；daemon 配对/反向 WebSocket 与 Python 附加客户端分开。

## Run 配置

使用根 `run_configuration` 限制根 Run 及子级的目标地址。包含所有需要的 Model、Web 和 MCP 主机：

```yaml title="a13n-harness-ui.yaml — Run host rules"
run_configuration:
  allowed_hosts:
    - api.example.com
    - 'regex:(?:[a-z0-9-]+\.)*docs\.example\.com'
  extensions:
    example.reader: {images: true}
```

省略/null `allowed_hosts` 允许全部目标；`[]` 拒绝全部。规则精确匹配规范化主机名/IP，或用 `regex:` 做完整主机名 Python 匹配。该模式允许 `docs.example.com` 及其子域，不允许 `docs.example.com.evil.test`。YAML 单引号保留反斜杠。空/无效表达式验证失败；URL、路径、端口、glob 和 CIDR 不是主机模式。

API 密钥 Model 客户端和 Host Web/MCP 检查声明目标及重定向跳转，不解析 DNS 或固定 IP。限制性规则拒绝不可检查的订阅传输。捕获的策略在根和子级 Run 内保持固定。带命名空间的 `extensions` 需要主动接入的消费者，不是 Capability 参数。Shell/插件网络需要 Environment 或部署隔离。见[主机规则](../a13n-harness/context.md#host-rules-and-regular-expressions)。
