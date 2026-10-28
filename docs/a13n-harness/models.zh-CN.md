---
title: 模型
description: 为每次 Run 选择模型，设置上下文策略，并配置网关亲和性与传输。
---

Model 将 Agent 连接到 provider 或离线测试实现。先决定模型从哪里来，再设置请求选项和本地上下文策略。Harness 可以在每次 Run 中重新选择模型，同时让对话继续保留在同一个 Thread 中。

## 选择模型来源

| 需求                               | 模型来源                                                        |
| ---------------------------------- | --------------------------------------------------------------- |
| 固定 provider 路由                 | `AgentSpec(model="provider:model")`                             |
| 原生客户端、自定义传输或离线测试   | `build(..., model=native_model)`，保持 `AgentSpec.model` 未设置 |
| 根据当前用户、租户、账号或路由选择 | `AgentSpec.model` 字符串加新 `RunBindings.model_resolver`       |
| Agent 图共享的静态自定义网关配置   | Builder 的 `gateway_provider_factory`                           |

先准备[离线快速入门](getting-started.md)中的环境。下面的程序不需要凭据或网络。本页后续 provider 示例需要安装对应集成依赖，并准备凭据和模型访问权限。

## 模型选择

先将保存的对话与当前路由分开。将以下程序保存为 `models_demo.py`，运行 `uv run python models_demo.py`：

```python
import asyncio

from a13n_harness import AgentSpec, HarnessBuilder, HarnessState, RunBindings
from pydantic_ai.models.function import FunctionModel


async def main():
    resolutions = []

    async def respond(messages, info):
        yield "Route selected"

    async def resolve(context, model_id):
        assert model_id == "logical:support"
        resolutions.append((context.deps.run_id, context.deps.thread_id))
        return FunctionModel(stream_function=respond)

    executable = HarnessBuilder(configured_plugins_enabled=False).build(
        AgentSpec(model="logical:support"),
        output_type=str,
    )
    first = await executable.run(
        "First question",
        bindings=RunBindings.embedded(model_resolver=resolve),
    )
    print(first.output_or_raise())

    saved = first.state.model_dump_json()
    restored = HarnessState.model_validate_json(saved)
    second = await executable.run(
        "Follow-up question",
        previous_state=restored,
        bindings=RunBindings.embedded(model_resolver=resolve),
    )
    print(second.output_or_raise())
    assert first.run_id != second.run_id
    assert first.state.thread_id == second.state.thread_id
    assert resolutions == [
        (first.run_id, first.state.thread_id),
        (second.run_id, second.state.thread_id),
    ]


if __name__ == "__main__":
    asyncio.run(main())
```

程序打印两次 `Route selected`。断言验证了：第二次 Run 获得新 Run ID，保留原 Thread ID，并在 State JSON 往返后重新调用解析器。模型和解析器留在应用代码中；State 保存对话数据。

接入自己的应用时：

1. 在 `AgentSpec.model` 中保留 `logical:support` 这样的逻辑选择。
2. 在 `resolve(context, model_id)` 中，根据当前 Host 数据选择获授权的账号和路由。
3. 返回原生 Pydantic AI `Model`。每次 Run 都通过新 bindings 提供解析器，续接也一样。

解析器可以是异步函数或异步可调用对象。上下文类型为 `ModelResolutionContext[AgentContext]`，无需继承 Harness 基类或注册。内部恢复尝试共享当前 Run 的解析器和上下文。解析器也可使用生命周期更长、支持并发的 Host 客户端，由 Host 负责关闭。

已有具体 Model 时，省略字符串选择并传入 `model=`：

```python
from a13n_harness import AgentSpec, HarnessBuilder
from pydantic_ai.models.test import TestModel

executable = HarnessBuilder(configured_plugins_enabled=False).build(
    AgentSpec(system_prompt="Answer concisely."),
    output_type=str,
    model=TestModel(custom_output_text="Ready"),
)
```

该构建直接使用传入的模型，不调用 Run 解析器。固定的已认证路由则使用 `AgentSpec(model="openai-responses:gpt-5")`，省略 `model=`。没有解析器时，Harness 通过 `infer_model()` 构造字符串选择。

需要自定义构造或同步模型补丁时，可以直接调用 `infer_model()`：

```python
from a13n_harness import AgentSpec, HarnessBuilder, infer_model


def build_with_provider(provider_factory, apply_provider_profile):
    model = infer_model(
        "openai-responses:gpt-5",
        provider_factory=provider_factory,
        patches=(apply_provider_profile,),
    )
    return HarnessBuilder().build(AgentSpec(), output_type=str, model=model)
```

