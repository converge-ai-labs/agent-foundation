---
title: 模型
description: 选择模型来源、逐次执行路由，声明上下文特性和请求亲和性。
---

使用原生 Pydantic AI Model 或模型字符串。Harness 添加逐次执行路由、显式上下文特性和基于线程的请求亲和性；provider 适配器仍负责线上协议和支持的设置。

## 选择模型来源

| 场景                               | 使用方式                                                                  |
| ---------------------------------- | ------------------------------------------------------------------------- |
| 固定 provider 路由                 | `AgentSpec(model="provider:model")`                                       |
| 已构建客户端、自定义传输或离线测试 | `HarnessBuilder.build(..., model=native_model)`，不设置 `AgentSpec.model` |
| 用户/租户专属路由或短期凭据        | 新 `RunBindings.model_resolver`                                           |
| 共享静态网关配置                   | Builder 的 `gateway_provider_factory`                                     |

这些示例扩展[离线快速入门](getting-started.md)。`provider_factory`、`apply_provider_profile` 和 `gateway_provider_factory` 等名称代表应用自己管理的协作对象，不是内置服务。

## 模型选择

只使用一个模型来源。在 `AgentSpec.model` 中放入原生或 Host 逻辑字符串：

```python
executable = HarnessBuilder().build(
    AgentSpec(model="openai-responses:gpt-5"),
    output_type=str,
)
```

通过 `model=` 传入具体 Pydantic AI Model，保持 `AgentSpec.model` 未设置。可以自行构建，也可以使用可选 Harness helper：

```python
from a13n_harness import (
    HarnessBuilder,
    infer_model,
)

model = infer_model(
    "openai-responses:gpt-5",
    provider_factory=provider_factory,
    patches=(apply_provider_profile,),
)
executable = HarnessBuilder().build(
    AgentSpec(system_prompt="Answer concisely."),
    output_type=str,
    model=model,
)
```

`infer_model()` 始终返回原生 Pydantic AI Model。它将单独的 `openai:` 映射到现代 `openai-responses:` provider，接受旧 Google Cloud 前缀，按顺序应用同步模型补丁，还可添加调用者选择的静态公共请求头，不覆盖逐请求 `ModelSettings.extra_headers`。也可跳过它，直接传入任意原生 Model。

没有执行模型解析器时，`HarnessBuilder` 为 `AgentSpec.model` 中的每个字符串使用同一 helper。字面形式 `gateway@provider:model` 选择 Pydantic AI 公开 Gateway Provider，使用标准 `PYDANTIC_AI_GATEWAY_API_KEY` 和可选 `PYDANTIC_AI_GATEWAY_BASE_URL` 配置：

```python
executable = HarnessBuilder().build(
    AgentSpec(model="gateway@openai:gpt-5"),
    output_type=str,
)
```

命名自定义网关使用一个 builder 级工厂：

```python
executable = HarnessBuilder(
    gateway_provider_factory=gateway_provider_factory,
).build(
    AgentSpec(model="company@openai:gpt-5"),
    output_type=str,
)
```

工厂接收 `(gateway_name, provider_name)`，返回 Pydantic AI `Provider`。它负责凭据、provider SDK 配置、重试、HTTP 客户端及其生命周期。同一 builder 工厂也用于递归构建的 subagent。路由授权、凭据或策略逐次执行变化时，改用新 `RunBindings.model_resolver`。

对于直接 provider 的模型信息，包内包含小型不可变官方目录：

```python
from a13n_harness.model_catalog import get_official_model_catalog

models = get_official_model_catalog()
characteristics = models["anthropic:claude-sonnet-5"].characteristics
```

条目只含带 provider 前缀的官方模型 ID、客观 `HarnessModelCharacteristics` 和官方来源 URL。不含网关路由、凭据、请求预设、推理设置、别名、标签或应用默认值。查找必须显式执行；`HarnessBuilder` 不会悄悄应用目录特性。

