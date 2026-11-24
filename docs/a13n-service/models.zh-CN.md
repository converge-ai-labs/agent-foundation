---
title: 模型
description: 在 Service 中接入模型提供方、注册工作区模型、配置推理与上下文默认参数、设置自定义计费单价并启用多模态媒体解析。
---

先[连接模型提供方](model-providers.md#model-providers)保存上游凭据，随后在工作区中注册**模型**，供智能体通过不可变的 `key` 引用。

## 添加模型

在 Console 打开 **Models → Add model**，选择提供方，从模型目录点选（或手动输入上游模型 ID），核对输入能力、默认参数与单价后保存。

通过 API 创建（`POST /api/v1/models`，需具备工作区与提供方的 `write` 权限）：

```json
{
  "provider_id": "mprov_...",
  "key": "local-llama",
  "name": "Llama (local)",
  "config": {
    "model_name": "llama3.3",
    "model_api": "ollama.chat_completions",
    "characteristics": {"context_window_tokens": 131072},
    "settings": {"max_tokens": 4096}
  }
}
```

- 可通过 `GET /api/v1/model-catalog?provider=openai&model=gpt-5.5` 查询公开目录元数据，辅助填充 `characteristics`、`pricing` 与 `catalog_ref`。
- 携带 `If-Match` 调用 `PATCH /api/v1/models/{key}` 更新模型（`config` 为整体替换；传入 `{"enabled": false}` 停用模型）。
- 在 `config.characteristics` 中声明原生多模态输入能力（`image_understanding`、`video_understanding`、`audio_understanding`、`document_understanding`）及可选 `image_input` 策略（见[共享图片策略](../a13n-harness/models.md#image-input-policy)）。

### 高级请求参数

在 `config.settings` 中配置模型全局默认值，在 `agent.config.model_settings` 中配置智能体级覆盖项（如 `openai_reasoning_effort`、`max_tokens`、`temperature`、`cache: "12h"`、`openai_store: false`、`extra_headers`、`extra_body`）。

优先级顺序：Service 基线默认值 → 模型 `config.settings` → 智能体 `model_settings`（单次运行传入的 `model_settings` 会先整体替换智能体的 `model_settings`，再叠加在模型默认值之上）。如需开启网关会话亲和缓存路由，请在模型提供方的 `config.session_affinity_header` 中指定请求头名。

## 媒体理解

当主模型不支持原生读取图片、音频或视频时，可在 **Workspace settings → Media understanding**（或通过 `GET` + 带 `If-Match` 的 `PUT /api/v1/media-understanding-defaults` 传入 `{"image": "model-key", "video": null, "audio": null}`）配置工作区级回退模型。智能体显式配置的 `media_understanding` 优先于工作区默认值。

## 用量与计费

Service 将每次模型调用（包括上下文压缩、工具风险审查、媒体理解与子智能体）归因到所选工作区模型 `key`，并按该模型保存的 `pricing` 快照记录到[用量账本](inspect-runs.md#usage)中。

## 模型提供方

<span id="chatgpt-subscription-provider"></span> <span id="self-hosted-callback"></span>

- [模型提供方参考](model-providers.md)