这里的 `provider_factory` 是应用提供的函数，从 provider 名称返回原生 Provider；`apply_provider_profile` 接收并返回原生 Model。补丁按顺序执行。helper 还接受静态 `common_headers`；逐请求 `ModelSettings.extra_headers` 以不区分大小写的方式优先。它将 `openai:` 映射到 `openai-responses:`，将旧 Google 前缀映射到 `google-cloud:`。结果仍是原生 Model。

字面形式 `gateway@provider:model` 选择 Pydantic AI 公开 Gateway Provider，使用 `PYDANTIC_AI_GATEWAY_API_KEY` 和可选 `PYDANTIC_AI_GATEWAY_BASE_URL`。其他网关名称需要自己的工厂：

```python
from a13n_harness import AgentSpec, HarnessBuilder


def build_with_gateway(gateway_provider_factory):
    return HarnessBuilder(gateway_provider_factory=gateway_provider_factory).build(
        AgentSpec(model="company@openai:gpt-5"),
        output_type=str,
    )
```

工厂接收 `(gateway_name, provider_name)` 并返回原生 Provider，负责 SDK 配置、凭据、重试、客户端和清理。Builder 也将其用于递归构建的子 Agent。会变化的授权或凭据应放在 Run 解析器中，而不是静态网关工厂中。

需要显式上下文信息时，查询包内不可变的官方目录：

```python
from a13n_harness.model_catalog import get_official_model_catalog

models = get_official_model_catalog()
characteristics = models["anthropic:claude-sonnet-5"].characteristics
```

每个条目包含带 provider 前缀的官方 ID、`HarnessModelCharacteristics` 和来源 URL。构建 spec 时显式赋予查询所得的特性；Builder 不会自动套用目录条目。目录存放模型事实；路由、凭据、请求预设、推理选项、别名、标签和应用默认值由 Host 管理。

## 出站 HTTP 代理

在启动 Host 的进程中设置代理变量。使用 `create_model_http_client()` 的路由，包括 Harness UI 的共享 API key 路由，读取 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 和 `NO_PROXY`，也支持小写形式。对于已安装的 Harness UI：

```bash
export http_proxy=http://127.0.0.1:8888
export https_proxy=http://127.0.0.1:8888
export no_proxy=localhost,127.0.0.1,::1
a13n-harness-ui
```

HTTP 代理可通过 CONNECT 承载 HTTPS 目标。`httpx2` 选择路由并判断绕过规则；直连和代理传输应用相同的有界重试策略。显式 `transport` 自己管理路由。设置 `retry=None` 会禁用重试，同时保留环境代理发现。

`httpx2` 2.14.0 及后续版本的 `NO_PROXY` 支持 IPv4/IPv6 CIDR，以及可带端口的方括号 IPv6，例如 `[::1]:8080`。例如，`NO_PROXY=localhost,127.0.0.0/8,10.0.0.0/8,[::1],fd00::/8,.internal.example` 让匹配目标直连。CIDR 匹配 URL 中的 IP 字面量；绕过判断不解析 DNS，因此需要单独列出主机名和域名后缀。无效条目会被忽略，有效条目继续生效。

升级时检查已有 CIDR 规则：以前经代理发送的地址现在可能改为直连。Provider 端点验证和 Run `allowed_hosts` 仍控制访问。未使用该 helper 的 SDK 传输遵循自己的代理规则。

## 出站 TLS 验证

Harness、Harness UI 和 Service 为自行构造的 HTTP 客户端验证目标证书与主机名。生产环境保留该默认值，需要时配置可信 CA。在使用拦截代理的受控开发环境中，运维人员可在启动 Host 前显式关闭验证：

```bash
export A13N_OUTBOUND_TLS_VERIFY=false
a13n-harness-ui
# Or start each Service control/worker process with the same environment.
```

删除变量或设为 `true` 即启用验证。值不区分大小写，并去除首尾空白；只接受 `true` 和 `false`。无效值使客户端构造和 Service 启动失败。修改后重启进程：客户端保留构造时的策略。这是进程设置，不是 Model、Agent、Provider、Run、YAML 或 TOML 配置字段。

策略覆盖自行管理的模型客户端，包括适配器创建的 Bedrock Converse、Web/媒体、远程 MCP、自有 OAuth 交换、管理请求、原生 HTTP Environment 操作和 HTTP Envd 连接。它控制直连、代理和 `NO_PROXY` 路由的目标 TLS。

