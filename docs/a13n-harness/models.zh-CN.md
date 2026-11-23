---
title: 模型
description: 在 Harness 中配置模型选择、别名、上下文预算、图像输入策略、请求亲和性以及出站 HTTP 代理与 TLS 验证。
---

通过 `AgentSpec.model` 指定字符串路由（例如 `openai-responses:gpt-5`），或在 `HarnessBuilder().build(..., model=...)` 中直接传入具体的 Pydantic AI `Model` 实例。

## 模型选择

- **静态路由：** 设置 `AgentSpec(model="openai-responses:gpt-5", model_settings={"temperature": 0.1})`。
- **动态解析：** 当宿主需要在运行期解析凭据、OAuth 令牌或多租户路由时，在 `RunBindings` 上提供 `model_resolver`。订阅 OAuth 流程见[模型认证](model-authentication.md)。
- **模型特性：** 通过 `AgentSpec.model_characteristics` 声明 `context_window_tokens`、压缩阈值以及支持的二进制输入模态（`image_understanding`、`audio_understanding`、`video_understanding`、`document_understanding`）。

## 图像输入策略

控制 Harness 在发送给模型前如何预处理图片：

```python
from a13n_harness import AgentSpec, ImageInputPolicy, ModelCharacteristics

spec = AgentSpec(
    model_characteristics=ModelCharacteristics(
        image_input=ImageInputPolicy(
            support_gif=False,
            max_images=20,
            image_retention_ratio=0.6,
            max_image_bytes=5_242_880,  # 单张图片 5 MiB base64 上限
        )
    )
)
```

将 `image_input=None` 可禁用自动图片预处理。

## 模型请求亲和性

Harness 基于当前 `thread_id` 派生确定性的 UUID v5 会话亲和键，使支持提示词缓存路由的提供方（如 Codex 与 OpenAI 兼容网关）能将同一 Thread 的连续轮次路由到同一缓存分片。

## 出站 HTTP 代理

Harness 的 HTTP 客户端在创建提供方传输时遵循标准环境变量（`HTTP_PROXY`、`HTTPS_PROXY`、`NO_PROXY` 及其小写形式）。

## 出站 TLS 验证

默认开启 TLS 证书验证。如需信任企业私有 CA，请在宿主环境中设置 `SSL_CERT_FILE`（或 `REQUESTS_CA_BUNDLE`），或向提供方传入已配置的 `httpx.AsyncClient`。
