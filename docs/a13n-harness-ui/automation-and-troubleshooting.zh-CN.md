---
title: 自动化与故障排查
description: 用脚本运行 Harness UI、跟踪执行，并排查连接、WebUI 和 Environment 问题。
---

## 自动化与诊断

```console
a13n-harness-ui run "Review the current diff"
a13n-harness-ui run "Summarize the next step" --resume thread-id --format json
a13n-harness-ui --environment-mode sandbox run "Inspect the repository"
a13n-harness-ui plugin list
a13n-harness-ui import subagents --product codex --scope project --project-root .
a13n-harness-ui --help
a13n-harness-ui login --help
```

单次运行模式输出最终文本或结构化操作对象后退出。它与交互模式使用相同的 Project 选择（来自当前目录）、Model 解析、续接和权限规则。它不使用 `/model` 记住的 Model。失败或挂起的操作以非零状态退出，不会打开交互式审批提示。要回答待处理决策，请以交互方式恢复。

帮助和版本命令不会启动 App。启动会在 TUI 打开前初始化本地配置和存储；Model、Environment 和 MCP 连接按需准备。启动耗时记录在 `<data-root>/logs/terminal.log`。

如果启动或 `--resume` 失败，阅读显示的错误链和诊断报告路径。分享前检查私有报告中是否有敏感内容；系统不会自动上传。

## 执行跟踪

自动 OTLP 导出、Langfuse 和 Logfire 配置、嵌入现有 provider，以及 `make cli` 使用的独立 `dev/harness-ui/.env`，见[跟踪 Harness UI](observation.md)。追踪用于补充诊断和已保存的对话状态，不能替代它们。

## 长时间运行的工作

根 Agent 和 subagent 没有固定的 Harness 请求次数上限。你可以取消执行，提供方或子级限制仍然适用。长 Run 可能使用更多 token；进程崩溃不会自动恢复正在运行的工作。

## 模型连接中断

符合条件的暂时性模型中断会从可用历史重试，连续尝试最多五次，包含首次请求。主模型成功响应会重置预算；永久错误会停止执行。同一个 Run 继续执行时，TUI 显示 `[System] Retrying model request…`。重试**不会** 重放已完成的工具调用，也不能恢复崩溃进程。手动重做有副作用的操作前，先检查其是否已完成。

## WebUI

协作、监听/身份验证选项、API 密钥保留、容器交付和前台生命周期见 [WebUI](webui.md)。

## 源码检出依赖

切换分支后，运行 `make sync`（或通过 `make a13n-harness-ui` 启动）安装该分支锁定的依赖。其他分支的虚拟环境可能包含不兼容依赖，导致导入失败。应使用已提交的锁文件，不要修补私有依赖导入或独立升级个别库。安装包用户应通过管理其安装的包管理器升级 `a13n-harness-ui`。

## 日志、更新与退出

交互诊断记录在 `<data-root>/logs/terminal.log`（5 MiB，三个轮转备份），不会进入对话或普通屏幕回滚区。被跳过的 Content Plugin 对每个未变化的路径和原因只显示一次可操作提示；详细内容见日志。Harness UI 不会删除旧版 Content Plugin 布局中的文件。

模型执行失败和意外错误还会在系统临时目录（常见 Linux 安装为 `/tmp`）生成私有的 `a13n-harness-ui-error-*.json` 报告。故障提示链接到报告路径和 GitHub Issue 表单。报告包含异常链、栈位置、组件版本和 Thread/Run ID，不含栈帧局部变量、源码行或对话记录。异常消息仍可能包含敏感信息：附加报告前先检查，并提供复现步骤。不会自动上传任何内容。需要更多上下文时，在 `terminal.log` 中按同一 Run ID 关联查找；生成报告不能代替保存对话状态。

如果 asyncio 报告 `Task was destroyed but it is pending!` 且没有伴随异常，TUI 会显示警告并保持打开，保留草稿。报告包含 asyncio 任务身份、协程位置、挂起栈位置，以及可获取的创建栈位置。TUI 保持打开不能恢复丢失的 asyncio 任务，也不能确认活动工作已成功。检查 `/status`，停止推进时用 `/cancel`，分享前检查报告。不会自动重试。其他未处理的 TUI 事件循环故障仍会退出并提供恢复说明。

启动顺序为**更新确认 → 必要时设置 → 对话**。更新提示、设置和对话会在同一 TUI 中替换视图，不会退出后重新打开，也不会在终端追加提示。已安装的发行版查询公共 PyPI 元数据，超时为三秒，缓存一天；离线失败时会静默继续启动。普通启动和 `a13n-harness-ui setup` 均遵循此顺序。