显式传入的客户端、传输和 CA 上下文保留自己的策略。依赖创建的 SDK 客户端、内部传输不透明的推断 Model 路由、数据库/对象存储/遥测 SDK、HTTPS 代理节点自身的 TLS，以及独立 Envd daemon/broker 进程也保留各自配置。

> [!WARNING]
> `false` 移除服务器身份验证，允许凭据和内容被截获。HTTPS 仍加密传输，应用认证、主机限制、重定向、限额和重试规则继续生效。

## 模型编写别名

保存定义前，先将简短的编写选项解析为具体值。分别使用两个解析器处理 Harness 上下文特性和 provider 请求设置：

```python
from a13n_harness import (
    AgentSpec,
    HarnessModelCharacteristics,
)
from a13n_harness.models import (
    resolve_model_characteristics,
    resolve_model_settings,
)
from pydantic_ai.settings import ModelSettings

model = "anthropic:claude-sonnet-5"
characteristics = resolve_model_characteristics(
    model,
    aliases=("anthropic:context-1m",),
    overrides=HarnessModelCharacteristics(compact_threshold=0.85),
)
settings = resolve_model_settings(
    model,
    aliases=(
        "anthropic:interleaved-thinking",
        "anthropic:max-output-128k",
    ),
    overrides=ModelSettings(temperature=0.2),
)

spec = AgentSpec(
    model=model,
    model_characteristics=characteristics,
    model_settings=settings,
)
```

两类结果各有用途：

| 别名                                                                                | 具体效果                                                                                |
| ----------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `anthropic:context-200k`、`anthropic:context-400k`、`anthropic:context-1m`          | Harness 上下文预算为 `200_000`、`400_000` 或 `1_000_000` token                          |
| `anthropic:interleaved-thinking`                                                    | Anthropic adaptive thinking；保留 effort、token 上限、缓存、beta 请求头和上下文管理设置 |
| `anthropic:thinking-disabled`                                                       | 禁用 thinking，并移除从前一个别名继承的 effort                                          |
| `anthropic:max-output-32k`、`anthropic:max-output-64k`、`anthropic:max-output-128k` | 请求 `max_tokens` 为 `32_768`、`65_536` 或 `131_072`                                    |

上下文别名控制本地生命周期阈值。Provider 上下文变体、beta 请求头和实际容量仍由 provider 决定。Max-output 别名设置请求上限，由 provider 验证所选 Model 是否支持。

别名按顺序应用，具体覆盖最后应用。选择别名时需使用带 provider 前缀的直接或网关模型字符串；未知或不兼容别名立即失败。没有特性输入时，`resolve_model_characteristics()` 返回 `None`。设置解析返回独立的原生字典。Host 可提供不可变自定义目录，然后持久保存解析值。别名名称不进入 `AgentSpec`、执行 worker 或 Harness State。

## 模型特性

将本地输入策略和上下文预算放入 `model_characteristics`。显式上下文窗口也会出现在生效的原生 `ModelProfile` 上，让原生 Capability 读取相同预算：

```python
from a13n_harness import AgentSpec, HarnessModelCharacteristics

spec = AgentSpec(
    model="logical:support",
    model_characteristics=HarnessModelCharacteristics(
        context_window_tokens=200_000,
        proactive_context_management_threshold=0.65,
        compact_threshold=0.90,
    ),
)
```

使用这些值时，已选择的 `HandoffCapability()` 在 65% 处产生总结提醒。已选择的 `CompactionCapability()` 在请求前检查 90% 阈值：先使用原生 `RunContext.model.context_window` 和 `context_window_used`，原生值不可用时使用 Harness 特性和已捕获用量。显式 Capability token 阈值优先。

这些 Capability 需要另外选择，设置比例不会安装它们。可从自己的预设、官方目录或别名解析特性；Builder 不根据模型名称猜测。通过 `spec.model_characteristics` 读取特性；Pydantic 类级 `model_config` 有不同用途。不需要 Harness 扩展时仍可使用原生 Pydantic AI `AgentSpec`。

### 图片输入策略

将 `HarnessModelCharacteristics.image_input` 设为 `a13n_harness` 导出的冻结 `ImageInputPolicy`，为所选模型准备输入。省略时采用默认值，部分策略的未指定成员使用默认值，显式 `null` 关闭自动预处理。显式 `ImageFilterCapability` 保留自己的策略，不会再自动安装一份。父 Model、上下文预算和原生请求设置不决定该策略。

