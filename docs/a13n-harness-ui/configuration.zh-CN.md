---
title: 配置参考
description: Harness UI 根配置文件 a13n-harness-ui.yaml、资源目录、优先级规则、内置工具、记忆以及 WebUI 服务器设置参考。
---

Harness UI 读取一个根配置文件（`a13n-harness-ui.yaml`）以及同级资源目录（`models/`、`agents/`、`projects/`、`devices/`、`mcp_servers/`、`subagents/`、`web_providers/`）。

## 定位并校验配置文件

```console
a13n-harness-ui config path
a13n-harness-ui config validate
```

默认根配置路径为 `~/.a13n-harness-ui/a13n-harness-ui.yaml`。在任意子命令前传入 `--config /path/to/a13n-harness-ui.yaml` 可使用其他配置树。

| 资源目录 / 文件              | 负责内容                                                                            |
| ---------------------------- | ----------------------------------------------------------------------------------- |
| `models/*.yaml`              | [提供方连接、凭据引用与请求参数](models-and-authentication.md#model-file-reference) |
| `agents/*.yaml`              | [指令、模型选择、能力与子智能体](agents-and-subagents.md#agent-file-reference)      |
| `web_providers/*.yaml`       | [可复用的宿主机搜索/抓取账号](native-and-web-tools.md#web-provider-accounts)        |
| `subscription_groups/*.yaml` | [有序订阅账号组](models-and-authentication.md#account-store-locations)              |
| `projects/*.yaml`            | [工作目录、设备绑定与默认值](environments-and-projects.md#project-file-reference)   |
| `devices/*.yaml`             | [远程 Envd 设备连接](environments-and-projects.md#add-device-bindings)              |
| `mcp/*.yaml`                 | [MCP 服务器定义](mcp.md#mcp-field-reference)                                        |
| `subagents/*.md`             | [轻量级 Markdown 子智能体](agents-and-subagents.md#write-a-markdown-subagent)       |
| `AGENTS.md`                  | [全局智能体指令](agents-and-subagents.md#instructions-and-guidance)                 |

## 优先级与生效时机

新对话按以下顺序解析默认值：**显式 CLI/界面选择 → 项目默认值 → 智能体默认值 → 根文件 `defaults`**。正在进行的运行保持其已捕获配置，编辑仅对后续运行生效。

## 最小根配置

```yaml title="a13n-harness-ui.yaml"
schema_version: "1"
defaults:
  agent: agent-coder
  environment_profile: environment-native
```

## 内置工具与子智能体

```yaml title="a13n-harness-ui.yaml — tools, input, and subagents"
tools:
  enable_ask_user_question: true
  enable_codeact: true
  interaction_timeout_seconds: 120
input:
  steering_mode: immediate # 或 queued
subagents:
  include: [explorer, code-reviewer, executor]
  max_concurrent: 8
  dynamic:
    enabled: true
    allowed_models: [model-primary]
    default_model: model-primary
```

- `tools.enable_ask_user_question` / `enable_codeact`：全局开关，同时约束自动启用与智能体显式声明。
- `input.steering_mode`：`immediate`（默认）立即中断模型生成或释放受支持的工具等待；`queued` 在下一个安全边界送达。

## Shell 审查快捷开关

为根运行和子运行的 `environment.shell_exec` 启用自动 LLM 安全审查：

```yaml title="a13n-harness-ui.yaml — security.shell_review"
security:
  shell_review:
    enable: true
    model: model-reviewer
    risk_threshold: extra_high
    on_flagged: approval_required
    on_error: allow
```

审批阈值与 Codex Guardian 关联见[配置 Shell 审查](configuration-recipes.md#configure-shell-review)。

## 上下文恢复

自动上下文恢复默认开启，在模型提供方上下文窗口溢出时独立于主动压缩进行恢复。

## 显示设置

在 `a13n-harness-ui.yaml` 或 **Settings → General** 中配置 TUI/WebUI 显示偏好、通知音效与收件箱开关（`inbox.enabled: true`）。

## 文件记忆

启用持久化全局与项目 Markdown 记忆（存放在 `memory/`）：

```yaml title="a13n-harness-ui.yaml — memory"
memory:
  enabled: true
  auto_organize:
    enabled: true
    model: model-primary
```

可在 WebUI 的 **Memory** 面板中查看记忆文件与自动整理记录。

## 媒体理解与语音输入

- `media_understanding`（`image`、`audio`、`video`）：当前模型不支持原生读取对应媒体时的回退模型 ID，见[媒体理解默认模型](models-and-authentication.md#media-understanding-defaults)。
- `dictation`：在 **Settings → Chats → Dictation** 配置 Codex 订阅或音频模型语音输入，见[语音输入](webui.md#voice-input)。

## 本地 envd 运行时

在 `a13n-harness-ui.yaml` 的 `envd:` 下配置托管或自定义 `a13n-envd` 可执行文件、本地计算机使用（`computer_use: true`）及资源监控采样间隔（`monitoring_interval_seconds: 60`，范围 10–60）。详见[管理本地 envd](environments-and-projects.md#manage-local-envd)。

## 宿主机浏览器

在 `a13n-harness-ui.yaml` 的 `browser:` 下覆盖 Chromium 可执行路径或沙箱参数，详见[宿主机浏览器](host-browser.md)。

## WebUI 允许的来源

在反向代理后运行 WebUI 时，限制允许的 `Host` / `Origin` 地址：

```yaml title="a13n-harness-ui.yaml — webui.allowed_origins"
webui:
  allowed_origins:
    - https://workbench.example.com
```

默认 `[]` 仅允许监听地址；`["*"]` 允许任意来源。详见[反向代理](running-webui.md#reverse-proxies-and-public-addresses)。

## WebUI Bot

在 `a13n-harness-ui.yaml` 的 `webui.bot:` 下配置常驻 Bot，详见 [Bot](bot.md)。

## MCP 宿主连接管理

- `mcp.host_owned_servers`：跨运行保持客户端存活的 MCP 服务器 ID 列表。
- `mcp.protocol_overrides`：按服务器 ID 覆盖协议版本（`auto`、`legacy` 或 `2026-07-28`）。

## 对象存储

Harness UI 自动维护会话级本地对象存储（嵌入调用时可通过 `HarnessUiSettings` 的 `ObjectStoreSettings` 配置），支持通过 `object_ref_tools` 在工具调用间直接传递媒体与大型输出引用，避免在模型历史中重复携带 Base64 字节。
