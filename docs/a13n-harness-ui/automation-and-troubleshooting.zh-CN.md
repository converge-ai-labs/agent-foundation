---
title: 自动化与故障排查
description: 通过脚本运行 Harness UI，检查失败原因，更新安装，并恢复已保存的对话。
---

用 `run` 执行一次脚本任务。失败后，先检查操作状态和已保存状态，再决定是否重做。

## 自动化与诊断

先完成[设置](setup.md)，并确认一次简短交互对话能正常完成。然后在目标 Project 对应的目录中运行命令。

1. 提交一次任务并打印最终回答：

   ```console
   a13n-harness-ui run "Review the current diff"
   ```

2. 在脚本中使用结构化操作对象：

   ```console
   a13n-harness-ui run "Review the current diff" --format json
   ```

   退出状态 `0` 表示操作完成。失败或挂起时返回非零状态。检查操作的状态和失败详情，不要将“有输出”等同于成功。

3. 要继续已有对话，将 `thread-id` 替换为已保存的根 Thread ID：

   ```console
   a13n-harness-ui run "Summarize the next step" --resume thread-id --format json
   ```

单次运行使用与交互模式相同的 Project、续接和权限规则，但跳过 `/model` 记住的 Model 偏好。它不会打开交互式审批提示。Run 因待处理决策而挂起时，以交互方式恢复并回答：

```console
a13n-harness-ui --resume thread-id
```

全局选项放在子命令前。例如，为脚本任务选择 Sandbox：

```console
a13n-harness-ui --environment-mode sandbox run "Inspect the repository"
```

用 `a13n-harness-ui --help` 和子命令的 `--help` 查询语法，不会启动 App。配置检查命令则会打开本地 App：它们可能初始化存储、写入已接受配置的索引，并在启用时启动价格更新器。`config validate` 检查配置，不验证 provider 或 MCP 连通性；`--no-update-check` 只关闭启动时的包更新检查。

## 保留草稿的故障排查

| 现象                                | 下一步                                                                                            |
| ----------------------------------- | ------------------------------------------------------------------------------------------------- |
| 配置修改似乎未生效                  | 运行 `config validate`，再运行 `config show`。修改无效时，上一版已接受配置继续生效。              |
| Agent 无法运行                      | 检查 Model 引用、Capability 键、角色清单 ID 和确切工具名。                                        |
| 本地 Markdown 子 Agent 拒绝 `model` | 删除该字段。需要独立设置时，创建 Agent 并通过 `- agent: agent-id` 引用。                          |
| 两个子 Agent 同名                   | 移除一个选择，或重命名自定义 Markdown 子 Agent。内置角色不会覆盖自定义角色。                      |
| Sandbox 就绪检查失败                | 运行 `a13n-harness-ui doctor`，修复报告的前置条件。选择 Full Control 是另一个需要显式作出的决定。 |
| 提交被拒绝                          | 使用恢复的草稿或 `/recover`，检查拒绝原因，准备好后再提交。                                       |
| 单次 Run 挂起                       | 以交互方式恢复该 Thread，完成待处理决策。                                                         |
| 回答已出现，但保存失败              | 阅读错误和已保存续接状态。提交另一个任务前，检查已完成的动作。                                    |
| 启动、恢复或进程失败                | 阅读诊断报告路径，再按下方已保存状态的恢复步骤处理。                                              |

较大的活动消息先显示纯文本预览，完成后重新排版为 Markdown。旧渲染行随滚动加载。如果有大小上限的源码缓存淘汰了内容，按提示使用 `/history`；它显示 App 保留且可以公开的内容。

## 模型连接中断

TUI 显示 `[System] Retrying model request…` 时，同一个 Run 正在从可用历史重试符合条件的暂时性模型中断。连续尝试最多五次，包含首次请求。主模型成功响应会重置该预算；永久错误会停止执行。

等待现有重试完成，或取消 Run。模型恢复不会重放已完成的工具调用。失败后要手动重做有副作用的动作，应先检查其实际结果。模型重试不会重启崩溃的进程。

## 长时间运行的工作

根 Agent 和 subagent 没有固定的 Harness 请求次数上限。提供方和子级限制仍适用，较长 Run 可能消耗更多 token。用 `/status` 检查进度，用 `/cancel` 停止活动工作。进程崩溃后，需从已保存续接状态启动新的调用；活动工作不会自动重启。

## 日志、更新与退出

### 查找诊断报告

1. 阅读显示的错误链和报告路径。模型执行失败和意外错误可能在系统临时目录生成 `a13n-harness-ui-error-*.json`。
2. 在 `<data-root>/logs/terminal.log` 查看启动耗时和交互诊断。日志达到 5 MiB 时轮转，保留三个备份。按 Run ID 关联条目。
3. 分享前检查报告。它包含异常链、栈位置、组件版本和 Thread/Run ID；异常消息可能含敏感数据。报告不含栈帧局部变量、源码行和对话记录。
4. 报告问题时，通过故障提示中的 Issue 链接附上已检查的报告和复现步骤。系统不会自动上传。

被跳过的 Content Plugin 对每个未变化的路径和原因只显示一次可操作提示，日志包含详情。旧版 Content Plugin 文件会留在磁盘上。

