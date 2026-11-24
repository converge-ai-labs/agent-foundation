---
title: 模型
description: 在 Harness 中配置模型路由、按运行动态解析器、上下文预算、图像输入策略以及网关会话亲和性。
---

按应用边界选择 Harness 解析模型的方式：

| 场景需求                            | 配置方式                                                                         |
| ----------------------------------- | -------------------------------------------------------------------------------- |
| 固定提供方路由                      | `AgentSpec(model="openai-responses:gpt-5")`                                      |
| 具体原生客户端或离线 `TestModel`    | `HarnessBuilder().build(..., model=native_model)`（保持 `AgentSpec.model=None`） |
| 按次动态选择租户、账号或 OAuth 凭据 | `AgentSpec.model` 填逻辑标识 + 每次传入 `RunBindings.model_resolver`             |
| 跨智能体图共享的静态网关工厂        | `HarnessBuilder(gateway_provider_factory=...)` 配合 `gateway@provider:model`     |

## 模型选择

在 `AgentSpec.model` 中保存不含密钥的逻辑标识，并在每次 Run 中动态解析原生 Pydantic AI `Model`：

```python title="models_demo.py"
import asyncio
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from pydantic_ai.models.function import FunctionModel


async def respond(messages, info):
    yield "Route selected"


async def resolve(context, model_id):
    assert model_id == "logical:support"
    return FunctionModel(stream_function=respond)


async def main() -> None:
    executable = HarnessBuilder(configured_plugins_enabled=False).build(
        AgentSpec(model="logical:support"), output_type=str
    )
    result = await executable.run(
        "First question", bindings=RunBindings.embedded(model_resolver=resolve)
    )
    print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

如需同步构造或修补原生模型，可使用 `infer_model("openai-responses:gpt-5", provider_factory=..., patches=...)`；也可通过 `a13n_harness.model_catalog.get_official_model_catalog()` 查询内置官方模型特性目录。

## 模型特性与预设别名

使用 `AgentSpec.model_characteristics`（`HarnessModelCharacteristics`）配置本地上下文预算，使用 `AgentSpec.model_settings` 配置提供方请求参数，或通过 `resolve_model_characteristics` 与 `resolve_model_settings` 解析预设别名：

```python
from a13n_harness import AgentSpec, HarnessModelCharacteristics
from a13n_harness.models import resolve_model_characteristics, resolve_model_settings
from pydantic_ai.settings import ModelSettings

model = "anthropic:claude-sonnet-5"
spec = AgentSpec(
    model=model,
    model_characteristics=resolve_model_characteristics(
        model,
        aliases=("anthropic:context-1m",),  # context-200k | context-400k | context-1m
        overrides=HarnessModelCharacteristics(compact_threshold=0.85),
    ),
    model_settings=resolve_model_settings(
        model,
        aliases=("anthropic:interleaved-thinking", "anthropic:max-output-128k"),
        overrides=ModelSettings(temperature=0.2),
    ),
)
```

### 图像输入策略

通过 `HarnessModelCharacteristics.image_input` 配置请求前图片预处理（`ImageInputPolicy`，设为 `None` 可禁用；详见[过滤器](context.md#filters)）：

| 参数                                                                    | 默认值                 | 含义                                            |
| ----------------------------------------------------------------------- | ---------------------- | ----------------------------------------------- |
| `support_gif`                                                           | `true`                 | 允许二进制 GIF（`false` 时替换为说明文本）      |
| `max_images` / `image_retention_ratio`                                  | `20` / `0.6`           | 单次请求最大图片数及溢出时保留的最新比例        |
| `max_image_bytes` / `max_image_dimension`                               | `5242880` / `8000`     | 单张图片 base64 字节上限（5 MiB）与最大像素边长 |
| `split_large_images`（`image_split_max_height`, `image_split_overlap`） | `true`（`4096`, `50`） | 压缩前自动切分超长静态图                        |

## 模型请求亲和性

配置网关会话亲和头以及自动 `openai_prompt_cache_key` 注入（对 `gpt-<数字>*` 与 `openai/gpt-<数字>*` 模型默认开启，也可通过环境变量 `A13N_HARNESS_MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED` 覆盖）：

```python
from a13n_harness import HarnessBuilder

builder = HarnessBuilder(
    session_affinity_header="x-litellm-session-id",
    openai_prompt_cache_key_enabled=True,
)
```

Harness 基于 `thread_id` 派生确定性的 UUID v5（`a13n_harness.model_affinity.derive_model_affinity_id`）填充上述标识。

## 出站 HTTP 代理

由 `create_model_http_client()` 创建的客户端遵循 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 与 `NO_PROXY`（支持 CIDR 网段与域名后缀匹配）。

## 出站 TLS 验证

受管 HTTP 客户端默认验证服务端证书与主机名。仅在受控本地抓包调试时，可在启动宿主进程前设置 `A13N_OUTBOUND_TLS_VERIFY=false`。
