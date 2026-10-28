---
title: 设置第一个 Agent
description: 连接模型账户、选择工具执行权限，并验证第一条回复。
---

完成本指南后，你将得到一个能回复消息的 Agent。开始前，先[安装 Harness UI](installation.md)，并准备受支持的模型订阅或提供方 API 密钥。

从希望处理的目录启动：

```console
cd your-repository
a13n-harness-ui
```

将 `your-repository` 替换为自己的目录。未配置默认 Agent，或默认 Agent 没有 Model 时，会打开设置向导。如果已有 Agent，想重新运行向导，先退出终端界面，再在 shell 中运行 `a13n-harness-ui setup`。使用 WebUI 时，打开 `a13n-harness-ui webui` 打印的登录链接，按页面完成设置。

## 1. 连接 Model

**Model** 是保存的模型提供方连接。先为这次会话选择一种连接方式：

- **订阅：** 选择 ChatGPT、Codex、Grok 或 GitHub Copilot。复用检测到的账户，或按显示的提示登录。如果要立即测试回复，先完成登录再继续。
- **API 密钥：** 选择提供方或协议，在向导的不回显字段中输入密钥。使用自定义端点时，还需要提供 API base URL，并选择端点支持的协议。

这里输入的 API 密钥与 Model YAML 分开保存。如果已有凭据管理方式，可输入 `env:OPENAI_API_KEY` 读取环境变量，或输入 `key:key-primary` 复用已保存的密钥。环境变量必须对启动 Harness UI 的进程可用。只在专用字段中输入密钥，不要把密钥发送为对话消息。

也可以先配置连接，稍后再运行 `a13n-harness-ui login <provider>` 登录。各提供方的登录方法和凭据存储见[身份验证指南](models-and-authentication.md#subscription-login-and-api-keys)。

## 2. 选择模型和设置

选择推荐模型，或输入区分大小写的 API 模型 ID。使用你的账户能够访问的模型；推荐列表来自目录，不代表已通过账户访问测试。复用已有 Model 时，其保存的设置保持不变。

第一次使用时，没有特定需求就保留向导提供的模型设置。之后可以修改[推理和上下文预算](models-and-authentication.md#context-and-modality-policy)。Codex 连接中的 **Fast** 请求优先服务，可能消耗更多配额，不保证回复更快。

API 密钥设置还提供可选的 **Shell review model**。选择模型来审查 shell 命令，或跳过以保持审查关闭。选择后会创建一个独立 Model，复用相同的端点和密钥引用。每次审查都会发出额外的模型请求。Shell 审查和执行隔离是两项独立控制。

## 3. 选择执行权限

这个选择决定文件和命令工具可使用的权限：

| 选项             | 用途与效果                                                                        |
| ---------------- | --------------------------------------------------------------------------------- |
| **Full Control** | 工具使用 Host 账户的文件系统和网络权限。Direct 执行不需要下载 Envd。              |
| **Sandbox**      | 设置向导准备本地隔离环境，检查前置条件后才保存。检查失败不会切换到 Full Control。 |

选择 Sandbox 时，先阅读[平台要求](environments-and-projects.md#execution-permissions)。Windows 使用实验性的原生 MXC 隔离。

终端向导完成选项后会保存配置。WebUI 中，等待 **Workspace** 检查完成，再选择 **Save and start chatting**。Sandbox 准备可能需要下载并校验 Envd；执行选项旁和页脚会显示进度与失败信息。可以取消准备，也可以重试失败的检查。

不需要创建 Project 文件。终端使用启动目录，并在你发送第一条提示时选择或创建单根 Project。

## 4. 验证回复

终端首次设置完成后会打开输入框。如果显式运行了 `setup` 命令，它会返回 shell；此时重新启动 `a13n-harness-ui`。WebUI 保存后会打开空对话。保存配置本身不会发送模型请求。

先发送一条不需要文件或 shell 工具的消息：

```text
Reply with one short greeting. Do not use tools.
```

收到回复，说明选中的 Model 可以处理请求。接着在代码库中尝试只读任务：

```text
Read the README and summarize what this project does. Do not modify files.
```

如果问候消息失败，先检查模型 ID 和身份验证，再排查工具。如果问候成功但文件任务失败，检查工作目录和 [Environment 权限](environments-and-projects.md)。[日常使用指南](everyday-use.md)介绍追问、停止工作和打开已保存对话。

## 查看保存的内容

先退出终端界面，再运行以下 shell 命令：

```console
a13n-harness-ui config path
a13n-harness-ui config show --format json
a13n-harness-ui config validate
```

`config path` 显示所选配置和数据路径。根 YAML 保存默认值；同级 `models/` 和 `agents/` 目录保存 Model 与 Agent 资源。`config validate` 检查文件和引用，不发送模型请求，因此仍需单独进行上面的回复测试。

新配置的设置向导还会用 `webui.sidekick: {}` 开启 [WebUI Sidekick](configuration.md#webui-sidekick)。要关闭它，选择 **Settings → General → Sidekick → Disabled**，或设置 `sidekick: null`。设置向导保留已有选择；这个偏好本身不会开始工作。

## 添加其他连接或 Agent

第一个 Agent 正常工作后，可在 shell 中使用以下任一命令：

```console
a13n-harness-ui add model
a13n-harness-ui add agent
```

`add model` 创建可复用的 Model，之后可以进入独立的 Agent 创建流程。`add agent` 让你复用 Model 或新建 Model，再为 Agent 命名。复用只引用已有 Model，不会复制或修改它。已有默认值和对话保留原来的选择。

终端中，用 `/agent` 切换整个 Agent。用 `/model` 为当前 Project 更换 Model，同时保留 Agent 的指令和工具；`/model default` 清除该选择。通过文件修改配置时，继续阅读[配置用法](configuration-recipes.md)。

## 高级设置

运行 `a13n-harness-ui setup --advanced`，可在设置时选择可选的上下文、推理、shell 审查、subagent 和指令设置。生成的设置都可以通过 YAML 编辑。

常规设置包含三种内置 Subagent 角色，继承父级 Model。高级设置提供全部开启或全部关闭选项，之后可用 `subagents.include` 选择单独角色。导入 Codex 或 Claude Code 角色使用独立的 `/import` 工作流。

选择 shell 审查后，向导会初始化尚未配置的审查设置：审查 shell 启动，在风险达到 `extra_high` 时请求审批。API 密钥设置没有选择审查模型时，快捷配置保持关闭。Codex 设置会创建独立审查 Model，并开启 Guardian 额度关联。已有审查设置保持不变。更换审查模型或策略见 [shell 审查](configuration-recipes.md#configure-shell-review)。

向导为选中的路由保存已知的媒体输入能力。为自定义端点添加图像、音频或视频支持前，先验证端点实际支持这些输入；见 [Model 输入策略](models-and-authentication.md#context-and-modality-policy)。

## 取消或恢复设置

终端向导中，用上/下方向键和 Enter，或输入选项编号。Esc 返回上一步；Ctrl+C 或 Ctrl+D 取消。取消首次设置会返回 shell。

取消后，已完成的登录和已保存的密钥仍可使用。多文件保存失败时，先检查错误中列出的路径，再重试。旧版 `.a13n-harness-ui-setup-recovery-*` 目录保持不变。
