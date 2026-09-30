---
title: 设置第一个 Agent
description: 连接模型、创建 Agent，并选择执行权限。
---

设置向导会连接 Model、创建 Agent，并选择执行权限。未配置 Model 时会自动运行。要重新运行，先退出聊天，然后执行：

```console
a13n-harness-ui setup
```

聊天中没有 `/setup` 命令。显式运行设置会在保存后返回 shell；首次设置则会打开聊天。

## 1. 连接 Model

选择支持的订阅连接或 API 密钥连接。

| 连接方式   | 你需要提供的内容                | 凭据存储位置                       |
| ---------- | ------------------------------- | ---------------------------------- |
| Codex 订阅 | 可复用的登录状态                | 共享 Codex 账户存储                |
| Grok 订阅  | 可复用的登录状态                | 共享 Grok 账户存储                 |
| API 密钥   | 提供方/协议、模型 ID 和密钥引用 | 环境变量或 Harness UI 本地密钥存储 |

向导会检测现有的兼容订阅登录，并允许复用。如果缺少登录，向导会显示外部命令 `a13n-harness-ui login codex` 或 `a13n-harness-ui login grok`，然后提供重新检查选项。登录在终端 UI 之外进行；聊天中没有 `/login` 命令。如果账户存储格式有误或当前版本不支持，向导会给出修复提示，不会自动替换。

使用 API 密钥时，向导提供不回显的密钥输入框，也可填写 `env:OPENAI_API_KEY` 或 `key:key-primary`。不要将密钥粘贴到聊天输入框。新输入的密钥会立即保存到独立的本地密钥存储，不会写入 Model YAML。

登录方式、已存密钥和账户位置见[模型与身份验证](models-and-authentication.md#subscription-login-and-api-keys)。

## 2. 选择模型和设置

从已安装的入门目录中选择，或输入区分大小写的 API 模型 ID。该目录是随安装包提供的参考，**不是** 实时的权限或可用性检查。现有 Model 资源会保留原设置，不会静默迁移到新的默认值。

创建 Codex 连接时，向导还会提供 Fast 或 Standard 服务选项。Fast 请求优先处理，可能消耗更多配额，也不保证更快。用 `/fast` 临时切换，或修改 Model 以永久设置。

已知模型路由会保存原生图像、音频和视频声明。这些声明告诉 Harness 应发送哪些输入，不能让端点获得其本身不支持的模态。为自定义端点填写这些声明前，请先验证其实际能力。

见[模型设置](models-and-authentication.md#native-request-settings)、[上下文预算](models-and-authentication.md#context-and-modality-policy)和 [Fast 模式](models-and-authentication.md#fast-mode-and-service-tiers)。

## 3. 选择执行权限

- **Full Control：** 命令使用宿主机账户的文件系统和网络权限运行，不需要 Envd 运行时。
- **Sandbox：** 保存前，向导会检查本地隔离的前置条件。失败不会降级到 Full Control。Windows 内置执行仅支持 Full Control。

选择后即保存配置，不会额外确认。选择前请阅读[执行权限](environments-and-projects.md#execution-permissions)。

工作目录就是终端工作区。设置不要求 Project 文件，也不会创建默认的 `project-local` 资源。

## 高级设置

```console
a13n-harness-ui setup --advanced
```

高级设置提供可选的上下文、推理、shell 审查、subagent 和指令选项。常规设置会填入默认配置，之后仍可编辑 YAML；这些设置并非隐藏的应用状态。

常规设置包含三种内置子角色；如果尚未配置，还会以 `extra_high` 阈值开启 `security.shell_review`。它用配置的 Model 审查 shell 启动，并在标记风险时请求审批。现有审查设置会保留。这种审查不是隔离，也不会检查每条命令；完整策略见[工具审查配置](configuration-recipes.md#configure-tool-review)。

内置 subagent 继承父级 Model。高级设置提供全部启用或全部关闭选项；要选择单独角色，请编辑 `subagents.include`。导入外部 Codex/Claude Code subagent 是独立的 `/import` 工作流，不属于设置流程。

## 添加其他连接或 Agent

```console
a13n-harness-ui add model
a13n-harness-ui add agent
```

`add model` 创建可复用的 Model，并提供独立的 Agent 创建流程。`add agent` 可让你复用 Model 或创建新 Model，再为 Agent 命名。复用只引用已有 Model，不会复制或修改它。重复名称会分配不同身份；现有默认值和对话保持不变。

使用 `/agent` 切换整个 Agent。使用 `/model` 临时尝试已配置的 Model，同时保留当前 Agent 的指令和工具。

## 取消或恢复设置

使用上/下方向键和 Enter，或输入选项编号。Esc 返回上一步；Ctrl+C 或 Ctrl+D 取消。取消首次设置会返回 shell，不会打开聊天。

即使取消设置，已完成的登录或已保存的密钥仍会保留。如果发布多个文件时失败，重试前先检查错误中列出的已完成路径。任何 `.a13n-harness-ui-setup-recovery-*` 文件，都应在检查原始内容之后再处理。

## 查看保存的内容

生成的根配置会显式开启 [WebUI Sidekick](configuration.md#webui-sidekick)：

```yaml
webui:
  sidekick: {}
```

这不会自动启动任务。设置 `sidekick: null`，或选择 **Settings → General → Sidekick → Disabled** 可以关闭此偏好。再次运行设置会保留明确的关闭选择或自定义 Agent/Model 选择。

```console
a13n-harness-ui config path
a13n-harness-ui config show --format json
a13n-harness-ui config validate
```

接下来可阅读[配置用法](configuration-recipes.md)或[完整根配置参考](configuration.md#complete-root-document)。