默认开启更新检测。要在 `a13n-harness-ui.yaml` 中关闭：

```yaml title="a13n-harness-ui.yaml"
process:
  terminal_update_check: false
```

使用 `a13n-harness-ui --no-update-check` 可在本次启动中跳过检测。仓库开发的 `make a13n-harness-ui` 始终关闭检测。开发版本、帮助/版本命令和非交互命令也会跳过检测。

**启动不会未经确认安装更新。** 对于已识别的 uv tool 安装，提示会显示命令和工具目录，并提供 **Update now** 和默认选项 **Not now** 。选择 Not now、Escape 或 Ctrl+C 都会继续启动。如果新版本仍可用，下次启用检测的启动会再次询问。选择 Update now 会先关闭 App 和 TUI，再运行安装器，随后提示重启；安装失败会直接报告，不会重试或继续设置。其他安装方式会提供手动说明，不会猜测更新命令。要立即更新而不等待下次启动检查：

```console
a13n-harness-ui update
```

该命令本身就是安装请求，因此不会再次确认，也不会打开 TUI 或设置。它针对当前运行安装的工具目录执行 `uv tool upgrade a13n-harness-ui`，跳过启动元数据缓存。关闭启动检测不会关闭此命令。PATH 中必须有 uv；不支持的安装方式会失败，并提示使用原来的包管理器。包解析和网络错误由 uv 处理。失败时返回安装器状态，不会重试；中断更新以状态 130 退出，并提示重试前先检查安装。成功后重启 Harness UI。你也可以直接运行 `uv tool upgrade a13n-harness-ui`。

清理后，你的终端会显示已保存对话（其根 Thread）的恢复命令。该命令保留显式配置和数据根选项，并说明应在哪个工作目录运行。失败和中断的操作会在释放前保存可用的有效 Harness 检查点，包括安全保留的部分文本。下一轮和 `--resume` 使用所选检查点。记录过的工具结果不会重放，先前的审批也不授权重放。未回答调用遵循 Harness 默认的 [`tool_recovery="declared"` 策略](../a13n-harness/state-and-resume.md#resume-unanswered-tool-calls)：当前声明为可重试的工具可能在新的权限和审批要求下再次执行。与检查点一起保存的已接受延迟结果，在原生历史纳入它们前仍可用；这不保证每条已接受输入都已保存。如果状态导出或存储失败，或进程在清理前被终止，恢复会使用之前的检查点。

较大的活动消息先用轻量纯文本预览，完成后重新排版为 Markdown。渲染行随滚动分页加载，不会因固定视口上限丢弃旧行。源码缓存仍有上限；明确的淘汰提示会提示你使用 `/history`。该命令只能恢复 App 保留并公开的内容，不能找回上游已省略的数据。

## 命令参考

[完整命令参考](command-reference.md)列出所有已注册 shell 子命令和选项、斜杠命令语法、别名以及忙碌状态下的可用性。全局选项放在子命令前；每层都可使用 `-h` 或 `--help`。

配置命令会打开本地应用，可能写入已接受配置的索引或初始化数据存储。启用价格更新时也可能启动更新器；`--no-update-check` 只关闭启动时的包更新检查。这些命令并非严格离线、无副作用的 YAML 解析器。验证不会发出模型请求，也不能证明提供方/MCP 凭据可用。

## 保留草稿的故障排查

| 现象                                | 下一步                                                                                        |
| ----------------------------------- | --------------------------------------------------------------------------------------------- |
| 文件修改似乎未生效                  | 运行 `config validate`，再运行 `config show`；新保存无效时，上一版已接受配置继续生效          |
| Agent 无法运行                      | 检查其 `model` 引用、选中的 Capability 键、角色清单 ID 和确切工具名                           |
| 本地 Markdown 子 Agent 拒绝 `model` | 删除此字段；需要独立设置时，创建 Agent 并使用 `- agent: agent-id`                             |
| 两个子 Agent 同名                   | 移除其中一个选择，或重命名自定义 Markdown 子 Agent；内置角色不会覆盖自定义角色                |
| Sandbox 就绪检查失败                | 运行 `a13n-harness-ui doctor`；修复前置条件或显式选择 Full Control。Harness UI 从不自动降级。 |
| 请求被拒绝                          | 使用恢复的草稿或 `/recover`；不要假定已发送                                                   |
| 单次运行模式中的操作挂起            | 以交互方式恢复并完成待处理决策                                                                |
| 进程崩溃                            | 检查私有诊断路径及敏感异常消息，然后使用打印的已保存状态恢复命令                              |

存在已保存对话，不代表可以恢复未发送输入和进行中的副作用。诊断报告不含局部变量或对话记录，也不会自动上传。分享前先检查。