逐次执行路由、凭据或租户策略通过 `RunBindings.model_resolver` 传入异步函数或异步可调用对象。它接收 Pydantic `ModelResolutionContext` 和字符串选择，返回原生 Model。无需 Harness 基类。解析器可用当前 Host 管理的工厂和补丁调用 Harness `infer_model()`，或返回自行构建的 Model。

## 出站 HTTP 代理

使用 `create_model_http_client()` 的模型路由，包括 Harness UI 共享 API key 路由，遵循标准 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY` 环境变量及小写形式。在启动 Host 的进程中设置，无需模型或 UI 配置字段：

```bash
export http_proxy=http://127.0.0.1:8888
export https_proxy=http://127.0.0.1:8888
export no_proxy=localhost,127.0.0.1,::1
a13n-harness-ui
```

HTTP 代理 URL 也可用于 HTTPS 目标：客户端使用 CONNECT 隧道。代理选择和绕过匹配遵循 `httpx2`；直连与代理请求应用相同有界 HTTP 重试策略。显式提供 `transport` 时使用其自身路由，不采用环境代理；`retry=None` 禁用重试，但不禁用代理发现。

provider 端点验证仍适用，包括需要时的本地 DNS 检查。不使用此 helper 的 SDK 传输保留自己的代理行为。

## 出站 TLS 验证

Harness、Harness UI 和 Service 构造的 HTTP 客户端默认验证 HTTPS 目标的证书和主机名。在受控开发环境或拦截代理环境中，运维人员可在启动 Host 前显式关闭验证：

```bash
export A13N_OUTBOUND_TLS_VERIFY=false
a13n-harness-ui
# Or start each Service control/worker process with the same environment.
```

删除该变量或设为 `true` 即可保持验证。仅接受 `true` 和 `false`，忽略大小写及首尾空白；其他值会使客户端构造失败，Service 也会在启动时拒绝。修改环境后重启进程：已有客户端保留构造时选择的策略。这是进程输入，不是 Model、Agent、Provider、Run、YAML 或 TOML 设置。

开关覆盖 Harness、Harness UI 和 Service 自行构造的客户端，包括 Model 请求（含适配器自建的 Bedrock Converse）、Web/媒体、远程 MCP、自有 OAuth 交换、管理请求、原生 HTTP Environment 操作及 HTTP Envd 连接。直连、代理和 `NO_PROXY` 路径上的目标 TLS 都受其控制。显式传入的客户端、传输和 CA 上下文保留自身策略。依赖内部构造的 SDK 客户端（包括无法检查内部传输的推断 Model 路由）、数据库/对象存储/遥测 SDK、HTTPS 代理节点自身的 TLS，以及独立 Envd daemon/broker 进程保留各自的 TLS 配置。

> [!WARNING]
> `false` 会移除服务器身份验证，凭据和内容可能被截获。它不会把 HTTPS 变成明文，也不会关闭身份验证、主机白名单、凭据限制、重定向检查、时间/字节上限或重试规则。生产环境应优先配置可信 CA 证书。

## 模型编写别名

编写界面需要简短、明确的名称，而其他位置都保留具体值时，使用两个平行解析器。上下文预算解析为 Harness `HarnessModelCharacteristics`；provider 请求选项独立解析为原生 `ModelSettings`：

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

内置特性别名如下：

- `anthropic:context-200k`：Harness 上下文预算为 `200_000` token；
- `anthropic:context-400k`：Harness 上下文预算为 `400_000` token；
- `anthropic:context-1m`：Harness 上下文预算为 `1_000_000` token。

这些值只控制 Harness 生命周期阈值，不选择 provider 上下文变体、添加 beta 请求头、更改请求设置或扩大模型实际上下文窗口。

内置设置别名如下：

- `anthropic:interleaved-thinking`：选择 Anthropic adaptive thinking，不强制 effort、token 上限、缓存行为、beta 请求头或上下文管理；
- `anthropic:thinking-disabled`：禁用 thinking，并移除从前一个别名继承的 effort；
- `anthropic:max-output-32k`、`anthropic:max-output-64k` 和 `anthropic:max-output-128k`：分别将 `max_tokens` 设为 `32_768`、`65_536` 和 `131_072`。

max-output 别名是显式请求上限，不承诺兼容性。所选模型和 provider 仍验证是否支持该请求。

每个解析器内，别名按声明顺序应用，具体覆盖最后应用。别名解析要求带 provider 前缀的直接或网关模型字符串，未知或不兼容别名立即失败。没有别名或覆盖提供特性时，`resolve_model_characteristics()` 返回 `None`；`resolve_model_settings()` 返回普通、独立的原生设置字典。Host 可通过不可变自定义目录添加私有选项，但必须在持久保存自己的定义修订前解析每个别名。`AgentSpec`、`HarnessBuilder`、worker 和 Harness 状态都不包含别名名称。

## 模型特性

构建和序列化键 `model_characteristics` 保存解析后的 Harness 管理特性，不是 provider 请求设置，也不是第二份 provider profile。Python 通过 `spec.model_characteristics` 读取，不与 Pydantic 类级 `model_config` 冲突。它定义显式模型输入能力、上下文窗口，以及主动总结和压缩比例。显式 Harness 上下文窗口也会反映到生效的原生 `ModelProfile`，使外部 Pydantic AI Capability 可通过上游模型抽象读取相同值：

```python
spec = AgentSpec(
    model="logical:support",
    model_characteristics=HarnessModelCharacteristics(
        context_window_tokens=200_000,
        proactive_context_management_threshold=0.65,
        compact_threshold=0.90,
    ),
)
```

选择 `HandoffCapability()` 时，它在 65% 处触发总结提醒。`CompactionCapability()` 每次请求根据原生 `RunContext.model.context_window` 和 `RunContext.context_window_used` 检查 90% 阈值，原生值不可用时回退到 Harness 特性和捕获的 provider 用量。显式 Capability token 阈值优先。模型特性本身不会启用任何 Capability。Host 可从自身预设目录、Harness 官方模型目录或特性别名解析这些值；`HarnessBuilder` 绝不从模型名称推断。无需该扩展时，仍可使用原生 Pydantic AI `AgentSpec`。

### 图片输入策略

`HarnessModelCharacteristics.image_input` 使用共享且冻结的 `ImageInputPolicy`，可从 `a13n_harness` 导入。它只控制所选模型的请求预处理；父 Agent 的模型、上下文预算及原生 `ModelSettings` 都不会改变它。省略时启用默认策略，部分对象为未指定成员采用默认值，显式 `null` 禁用自动预处理。显式 `ImageFilterCapability` 保留自己的策略，不会重复安装自动实例。

| 参数                     | 默认值    | 含义                                                 |
| ------------------------ | --------- | ---------------------------------------------------- |
| `support_gif`            | `true`    | 允许二进制 GIF；`false` 时替换为说明文字。           |
| `max_images`             | `20`      | 保留请求中最新的图片；`0` 移除所有图片。             |
| `max_image_bytes`        | `5242880` | **每张图片**的 base64 编码字节限制；`0` 禁用该限制。 |
| `max_image_dimension`    | `8000`    | 单边像素限制；`0` 禁用该限制。                       |
| `split_large_images`     | `true`    | 压缩前将较高的静态图片切成完整宽度的分段。           |
| `image_split_max_height` | `4096`    | 正整数分段高度，单位像素。                           |
| `image_split_overlap`    | `50`      | 非负重叠像素，必须小于分段高度。                     |

默认字节限制是 5 MiB（`5 * 1024 * 1024`），不是原始文件大小或整个请求预算。即使表单以 MiB 显示，Host 也存储精确字节。不会从模型名称猜测 GIF 支持或限制。默认值策略在序列化时省略，以保持旧模型捕获的 canonical bytes；显式 `null` 始终保留。变换和历史保留行为见[请求级图片预处理](context.md#filters)。

## 模型请求亲和性

网关亲和性需要**主动启用**。配置**请求头名称** ，不要配置固定会话值：

```python
builder = HarnessBuilder(session_affinity_header="x-litellm-session-id")
```

Harness 在该请求头中发送从当前 `AgentContext.thread_id` 派生的稳定 UUID v5，不发送原始线程 ID。省略选项（或使用 `None`）保持网关亲和性禁用。选择网关配置识别的名称；`x-session-id` 不是通用标准。Harness 只发送配置的请求头，不会同时发送 `x-session-id`。发送请求头表示请求亲和性，不保证 provider 固定路由。

同一 `HarnessState` 续接期间值保持稳定；独立根、子、同级和分叉执行使用不同值。可信 Host 可通过 `HarnessState.new(thread_id=...)` 选择源线程 ID，`HarnessState.fork(thread_id=...)` 创建独立的 Host 指定分支。Harness 不使用临时执行 ID，也不修改调用者设置。嵌入 SDK 中，显式原生 `extra_headers` 仍以不区分大小写的方式优先。

同一 Agent 图中的不同连接可从 `a13n_harness.model_affinity` 导入 `derive_model_affinity_id`，在 `RunModelResolver` 中通过 `RequestHeadersModel(model, common_headers={header_name: derive_model_affinity_id(context.deps.thread_id)})` 绑定各模型；保持 Builder 选项禁用。始终使用当前解析上下文，不捕获父级 ID。Harness UI 在 `Model.model_configuration` 中管理此项；Service 在 Provider 的 `config` 中管理。

OpenAI 提示缓存键独立控制：符合条件的模型默认接收 `openai_prompt_cache_key=derive_model_affinity_id(thread_id)`，使用与网关亲和性相同的派生值。显式缓存设置优先，不进行转换。

共享派生使用固定命名空间和精确线程 ID，即使线程 ID 本身已是 UUID。它生成 36 字符、小写、带连字符的 UUID，不持久保存映射、不添加状态字段，也不依赖执行、模型、时钟或机器。内部 ID、事件和遥测保持不变。这是有界线上格式，不是身份验证 token，也不保证所有网关接受。

缓存键资格使用最终解析 Model 的 `model_name`，不使用 Host 逻辑模型字符串。名称必须以 `gpt-` 开头，后面紧接 ASCII 数字，可加恰好一个 `openai/` 前缀。因此 `gpt-4.1`、`gpt-5-codex` 和 `openai/gpt-5` 符合条件；DeepSeek、`gpt-oss-120b`、`o3` 和自定义部署名称不符合。匹配区分大小写，不裁剪空白或移除任意命名空间。Chat Completions 和 Responses 适配器都可将设置作为 `prompt_cache_key` 发送。命名策略不保证兼容网关接受该字段。

Host 构建 builder 时可独立控制默认值：

```python
builder = HarnessBuilder(
    session_affinity_header="x-conversation-id",
    openai_prompt_cache_key_enabled=False,
)
```

旧 Builder 选项 `x_session_id_enabled` 已移除，对应环境开关被忽略。请改为显式 `session_affinity_header="x-session-id"`。独立缓存键开关仍默认 **true**；显式布尔值 `openai_prompt_cache_key_enabled` 覆盖环境值，且不读取环境变量：

```bash
export A13N_HARNESS_MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED=false
```

环境值接受 `1/true/yes/on` 或 `0/false/no/off`，不区分大小写；含首尾空白的值无效。被读取的环境值无效，或 Builder 覆盖不是布尔值时，构建失败。每个 builder 为整个执行图（含子 agent）只记录一次请求头名称和缓存策略，因此环境变化不影响已有 builder 或执行对象。启用缓存键开关只启用 GPT 命名规则，不强制向其他模型注入。禁用补丁不改变任何显式设置，包括非 GPT 模型上的缓存键。

这些开关只控制 Harness 默认值，不控制 provider 原生行为。在解析器中显式绑定 `CodexRequestModel(..., thread_id=context.deps.thread_id)`：其原生 `session-id`、`thread-id` 和 `x-client-request-id` 独立于网关请求头，使用相同 UUID 派生。向适配器传入原始线程 ID，不是已派生 UUID。禁用 Harness 注入时，Codex 适配器仍可提供自己的缓存键。

### 迁移已有连接

主动启用的网关请求头、自动提示缓存键和绑定 Codex 会话默认值使用派生 UUID，而非原始线程 ID。已有线程的出站值切换一次，可能重置上游缓存或路由亲和性。本地历史不变，无需状态迁移。显式原生值保持不变；嵌入调用者若需要旧线上值，可以显式提供，但须符合 Host 验证策略。

旧版本向所有模型隐式发送 `x-session-id`。该默认值已移除，包括省略新字段的旧配置文件。要保留它，在嵌入 Builder 中选择 `session_affinity_header="x-session-id"`，为各 Harness UI Model 添加 `model_configuration.session_affinity_header: x-session-id`，或在 Service provider 上设置 `config.session_affinity_header`。文件不会自动改写。Harness UI 和 Service 有意忽略旧全局请求头开关，防止一个连接的策略泄露到另一个。Harness UI 不会改写已捕获的执行组合；新执行会捕获编辑后的模型。

## 凭据与订阅身份验证

使用 API key 的模型遵循原生 provider 凭据机制。秘密和客户端生命周期放在应用代码中，不放入 `HarnessState`、元数据或 `AgentSpec`。模型字符串本身不能证明访问权限。

`a13n_harness.providers.model.oauth` 模块提供 SDK 级订阅组件：Codex 浏览器/设备登录流程；ChatGPT 登录与凭据类型；Grok 凭据来源与 OAuth/设备流程类型，以及 `build_grok_model`；GitHub Copilot 凭据类型，以及 `build_copilot_model`。Codex 模型适配器是 `a13n_harness.models.codex.CodexRequestModel`。Host 负责用户交互、账号存储、持久化和替换账号的许可。新执行重建已验证模型，不把导出的续接状态当作已保存客户端。

直接可用的本地登录体验见 [Harness UI 身份验证](../a13n-harness-ui/models-and-authentication.md)。SDK 集成可先参考[身份验证与 HTTP 客户端示例](model-authentication.md)，再查看[模型身份验证契约](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/a13n-harness/16a-model-authentication.md)和 `a13n_harness.providers.model.oauth` 的公开类型；Harness 不提供产品账号数据库。

## 请求设置与上下文策略

`AgentSpec.model_settings` 包含原生 provider 请求设置。`model_characteristics` 描述显式本地上下文和输入策略。更大本地预算不会提高 provider 上限，声明支持图像也不会使端点接受图像。

继续阅读[上下文与工作状态](context.md)、[输入与输出](inputs-and-outputs.md)或[用量与限制](usage-and-limits.md)。

## 可复用 provider 定义

使用 `a13n_harness.providers.model.ModelProviderDefinition` 提供带类型连接和原生 SDK 构造器。`build()` 结果是原生 Pydantic AI Model，可直接传给 Harness。内置项位于 `a13n_harness.providers.model.builtins`。可选模型 OAuth 流程位于 `a13n_harness.providers.model.oauth`，感知线程的 Codex 适配器为 `a13n_harness.models.codex.CodexRequestModel`。

Host 通过代码组合模型 provider 定义，经 `ProviderCatalog` 选择。Host 负责持久化、授权和当前账号选择。API key 凭据对象使用 `{"api_key": "..."}`；AWS 和 Google 凭据保留结构化字段。