| 参数                     | 默认值    | 含义                                                    |
| ------------------------ | --------- | ------------------------------------------------------- |
| `support_gif`            | `true`    | 允许二进制 GIF；`false` 时替换为说明文字。              |
| `max_images`             | `20`      | 请求中保留图片的数量上限；`0` 移除所有图片。            |
| `image_retention_ratio`  | `0.6`     | 首次超限后保留的比例，向上取整；大于 `0` 且不超过 `1`。 |
| `max_image_bytes`        | `5242880` | **每张图片**的 base64 编码字节限制；`0` 禁用该限制。    |
| `max_image_dimension`    | `8000`    | 单边像素限制；`0` 禁用该限制。                          |
| `split_large_images`     | `true`    | 压缩前将较高的静态图片切成完整宽度的分段。              |
| `image_split_max_height` | `4096`    | 正整数分段高度，单位像素。                              |
| `image_split_overlap`    | `50`      | 非负重叠像素，必须小于分段高度。                        |

例如，第 21 张图片到来时，默认策略保留最新 12 张；之后允许保留窗口增长至 20 张，再移除下一批。两次回收之间，旧图片的替换文字保持稳定。选择 `image_retention_ratio: 1` 可改为始终保留最新 20 张。计数发生在切分后；默认 20 是请求大小策略，不是 provider 上限。