对于不伴随异常的 asyncio 警告 `Task was destroyed but it is pending!`，TUI 保留草稿并保持打开。报告包含有上限的任务身份，以及可获取的协程、挂起栈和创建栈位置。检查 `/status`；工作停止推进时用 `/cancel`。丢失的任务不会恢复或重试。其他未处理的 TUI 事件循环故障会退出并提供恢复说明。

### 更新 Harness UI

对于识别出的 uv-tool 安装：

1. 退出 Harness UI。

2. 运行：

   ```console
   a13n-harness-ui update
   ```

3. 命令成功后重启 Harness UI。

命令会针对当前运行安装的工具目录，直接执行 `uv tool upgrade a13n-harness-ui`。它不会再次确认、打开设置，也不使用启动元数据缓存。PATH 中必须有 uv。其他安装类型应使用原来的包管理器。也可以直接调用 `uv tool upgrade a13n-harness-ui`。

安装器失败时返回非零状态，不自动重试。更新中断时退出状态为 `130`；重试前先检查安装。更新 Python 包和准备原生 Envd runtime 是两个独立操作。

### 控制更新提示

启动时先按需完成设置，再打开对话。包更新检测在后台进行，**从不安装更新**。已安装的发行版查询公共 PyPI 元数据，超时为三秒，缓存一天；离线失败静默处理。发现较新稳定版后，会显示一条 System 提示，包含两个版本及相应更新命令，不打断输入，也不要求作出选择。

要关闭检测，将以下片段合并到[选中的根 YAML](configuration.md#locate-and-validate-your-files)：

```yaml title="a13n-harness-ui.yaml"
process:
  terminal_update_check: false
```

用 `a13n-harness-ui --no-update-check` 跳过一次检测。开发版本、帮助／版本命令、非交互命令和 `make a13n-harness-ui` 都跳过检测。仅执行设置时，不等待或显示更新提示；关闭终端会取消待完成的检测。下一次启用检测的对话启动仍可能再次显示缓存提示。这些控制不影响显式 `update` 命令。

### 准备或更新 Envd

对于托管 runtime，先停止使用同一数据根的其他 Harness UI 实例，然后运行：

```console
a13n-harness-ui envd update --check
a13n-harness-ui envd update
```

第一条命令报告所选 runtime 文件是否存在，不验证执行。第二条获取已安装客户端要求的版本。完成后重启 Harness UI。要选择更新的托管版本，需更新 Harness UI；用 `--force` 重装当前所选版本。

这些命令遵循全局 `--config` 和 `--data-root`，不会启动 App 或守护进程。支持时会使用现有原生更新器；首次安装或旧版缓存程序使用引导获取。原生失败会直接报告，不自动回退或重试。

对于独立安装，先停止其守护进程，再选择可执行文件：

```console
a13n-harness-ui envd update --executable /absolute/path/to/a13n-envd
```

配置中的 `envd.executable` 会自动被选中。此路径委托给 [Envd 原生更新器](../a13n-envd/installation.md#update-an-existing-installation)，默认使用其最新稳定版。添加 `--version X.Y.Z` 可指定与客户端匹配的版本或 RC。成功后重启受影响的应用或服务。远程 Device 不会通过 EIP 更新。

如果替换后 Envd 报告缺少 macOS 屏幕录制或辅助功能权限，请按错误指引[恢复权限](../a13n-envd/computer-use.md#restore-macos-permissions-after-an-upgrade)。

### 退出或失败后恢复

1. 使用终端清理后打印的恢复命令。它指定根 Thread，保留显式配置／数据根选项，并说明工作目录。
2. 继续前检查 `/status` 和 `/history`，确认结果不明的动作。
3. 提交下一条指令，或完成待处理决策。

失败或中断的操作会在释放前保存可用的有效 Harness 检查点，包括安全保留的部分文本。下一轮使用该已选定检查点。如果导出或存储失败，或进程在清理前被终止，恢复使用之前保存的检查点。

记录过的工具结果不会重放。未回答调用遵循 [`tool_recovery="declared"`](../a13n-harness/state-and-resume.md#resume-unanswered-tool-calls)：当前声明为可重试的工具，可能在新的权限和审批要求下再次执行。先前的审批不授权重放。与检查点一同保存的已接受延迟结果，在原生历史纳入它们前仍然可用。

已保存状态不能找回未发送的输入，也不能确定进行中副作用的结果。诊断报告与已保存状态相互独立。

## 源码检出依赖

切换分支后，运行 `make sync`，或通过 `make a13n-harness-ui` 启动，以安装该检出锁定的依赖。复用其他分支的环境可能导致导入失败。应使用已提交的锁文件，而不是修补私有依赖导入或独立升级库。安装包用户应通过原包管理器更新。

## 执行跟踪

按[跟踪 Harness UI](observation.md)配置 OTLP 导出、发送测试 Run，并检查模型／工具耗时和续接保存。仓库的 `make cli` 流程使用独立的私有 `dev/harness-ui/.env`。

## WebUI

监听器和身份验证设置、协作、API 密钥保留、容器和前台生命周期，见 [WebUI](webui.md)。

## 命令参考

[命令参考](command-reference.md)列出 shell 子命令、选项、斜杠命令语法、别名和忙碌状态下的可用性。每层命令都可使用 `-h` 或 `--help`。
