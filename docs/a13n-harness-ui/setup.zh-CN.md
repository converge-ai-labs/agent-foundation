---
title: 设置第一个 Agent
description: 连接 Model、创建 Agent，并选择执行权限。
---

设置向导会连接 Model、创建 Agent，并选择执行权限。未配置默认 Agent，或所选 Agent 没有 Model 时，设置向导会自动运行。要重新运行，先退出 TUI，然后执行：

```console
a13n-harness-ui setup
```

TUI 中没有 `/setup` 命令。显式运行设置会在保存后返回 shell；首次设置完成后会打开 TUI 输入框。

## 1. 连接 Model

选择 ChatGPT、Codex、Grok 或 GitHub Copilot 订阅，或 API 密钥连接。

| 连接方式            | 你需要提供的内容                | 凭据存储位置                                                 |
| ------------------- | ------------------------------- | ------------------------------------------------------------ |
| ChatGPT 订阅        | 登录并提供完整回调 URL          | Harness UI 数据根（`auth.json`）                             |
| Codex 订阅          | 可复用的登录状态                | 共享 Codex 账户存储                                          |
| Grok 订阅           | 可复用的登录状态                | 共享 Grok 账户存储                                           |
| GitHub Copilot 订阅 | 设备登录或已有 Copilot CLI 登录 | Harness UI 数据根（`oauth/copilot.json`）或 Copilot CLI 存储 |
| API 密钥            | 提供方/协议、模型 ID 和密钥引用 | 环境变量或 Harness UI 本地密钥存储                           |

复用已检测到的登录，使用向导提供的方法登录，或先配置连接，稍后运行 `a13n-harness-ui login <provider>` 登录。如果账户存储需要修复，按显示的提示操作。登录在 TUI 之外进行。

使用 API 密钥时，向导提供不回显的密钥输入框，也可填写 `env:OPENAI_API_KEY` 或 `key:key-primary`。不要将密钥粘贴到 TUI 输入框。新输入的密钥会立即保存到独立的本地密钥存储，不会写入 Model YAML。

登录方式、已存密钥和账户位置见[模型与身份验证](models-and-authentication.md#subscription-login-and-api-keys)。

## 2. 选择模型和设置

选择推荐模型，或输入区分大小写的 API 模型 ID。推荐来自已安装的目录；向 provider 确认可用性。复用 Model 时保留原设置。

创建 Codex 连接时，向导还会提供 Fast 或 Standard 服务选项。Fast 请求优先处理，可能消耗更多配额，也不保证更快。用 `/fast` 临时切换，或修改 Model 以永久设置。

设置向导为所选路由保存已知的媒体输入能力。使用自定义端点时，先验证图像、音频和视频输入支持，再修改这些声明。

见[模型设置](models-and-authentication.md#native-request-settings-and-connection-wiring)、[上下文预算](models-and-authentication.md#context-and-modality-policy)和 [Fast 模式](models-and-authentication.md#fast-mode-and-service-tiers)。

## 3. 选择执行权限

- **Full Control：** 命令使用宿主机账户的文件系统和网络权限运行，不需要 Envd 运行时。
- **Sandbox：** 保存前，向导会检查本地隔离的前置条件。失败不会降级到 Full Control。Windows 内置执行仅支持 Full Control。

选择后即保存配置，不会额外确认。选择前请阅读[执行权限](environments-and-projects.md#execution-permissions)。

TUI 使用启动目录作为工作目录；首次提示时，会为该目录选择或创建单根 Project。设置不要求 Project 文件，也不会创建默认的 `project-local` 资源。

## 高级设置

```console
a13n-harness-ui setup --advanced
```

高级设置提供可选的上下文、推理、shell 审查、subagent 和指令选项。常规设置会填入默认配置，之后仍可编辑 YAML；这些设置并非隐藏的应用状态。

常规设置包含三种内置 Subagent 角色，并初始化尚未配置的 `security.shell_review`：用 Model 审查 shell 启动，在风险达到 `extra_high` 时请求审批。Codex 设置选择独立的审查 Model，并开启 Guardian 额度关联。现有审查设置保持不变。审查与隔离是两回事；见 [shell 审查配置](configuration-recipes.md#configure-shell-review)。

内置 subagent 继承父级 Model。高级设置提供全部启用或全部关闭选项；要选择单独角色，请编辑 `subagents.include`。导入外部 Codex/Claude Code subagent 是独立的 `/import` 工作流，不属于设置流程。

## 添加其他连接或 Agent

```console
a13n-harness-ui add model
a13n-harness-ui add agent
```

`add model` 创建可复用的 Model，并提供独立的 Agent 创建流程。`add agent` 可让你复用 Model 或创建新 Model，再为 Agent 命名。复用只引用已有 Model，不会复制或修改它。重复名称会分配不同身份；现有默认值和对话保持不变。

使用 `/agent` 切换整个 Agent。使用 `/model` 为当前 Project 选择已配置的 Model，同时保留当前 Agent 的指令和工具；`/model default` 清除该选择。

## 取消或恢复设置

使用上/下方向键和 Enter，或输入选项编号。Esc 返回上一步；Ctrl+C 或 Ctrl+D 取消。取消首次设置会返回 shell，不会打开 TUI 输入框。

取消设置会保留已完成的登录和已保存的密钥。保存多个文件失败时，先检查错误列出的路径再重试。旧版 `.a13n-harness-ui-setup-recovery-*` 目录保持不变。

## 查看保存的内容

生成的根配置会显式开启 [WebUI Sidekick](configuration.md#webui-sidekick)：

```yaml
webui:
  sidekick: {}
```

要关闭 Sidekick，设置 `sidekick: null`，或选择 **Settings → General → Sidekick → Disabled**。设置向导保留已有 Sidekick 选择；该偏好本身不会开始工作。

```console
a13n-harness-ui config path
a13n-harness-ui config show --format json
a13n-harness-ui config validate
```

接下来可阅读[配置用法](configuration-recipes.md)或[入门根配置](configuration.md#starter-root-document)。