字节限制是每张图片 5 MiB 的 base64 数据，与原始文件大小及总请求大小分别计算。即使编辑器显示 MiB，也要保存精确字节。GIF 支持和上限均显式设置，不根据模型名称推断。默认值策略在序列化时省略，以保留旧捕获的 canonical bytes；显式 `null` 会保留。变换和历史保留方式见[请求级图片预处理](context.md#filters)。

## 模型请求亲和性

要让网关尽量将同一 Thread 的请求路由到一起，配置该网关识别的请求头名称：

```python
from a13n_harness import HarnessBuilder

builder = HarnessBuilder(session_affinity_header="x-litellm-session-id")
```

Harness 用 `AgentContext.thread_id` 派生的稳定 UUID v5 填充该请求头。省略选项或设为 `None` 即关闭网关亲和性。只发送选中的请求头；`x-session-id` 既不是额外自动发送的请求头，也不是通用网关标准。是否产生路由亲和性由网关决定。

续接保留相同值。具有不同 Thread ID 的根、子、同级和分叉执行得到不同值。可信 Host 可通过 `HarnessState.new(thread_id=...)` 和 `fork(thread_id=...)` 指定 ID。派生使用精确 ID 和固定命名空间，即使原 ID 已类似 UUID。结果为 36 字符、小写、带连字符的 UUID。

显式原生 `extra_headers` 以不区分大小写的方式优先。调用者设置及内部 ID、事件和遥测保持不变。这个传输标识传递亲和性，不承载认证。

同一图需要不同网关时，关闭 Builder 请求头，并用 `a13n_harness.models` 的 `RequestHeadersModel` 包装每个解析后的 Model。设置 `common_headers={header_name: derive_model_affinity_id(context.deps.thread_id)}`，从 `a13n_harness.model_affinity` 导入派生函数。每个子 Agent 使用当前解析上下文。Harness UI 将该选择保存在 `Model.model_configuration`，Service 保存在 Provider `config`。

提示缓存键是独立默认值。符合条件的 Model 会收到 `openai_prompt_cache_key=derive_model_affinity_id(thread_id)`，已有显式设置时则保留原值。判断使用最终解析的 `model_name`：以 `gpt-` 紧跟 ASCII 数字开头，前面可有且仅有一个 `openai/`。`gpt-4.1`、`gpt-5-codex` 和 `openai/gpt-5` 符合；`gpt-oss-120b`、`o3`、DeepSeek、自定义部署名、其他命名空间、不同大小写或前置空白不符合。逻辑选择字符串不参与判断。

Chat Completions 和 Responses 适配器可将其作为 `prompt_cache_key` 发送；兼容网关仍自行决定是否接受。两个默认行为可分别控制：

```python
from a13n_harness import HarnessBuilder

builder = HarnessBuilder(
    session_affinity_header="x-conversation-id",
    openai_prompt_cache_key_enabled=False,
)
```

显式缓存键布尔值优先，不再读取环境。否则默认 `true`，可用以下进程变量覆盖：

```bash
export A13N_HARNESS_MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED=false
```

接受 `1/true/yes/on` 和 `0/false/no/off`，不区分大小写，不允许首尾空白。被读取的环境值无效，或 Builder 覆盖不是布尔值时，构造失败。每个 Builder 为整个图及子 Agent 捕获一次请求头和缓存策略。禁用注入会保留显式设置，包括非 GPT Model 上的缓存键。

Codex 原生默认值独立于这些开关。在解析器中使用原始 ID 构造 `CodexRequestModel(..., thread_id=context.deps.thread_id)`。其 `session-id`、`thread-id` 和 `x-client-request-id` 使用相同 UUID 派生，与网关请求头无关；Harness 注入关闭时，它仍可提供自己的缓存键。

### 迁移已有连接

自动出站值现在使用派生 UUID，取代原始 Thread ID。已有 Thread 会切换一次，可能重置上游缓存或路由亲和性。本地历史无需迁移。显式原生值不变；Host 允许时，嵌入调用者可提供旧值。

旧版隐式 `x-session-id` 行为已移除。旧 `x_session_id_enabled` 参数不可用，对应环境开关也被忽略。需要保留该请求头时，在 Builder 中显式设置 `session_affinity_header="x-session-id"`，或设置各 Harness UI Model 的 `model_configuration.session_affinity_header`、Service Provider 的 `config.session_affinity_header`。

配置文件不会自动改写。Harness UI 和 Service 忽略旧全局开关，让每个连接管理自己的策略。编辑 Harness UI Model 影响新 Run 捕获，不改变已有组合捕获。

## 凭据与订阅身份验证

将 API key、认证客户端和账号选择保留在 Host 中。原生 Provider 提供 API key 机制；`a13n_harness.providers.model.oauth` 提供 Codex、ChatGPT、Grok 和 Copilot 的订阅组件。Codex 感知 Thread 的适配器为 `a13n_harness.models.codex.CodexRequestModel`。

新 Run 重新选择已认证 Model。State、工具元数据和 `AgentSpec` 都不是凭据存储。Host 负责用户交互、持久凭据，以及替换账号的许可。从[身份验证与 HTTP 客户端](model-authentication.md)开始，或使用 [Harness UI 登录流程](../a13n-harness-ui/models-and-authentication.md)。[身份验证契约](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/a13n-harness/16a-model-authentication.md)描述 SDK 与 Host 的边界。

## Thinking 签名恢复

Provider 明确拒绝历史 thinking 签名时，Harness 可以移除历史 thinking 和 redacted-thinking 部分，再重试当前请求一次。匹配错误包括 `` Invalid `signature` in `thinking` block ``。重试保持相同 Model 和设置，包括 thinking 配置。用户输入、assistant 正文、工具调用、已完成结果和其他内容均保留；工具不会再次执行。

恢复要求同时存在匹配的拒绝和可移除内容。首次请求成功则保留历史，第二次失败则抛出异常。流式恢复只适用于适配器交出 stream 之前：开头 SSE 错误即使发生在 HTTP 200 之后也可能符合；`message_start` 建立 stream 后的错误不能使用该重试，即使还没有正文。

修复直接修改内存消息，不会回滚。共享历史将清理结果带入后续导出；图片准备等独立请求投影则将修改留在当前请求中。Host 保存新 State 前，已有快照不变。应用自行管理该行为时，可用 `HarnessBuilder(self_healing_enabled=False)` 禁止自动安装。

## 请求设置与上下文策略

`AgentSpec.model_settings` 用于原生 provider 请求选项，`model_characteristics` 用于 Harness 上下文和输入准备。两者都应匹配实际端点：本地预算不能扩大 provider 容量，能力声明不能增加媒体支持。

继续阅读[上下文与工作状态](context.md)、[输入与输出](inputs-and-outputs.md)或[用量与限制](usage-and-limits.md)。

## 可复用 provider 定义

要让多个 Host 复用带类型的连接构造，实现 `a13n_harness.providers.model.ModelProviderDefinition`。其 `build()` 返回 Harness 可直接接受的原生 Model。内置项位于 `a13n_harness.providers.model.builtins`，通过 `ProviderCatalog` 组合和选择。

Host 保留持久化、授权和当前账号选择职责。API key 凭据使用 `{"api_key": "..."}`；AWS 和 Google 凭据保留结构化字段。订阅集成使用前面介绍的 OAuth 模块和 Codex 适配器。
