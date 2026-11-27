---
title: 自动化脚本与故障排查
description: 在 CI 或 Shell 脚本中无头执行单次智能体任务、查阅诊断日志、升级安装包并恢复中断会话。
---

在脚本或 CI 任务中使用 `a13n-harness-ui run` 执行无头单次运行；遇到异常中断时，请先检查操作状态与已保存检查点，再决定是否重试。

## 自动化执行与常见故障诊断

完成[初始化设置](setup.md)后，在目标项目目录下执行非交互式命令（全局参数置于子命令之前）：

```console
# 直接打印最终文本回答
a13n-harness-ui run "Review the current diff"

# 输出结构化 JSON 操作回执（退出码 0 表示 completed，非 0 表示失败或挂起）
a13n-harness-ui run "Review the current diff" --format json

# 续接已保存会话或显式指定沙箱模式
a13n-harness-ui run "Summarize the next step" --resume thread-id --format json
a13n-harness-ui --environment-mode sandbox run "Inspect the repository"
```

单次运行始终采用无人值守模式。需要外部输入或固定要求审批的工具不会出现在工具列表中。Shell 审查和其他依赖调用参数的检查照常执行；需要审批的调用会失败，不会挂起运行。`run --resume` 保留会话已保存的项目和根目录。若已有待处理决定导致启动失败，请在交互式终端执行 `a13n-harness-ui --resume thread-id`，先处理该决定。

| 常见现象                                | 处理方式                                                                                |
| --------------------------------------- | --------------------------------------------------------------------------------------- |
| 修改配置文件后未生效                    | 运行 `a13n-harness-ui config validate` 与 `config show`（无效修改会保留上一版生效配置） |
| 智能体无法启动                          | 检查模型 ID、能力键名、名册 ID，以及自定义 Markdown 子智能体是否与内置角色重名          |
| 沙箱就绪检查失败                        | 运行 `a13n-harness-ui doctor` 并按提示补齐前置依赖                                      |
| 提交被拒绝或误关草稿                    | 使用 `/recover` 恢复草稿缓冲区，修正报错原因后重新提交                                  |
| 显示 `[System] Retrying model request…` | 瞬时网络波动自动重试（单轮最多 5 次，不会重复执行已完成的工具调用）                     |
| 源码检出切换分支后导入报错              | 运行 `make sync`（或 `make a13n-harness-ui`）同步锁定的依赖版本                         |

如需从编辑器或其他程序驱动会话，请使用 [ACP](agent-client-protocol.md)。

OTLP 链路追踪配置见[可观测性](observation.md)，WebUI 重启恢复见 [WebUI](webui.md)，完整命令行语法见[命令参考](command-reference.md)。

## 日志、版本升级与退出恢复

### 诊断报告与检查点恢复

1. **日志与崩溃报告：** 未预期异常会在系统临时目录生成 `a13n-harness-ui-error-*.json`（分享前请先审阅脱敏，系统不会自动上传任何数据）。交互运行日志位于 `<data-root>/logs/terminal.log`（单文件 5 MiB 轮转，保留 3 份备份，按 Run ID 关联）。
2. **退出或异常后恢复：** 执行终端退出时打印的 `a13n-harness-ui --resume <thread-id>` 命令，先查看 `/status` 与 `/history`。已记录的工具结果不会重放；未收到响应的可重试调用遵循 [`tool_recovery="declared"`](../a13n-harness/state-and-resume.md#resume-unanswered-tool-calls) 在全新权限检查下恢复。

### 升级 Harness UI 与 Envd 运行时

关闭所有正在运行的 TUI 与 WebUI 会话后，升级 `uv tool` 安装版本：

```console
a13n-harness-ui update
```

在 WebUI 的**设置 → 诊断 → 关于**中，可以查看已安装版本、可用更新和适用于当前版本的安全公告。终端通过系统消息提示可用更新。请使用初次安装时的包管理器升级 Harness UI。

如需跳过本次检查，在 `webui` 子命令之前传入 `--no-update-check`，或启动终端时直接传入该参数。要同时关闭 WebUI 和终端的检查，请在 `a13n-harness-ui.yaml` 中设置：

```yaml title="a13n-harness-ui.yaml"
process:
  update_check: false
```

如果只想关闭终端提示，请设置 `process.terminal_update_check: false`。

检查或获取与当前客户端匹配的沙箱 `a13n-envd` 二进制文件（或升级独立部署的可执行文件）前，请先停止共用同一数据目录的实例：

```console
a13n-harness-ui envd update --check
a13n-harness-ui envd update
a13n-harness-ui envd update --executable /absolute/path/to/a13n-envd
```

在 macOS 上替换二进制后，如遇录屏或辅助功能权限失效提示，请按[升级后恢复 macOS 权限](../a13n-envd/computer-use.md#restore-macos-permissions-after-an-upgrade)重新授权。
