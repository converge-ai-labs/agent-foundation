---
title: 使用 TUI
description: 在终端中发起任务、引导 Agent、添加附件，并继续已保存的工作。
---

## 日常交互

完成[设置](setup.md)后，在要处理的仓库目录中打开终端：

```bash
a13n-harness-ui
```

1. 输入 `/status`，检查 Agent、Model 和 `Workspace` 工作目录。
2. 输入具体任务，例如“说明这个仓库如何运行测试，不要修改文件”，然后按 Enter。
3. 阅读回复。需要查看工具参数、输出或 diff 时，按 Ctrl+O。
4. 在同一对话中继续追问；无关任务则用 `/new` 开始新对话。

对话是保存历史和设置的根 Thread，每次执行是一个 Run。输入框会提示 Enter 当前的作用：

| 当前状态         | Enter 的作用         |
| ---------------- | -------------------- |
| 空闲             | 将草稿作为新提示发送 |
| Agent 工作中     | 向当前 Run 补充引导  |
| 问题或审批已打开 | 确认当前交互         |

Ctrl+J 或 Alt+Enter 用于换行。粘贴多行文本后，文本会留在草稿中，直到你发送。

### 选择 Agent 或 Model

空闲时用 `/agent` 或 `/model` 打开选择器。选择后运行 `/status`，确认实际生效的设置。

| 要修改的内容   | 命令                                                      | 效果                                                   |
| -------------- | --------------------------------------------------------- | ------------------------------------------------------ |
| Agent          | `/agent <agent-id>`                                       | 为后续 Run 更换指令、工具、审查策略及 Agent 默认 Model |
| Model          | `/model <model-id>`                                       | 保留 Agent，为当前 Project 记住 Model 偏好             |
| 回到默认 Model | `/model default`                                          | 清除 Project 的 Model 偏好                             |
| 推理设置       | `/thinking`                                               | 查看或修改支持的推理设置                               |
| 临时推理模式   | `/pro on`、`/pro off`、`/pro reset`                       | 覆盖或恢复配置的模式                                   |
| 临时处理层级   | `/fast on`、`/fast off`、`/fast ultrafast`、`/fast reset` | 请求支持的层级，或恢复默认值                           |
| 执行权限       | `/environment`                                            | 查看可用 Environment 选项                              |

更换 Agent 或 Model 会保留历史，清除临时推理和服务层级覆盖。Model 偏好在 `/new`、更换 Agent 和重启 TUI 后保留。恢复 Thread 时，其已保存的默认 Model 优先于启动 Project 的偏好。其他已打开的 TUI 进程保留当前选择，直到重启；偏好中的 Model 被移除后，回退到 Agent 的 Model。显式 `--agent`、单次 `run` 和 API 调用跳过该 TUI 偏好。

