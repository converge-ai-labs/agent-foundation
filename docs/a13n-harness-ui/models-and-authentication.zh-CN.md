---
title: 模型与认证
description: 在 Harness UI 中配置 API 密钥与 OAuth 订阅（Codex、ChatGPT、Grok、GitHub Copilot），设置推理深度并管理上下文窗口。
---

**Model**（`models/*.yaml`）是可复用的提供方连接与请求参数配置；**Agent** 通过 ID 选择模型，并叠加指令与工具。凭据与 YAML 配置分开存储。

## 创建与管理模型

在 WebUI 的 **Settings → Models** 或通过 CLI 添加模型：

```console
a13n-harness-ui add model
```

1. 选择 API 提供方/协议，或选择订阅连接（ChatGPT、Codex、Grok、GitHub Copilot）。
2. 绑定凭据：选用已保存密钥、输入新密钥、引用环境变量，或选择订阅账号组。
3. 选择推荐模型或填写提供方模型 ID，确认默认参数（推理深度、速度档位、上下文预算）后保存。
4. 在对话中切换该模型（TUI 使用 `/model`，WebUI 在输入框底部切换），发送一条简短消息验证连通性。

TUI 常用命令：

| 命令                               | 效果                           |
| ---------------------------------- | ------------------------------ |
| `/agent` 或 `/agent agent-primary` | 切换完整智能体配置             |
| `/model` 或 `/model model-primary` | 覆盖当前模型并记住为本项目默认 |
| `/model default`                   | 清除模型覆盖与项目记忆         |
| `/thinking` / `/thinking default`  | 查看或重置下次运行的推理深度   |
| `/status`                          | 查看当前智能体、模型与用量     |

## Codex 模型示例

1. 执行 `a13n-harness-ui login codex` 完成登录。
2. 在根配置目录（通过 `a13n-harness-ui config path` 查看）下保存 `models/codex.yaml`：

```yaml title="models/codex.yaml"
schema_version: "1"
kind: model
id: model-codex
name: Codex
route: openai-codex:gpt-6.2-sol
authentication:
  kind: codex_subscription
settings:
  openai_reasoning_effort: high
  openai_reasoning_summary: detailed
  openai_store: false
model_characteristics:
  context_window_tokens: 350000
  proactive_context_management_threshold: 0.65
  compact_threshold: 0.90
```

3. 运行 `a13n-harness-ui config validate`，随后在对话中执行 `/model model-codex` 并发送测试消息。

## 模型文件参考

每个模型文件均需包含 `schema_version: "1"`、`kind: model`、唯一的 `model-` 前缀 ID 以及 `name`：

| 字段                    | 默认值 | 用途                                                 |
| ----------------------- | ------ | ---------------------------------------------------- |
| `route`                 | 必填   | 提供方与模型 ID（如 `openai-responses:gpt-5`）       |
| `authentication`        | 必填   | 与路由匹配的凭据来源                                 |
| `settings`              | `{}`   | 请求参数（推理设置、`max_tokens`、`temperature` 等） |
| `model_configuration`   | `{}`   | 连接参数（`base_url`、`session_affinity_header`）    |
| `model_characteristics` | `null` | 本地上下文预算、压缩阈值与媒体输入策略               |

API 密钥认证二选一引用环境变量或已保存密钥（通过 `a13n-harness-ui auth key set key-primary` 保存）：

```yaml
authentication:
  kind: api_key
  env: OPENAI_API_KEY # 或 credential_ref: key-primary
```

订阅认证使用 `chatgpt_subscription`、`codex_subscription`、`grok_subscription` 或 `copilot_subscription`（Codex/Grok 可选填 `group`）。

### 上下文与模态策略

在 `model_characteristics` 中配置本地预算与多模态能力：

| 字段                                     | 默认值     | 含义                                                                                                          |
| ---------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------- |
| `context_window_tokens`                  | `null`     | 本地工作上下文预算                                                                                            |
| `proactive_context_management_threshold` | `0.65`     | 触发上下文提醒的比例阈值                                                                                      |
| `compact_threshold`                      | `0.90`     | 触发上下文压缩的比例阈值                                                                                      |
| `capabilities`                           | `[]`       | 二进制输入声明：`image_understanding`、`audio_understanding`、`video_understanding`、`document_understanding` |
| `image_input`                            | 默认策略   | 图片限制（`max_images`、`image_retention_ratio`、`max_image_bytes`、`support_gif`）；`null` 表示禁用          |
| `video_input.max_video_bytes`            | `10485760` | 单个视频及单次请求的 Base64 编码体积上限                                                                      |

## 原生请求参数与连接配置

使用 `settings` 传递提供方原生参数（`max_tokens`、`temperature`、`thinking`、`openai_reasoning_effort`、`anthropic_thinking`、`google_thinking_config`、`service_tier`、`openai_transport: websocket|http`、`extra_headers`、`extra_body`）。

使用 `model_configuration.base_url` 配置自定义 HTTP/API 密钥端点或网关（例如 `route: google-cloud:<model-id>` 搭配 `base_url: https://gateway.example.com`）。订阅路由与原生 `xai:` gRPC 不支持自定义 `base_url`。

## 订阅登录与 API 密钥

| 提供方         | 登录命令                                | 说明                                       |
| -------------- | --------------------------------------- | ------------------------------------------ |
| API 密钥       | `a13n-harness-ui auth key set <key-id>` | 存入本地密钥库，通过 `credential_ref` 引用 |
| Codex / Grok   | `a13n-harness-ui login codex` / `grok`  | 浏览器 OAuth，支持命名账号组               |
| ChatGPT        | `a13n-harness-ui login chatgpt`         | OAuth 回调 URL 粘贴流程                    |
| GitHub Copilot | `a13n-harness-ui login copilot`         | 使用公开 Copilot App ID 的设备授权流程     |

在**设置 → 账号 → 订阅账户**中，如果卡片提示「需要重新登录」，点击「重新登录」恢复授权。如果提示「用量刷新失败」，点击「重试刷新」。卡片会在上次获取的额度旁显示获取时间。

### GitHub Copilot 订阅

运行 `a13n-harness-ui login copilot`，打开终端打印的 GitHub 验证链接并输入设备码，无需自行注册 OAuth App。模型配置使用 `route: github-copilot:<model-id>` 与 `authentication: {kind: copilot_subscription}`。

### 账号存储位置

凭据与订阅组（`subscription_groups/*.yaml`）均存放在 Harness UI 状态目录（默认 `~/.a13n-harness-ui/`）中并受仅属主可读权限保护。切勿将原始 API 密钥或 OAuth Token 写入 YAML。

### 媒体理解默认模型

当当前模型不支持原生读取图片、音频或视频时，可在 **Settings → Models**（或 TUI `/model defaults`）中配置 `a13n-harness-ui.yaml` 下的 `media_understanding.image`、`.audio` 和 `.video` 回退模型 ID。