要创建其他 Agent，可按[从文件创建 Agent](agents-and-subagents.md#create-an-agent-from-files)操作，或在 TUI 外运行 `a13n-harness-ui add agent`。

### 输入、显示与状态

**引导正在进行的工作。** 输入补充要求并按 Enter，或使用 `/steer <message>`。引导可包含附件。默认情况下，它会中断受支持的主模型生成；工具审查和执行则以协作方式交接。应用后的引导按编写顺序出现在对话记录中。如需仅在执行边界交付，可配置[排队引导](configuration.md#built-in-tools-and-subagents)。

**找回被拒的输入。** 准备中、取消中和已完成的操作不接受引导。被拒或未确认的输入留在草稿中；已经开始另一份草稿时，用 `/recover` 找回。重发前先检查显示的结果：Harness UI 不会自动把这份输入发送到新 Run。

**查看详情。** Ctrl+O 在简洁回复／工具摘要和保留参数、输出、完整 diff 之间切换，不会重新运行工具。`/status` 显示设置和待处理决策，`/help` 解释命令。输入 `$` 可查找 Skill，并在提示中插入 `$name`。

**使用命令。** Tab 补全命令和支持的参数。已知命令的参数无效时，显示错误并保留草稿。未匹配的斜杠文本会提示后作为普通输入原样发送。含空格的路径应加引号；Windows 反斜杠会保留。

### 审批与提问

决策打开后，先阅读请求，再选择动作：

1. 检查命令、目标、参数及已有的风险说明。用 `/review <request-id>` 查看保留详情。
2. 使用显示的控件选择 **Approve once**、**Deny** 或 **Deny with reason**。
3. 如果打开原因编辑器，输入原因并按 Enter。Ctrl+J/Alt+Enter 换行，Esc 返回。

审批不会预选答案。如果提供 **Approve with edited arguments**，必须提交完整 JSON 对象。绑定 shell 审批不能改参数：应拒绝并说明原因，要求新命令。参数被省略的请求不能批准。

需要外部工具结果的请求改为提供 **Provide result**。在 JSON 编辑器中填写真实结果；这个动作不会执行工具。无效 JSON 会留在编辑器中供修改。

每个显示的决策使用 `tools.interaction_timeout_seconds`，默认 120 秒。编辑不会重置计时。超时会返回拒绝或未收到回答的失败。从动作选择器取消会保留待处理请求；`/status` 或恢复对话可重新打开。使用这些卡片处理决策，而非 `/approve`、`/deny` 或 `/result` 命令。Shell 审查另有模型超时和[配置教程](configuration-recipes.md#configure-shell-review)。

#### 回答问题

问题卡片临时替换输入框，并保留你的普通草稿和附件。

1. 阅读问题和选项。上／下方向键或数字键只定位选项，不会提交。
2. 多选问题用 Space 切换选项，再按 Enter 确认。
3. 要自行填写答案，按 Tab 或 Ctrl+Space。Enter 提交答案，Ctrl+J/Alt+Enter 换行。
4. 回答剩余问题。看到成功的问题与回答回执后，再将答案视为已接受；本地收集完成还不够。

PageUp/PageDown 滚动卡片，不选择答案。点击只聚焦选项，滚轮只滚动。在答案编辑器中按 Esc 会返回选项并保留答案草稿；在选项中按 Esc 会取消收集，保留待处理请求。`/status` 可重新打开。

答案编辑器接受 `/help`、`/mode`、`/status`、`/quit`、`/cancel`、`/theme`、`/mouse` 和 `/review`。其他已知命令会被拒绝；未知斜杠文本仍作为字面答案。Ctrl+O 和 Ctrl+T 分别查看工具详情和历史，不会回答问题。关闭历史会回到卡片。

### 主题、滚动、粘贴与附件

要将文件与任务一起发送：

1. 输入 `/attach "path/to/notes.txt"` 或 `/attach "path/to/image.png"`。
2. 等待加载标记变成文件或图像标记。
3. 在标记旁添加要求，再按 Enter。也可发送仅含附件的消息。

剪贴板图像需要显式按 Ctrl+V、Alt+V，或使用 `/paste-image`。普通文本粘贴仍是文本。Linux 图像剪贴板需要显示会话，以及 Wayland 的 `wl-paste` 或 X11 的 `xclip`。通过 SSH 时，读取的是运行 TUI 的机器的剪贴板：请先上传文件，再附加其远程路径。粘贴本地路径不会上传文件。

`[image#1]`、`[file#2: notes.txt]` 等标记让附件保持编写顺序。Backspace/Delete 整体删除标记，Undo 恢复其内容。手动输入可见标记或从输入历史找回该文本，都不会附加文件。重试前先删除失败标记。所选 Model 必须支持输入模态；失败不会静默切换 Model 或丢弃图像。

| 附件限制 | 数值                     |
| -------- | ------------------------ |
| 数量     | 每份草稿 8 个            |
| 文件大小 | 单个 10 MiB，合计 20 MiB |
| 图像格式 | PNG、JPEG、WebP、GIF     |
| 图像尺寸 | 最多 3200 万像素         |

用以下控件调整阅读与复制方式：

| 任务                    | 控件                                         |
| ----------------------- | -------------------------------------------- |
| 修改本次 TUI 的颜色     | `/theme auto`、`/theme dark`、`/theme light` |
| 设置持久主题            | 根配置的 `display.theme`                     |
| 滚动输出                | PageUp / PageDown                            |
| 恢复跟随实时输出        | Ctrl+End，或返回底部                         |
| 让 TUI 面板处理鼠标滚轮 | `/mouse on`                                  |
| 使用终端原生选择与复制  | `/mouse off`                                 |
| 切换鼠标模式            | Esc；先关闭已打开的交互／补全                |

Auto 主题保留终端前景色、背景色和 ANSI 调色板。两种鼠标模式都支持键盘滚动。旧输出离开实时显示后，可用 Ctrl+T 查看。

### 长粘贴文本与 Thread 工作文件

超过 1000 字符的粘贴文本会折叠为紧凑标记。按 Alt+E，或将光标移入标记，即可编辑文本。标记后 Backspace 或标记前 Delete 删除整块。Enter 向 App 提交完整原文。

独立的[长文本输入策略](configuration.md#long-text-inputs)可将超过 8000 个 Unicode 字符的文本块作为保留文本文件引用交给 Model。折叠改变编辑器显示，文件转换改变模型接收方式。

Agent 通过 `thread-files` 挂载处理与本对话相关的文件：

| 位置                | 用途与生命周期                                    |
| ------------------- | ------------------------------------------------- |
| `attachments/`      | 已提交文件，保留供后续 Run 和恢复使用             |
| `tmp/`              | 可丢弃的脚本、下载和中间输出；跨 Run 和重启复用   |
| `tmp/tool-results/` | 过大工具输出；仅属于当前 Run，可在 Run 结束时清理 |

临时文件在不活动三天后可被清理，检查在启动时和每小时进行。App 会保护它使用过的 Thread 目录，直到退出。清理保留附件和 Project 文件。重要结果应先让 Agent 复制到 Project 或你选择的其他位置，再依赖它们。

在 Sandbox 中，Project 内的 shell 不能自动访问同级 Thread 挂载。处理附件必须选择该挂载内的工作目录。自定义 provider 可能只通过文件工具暴露它。

### 查找已保存对话

1. 在准备工作的目录中启动 TUI。
2. 输入 `/resume`，按名称、ID 或已保存输入／回复摘录搜索。
3. 选中一行查看预览。Ctrl+T 可查看历史而不切换对话。
4. 按 Enter 恢复，再用 `/status` 检查工作目录和选择。

**恢复使用启动目录的 Project 和根目录**，同时保留已保存历史、Agent 和执行权限。要保留原 Project 和目录，应从该 Project 的第一个根目录启动。活动对话不能在另一 TUI 中恢复。

| 浏览器操作                     | 按键              |
| ------------------------------ | ----------------- |
| 选择对话                       | 上 / 下方向键     |
| 加载另一页结果                 | PageUp / PageDown |
| 纳入其他目录和无 Project 对话  | Ctrl+A            |
| 重命名；空名称恢复首次输入标签 | F2，然后 Enter    |
| 刷新结果                       | F5                |
| 返回而不切换                   | Escape            |

列表按已保存活动排序。预览来自已保存摘录，未完成回复标为进度。搜索、重命名和历史查看不发出模型请求。没有摘录的旧对话仍可按名称和 ID 搜索。取消恢复或恢复失败会保留草稿和附件。

已知 ID 时，可在 TUI 使用 `/resume <thread-id>`，或在 shell 运行 `a13n-harness-ui --resume <thread-id>`。

### 查看保留历史

按 Ctrl+T 或输入 `/history`。Up/PageUp 查看旧页，End 跳到最新内容。Ctrl+T、q 或 Escape 关闭历史并返回草稿。

已保存的显示历史与模型上下文分开。摘要、压缩和冷启动压缩可以缩短上下文，但不会删除保留的显示内容。已保存检查点只覆盖其保存位置，更新的实时输出仍可能尚未保存。

### 任务、用量与终端反馈

根据要回答的问题选择查看命令。Agent 忙碌时也能使用：

| 问题                           | 命令                                                         |
| ------------------------------ | ------------------------------------------------------------ |
| 当前设置和待处理决策是什么？   | `/status`                                                    |
| 子 Agent 返回了什么？          | `/subagents`、`/subagents next`、`/subagents <execution-id>` |
| 观测到了哪些后台进程？         | `/ps`                                                        |
| 本 Thread 记录了多少用量？     | `/usage` 或 `/usage all`                                     |
| 某一范围的用量是多少？         | `/usage root`、`/usage subagents`                            |
| 精确费用和记录覆盖情况是什么？ | `/usage details`                                             |

进程观测有数量上限，且仅属于本对话，不是整台 Host 的进程清单。不可用不表示已退出。用量合计包含已观测的根级、子级和辅助模型请求；`ctx` 表示最近根请求的上下文占用。`--` 表示不可用，`+` 表示数据不完整或只知道部分费用。记录用量是估计，不是订阅账单。

使用 Codex Model 时，空闲状态的 `/usage subscription` 显示可用订阅窗口和重置信息。`/usage reset` 可查看并显式确认符合条件的兑换。超时或取消后结果未知时，保留当前 TUI，并重试同一兑换 ID。使用 ChatGPT Model 时，Harness UI 会引导你在 ChatGPT 管理用量，不提供数字订阅额度。

**Tool executions** 集中显示保留工具的检查和结果。工具在空闲时完成，可触发 Agent 后续执行，不会发送或清空你的草稿。Stop 取消该后续执行；`/executions cancel <execution-id>` 针对保留工具本身。请求取消不等于工作已经停止。

工作期间 Ctrl+C 请求取消并等待清理。空闲时第一次 Ctrl+C 清空草稿，两秒内第二次按下退出；编辑会解除这个退出手势。草稿为空时 Ctrl+D，或 `/quit`、`/exit` 也可退出。终端响铃提示完成或出现新决策。

故障产生私有诊断报告时，分享前先检查异常消息是否包含敏感数据。报告不会自动上传。按打印的恢复提示回到已保存状态；该提示无法恢复未发送草稿和未保存工作。

### 任务与笔记

默认包含任务和笔记工具。F2 展开或折叠任务面板，最多显示五条已保存任务，活动工作优先。`/notes` 打开已保存笔记值，Ctrl+O 展开笔记更新。笔记面板最多显示 256 条或 256 KiB，并标明省略。

要为某个 Agent 关闭笔记，在其 `working_state` Capability 条目中设置 `configuration.notes_enabled: false`。已保存任务和笔记的变化会在操作或恢复后显示。

### 围绕 Goal 持续工作

需要同一 Agent 按明确完成条件检查工作时，可以使用 Goal：

```text
/goal Implement the requested change, run its tests, and report any remaining blockers.
```

首次响应是第 `0` 次检查；默认预算允许再检查 10 次。Goal 状态条显示进度和结果。Ctrl+C 和 `/cancel` 仍可停止执行，普通工具权限仍然适用。

Agent 以单独一行的 `[GOAL_COMPLETE]` 表示完成。摘要或压缩后必须重新审查原目标。**Verified 是 Agent 自己的完成声明，不是独立审查。** 预算耗尽、取消、错误和未验证停止保持为不同结果。

如果剩余工作受外部事件、不可用的访问权限或用户决定阻塞，Agent 可以说明原因，并以单独一行的 `[GOAL_BLOCKED]` 结束。**Blocked** 会结束 Goal 模式，不声明成功，也不再自动检查。浏览器交还通知可以让对话以普通模式继续，但不会恢复已结束的 Goal 或其预算。

根级 `max_goal_iterations` 可改变新 Goal 的预算；零或负数关闭后续检查。挂起的 Goal 保留捕获预算。仅恢复对话不会重启未完成工作。发送普通输入进入普通行为，或用 `/goal` 开始新目标和预算。WebUI 通过 [Goal 控件](webui.md)提供同一策略。

### 本地宿主机命令

在 POSIX 宿主机空闲时，可输入以下命令：

```text
!git status
```

命令使用 Host 进程环境，在 TUI 工作目录运行，**位于 Agent 所选 Environment 和 Sandbox 之外**。命令和输出不进入模型上下文。Windows 不支持这个 TUI 快捷方式；普通 Windows CLI 和 Agent 工具仍然可用。

命令非交互式，stdin 关闭，期限为 120 秒，合并输出显示上限为 256 KiB。Ctrl+C 或 `/cancel` 终止所属进程组并等待清理。工作忙碌或交互打开时，命令会被拒绝并保留草稿。

### 工具开关

要修改内置交互工具，将以下片段合并到[选中的根 YAML](configuration.md#locate-and-validate-your-files)：

```yaml
tools:
  enable_ask_user_question: true
  interaction_timeout_seconds: 120
  enable_codeact: true
```

两个工具默认开启。保存文件，在 TUI 外运行 `a13n-harness-ui config validate`，再用后续 Run 使用新的选择。将任一 enable 标志设为 `false` 可移除对应工具。交互超时必须为正有限数；它控制活动 TUI 的等待，不是持久 HTTP 决策到期时间。

CodeAct 提供受限 Python `run_code`/`run_program`，以及 `store`/`load`/`forget` 保存值。它对 Host 的操作使用符合条件的工具及其普通权限。高级运行器设置属于 Agent 的 `codeact` Capability；根开关和可见性过滤优先。
