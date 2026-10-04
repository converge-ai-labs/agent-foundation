---
title: 多媒体理解
description: 通过模型原生输入或专用媒体理解 Agent，理解图像、视频和音频文件。
---

Harness 提供内置方式来理解 Environment 中的图像、视频和音频文件。面向模型的 `view` 工具会自动选择以下路径之一：

1. 当前 Agent 的 `model_characteristics` 声明了对应的模型输入能力时，将文件作为原生 `BinaryContent` 附加到输入中；
2. 否则，运行专用的媒体理解 Agent，返回文字分析。

调用模型不会自行选择路径，Harness 也不会根据模型名称或 Pydantic AI 的 `Model.profile` 猜测是否支持某种媒体。

## 声明模型输入能力

原生媒体支持由 Harness 的 `AgentSpec` 配置管理。调用方使用 `model_characteristics` 构建参数，Python 代码通过 `spec.model_characteristics` 读取解析后的值：

```python
from a13n_harness import (
    AgentSpec,
    ModelCapability,
    HarnessModelCharacteristics,
)

agent_spec = AgentSpec(
    model_characteristics=HarnessModelCharacteristics(
        capabilities=frozenset(
            {
                ModelCapability.IMAGE_UNDERSTANDING,
                ModelCapability.VIDEO_UNDERSTANDING,
                ModelCapability.AUDIO_UNDERSTANDING,
            }
        )
    )
)
```

只声明当前模型和 provider 调用路径实际支持的模型输入能力。三种能力相互独立。未声明某个值时，`view` 会采用保守策略，使用专用媒体理解 Agent，而不是附加模型不支持的内容。

## 配置默认的媒体理解 Agent

为需要回退处理的每种媒体，设置一个带 provider 前缀的 Pydantic AI 模型字符串：

```bash
export A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL=google:gemini-3.7-flash
export A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL=google:gemini-3.7-flash
export A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL=google:gemini-3.7-flash
```

只有当 `view` 需要回退时，Harness 才读取这些进程环境变量。它不会加载 `.env`；可执行程序、shell、容器运行时或进程管理器可以通过常规方式提供变量。Provider 凭据仍使用选定 Pydantic AI provider 的标准环境变量。

每个媒体理解 Agent 的初始配置为 `temperature=0.1`。可以通过大小受限的 JSON 对象覆盖或扩展原生 `ModelSettings`：

```bash
export A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL_SETTINGS='{"temperature":0.2}'
export A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL_SETTINGS='{"temperature":0.1,"max_tokens":8192}'
export A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL_SETTINGS='{"temperature":0.1}'
```

完整变量名如下：

| 媒体 | 模型字符串                               | 可选的设置 JSON                                   |
| ---- | ---------------------------------------- | ------------------------------------------------- |
| 图像 | `A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL` | `A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL_SETTINGS` |
| 视频 | `A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL` | `A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL_SETTINGS` |
| 音频 | `A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL` | `A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL_SETTINGS` |

可以配置其中一种、两种或全部三种媒体。缺少模型字符串只影响对应媒体类型。仓库贡献者可以将根目录 `.env.harness.example` 模板中的这些条目复制到 `.env`；Harness 本身仍只读取最终传入进程的环境变量。

## 默认分析行为

专用媒体理解 Agent 使用随包提供、按媒体类型设计的生产提示词：

- 图像分析覆盖所有可见元素、准确的 OCR、布局、视觉风格，以及可用于实现的 UI/CSS 细节；
- 视频分析按完整时间顺序进行，捕捉语音和屏幕内容，谨慎区分说话者，并且只在内容确实表达了用户意图时提取它；
- 音频分析按录音时间顺序进行，分别描述语音、音乐、音效、环境声和音质。

这三种提示词都优先保证准确性，避免猜测；转写保留原语言，明确标注无法辨认或听清的内容，不擅自推断未说明的身份。`view(..., instructions=...)` 可以缩小分析范围，但这些防止幻觉的规则仍然有效。如果没有明确的分析指令，媒体理解 Agent 会使用对应媒体的全面分析默认值，并说明不清楚或未覆盖的内容。

运行时会限制每次文件读取、媒体理解 Agent 的超时、输出大小、输出校验重试次数，以及临时 HTTP 错误的重试次数。视频的超时时间比图像或音频更长。受限读取完成后，Environment 的文件作用域就会释放，随后媒体理解 Agent 或自定义 provider 才进行外部模型调用。

## 通过 `view` 使用

通过 `DynamicEnvironmentCapability` 启用 Environment 文件工具，之后模型即可调用 `view`。查看文本需要文本读取权限；查看媒体则需要在同一个选定挂载点上同时拥有 stat 和字节读取权限，不同挂载点上的权限不能拼在一起来授权媒体读取：

```python
from a13n_harness.environment import (
    DynamicEnvironmentCapability,
    DynamicEnvironmentConfiguration,
)

capabilities = (
    DynamicEnvironmentCapability(DynamicEnvironmentConfiguration()),
)
```

同一个工具接受文本和受支持的媒体路径。查看媒体时，`instructions` 应明确所需的分析范围，例如 OCR、说话者转写、时间戳、UI 审查，或某个需要核实的局部区域。

## 覆盖单次执行的配置

更复杂的集成可以在某次 Run 中替换环境变量配置的默认实现：

```python
from a13n_harness import RunBindings
from a13n_harness.toolsets import (
    MediaUnderstandingRequest,
    MediaUnderstandingResult,
)


class CustomMediaUnderstandingProvider:
    async def understand(
        self,
        request: MediaUnderstandingRequest,
    ) -> MediaUnderstandingResult:
        text, usage = await analyze_with_custom_model(request)
        return MediaUnderstandingResult(text=text, usage=usage)


bindings = RunBindings.embedded(
    file_media_understanding=CustomMediaUnderstandingProvider(),
)
```

为当前 Run 绑定的自定义 provider 优先于所有默认环境模型字符串。可以用它接入 Host 管理的模型解析器、自定义凭据、已有 provider 客户端，或专门的分析逻辑。它不会改变模型输入能力声明。

`AgentMediaUnderstandingProvider` 也是公开接口，可直接通过代码传入明确的 Pydantic AI `Model` 实例或模型字符串。如果 Host 根据自身的模型 ID 解析出了 `Model` 实例，应通过 `model_ids` 为每种媒体传入该 ID。该媒体类型的每个理解请求都会将该 ID 用作 `ModelCall.model_id`，以及定价输入中的选定模型 ID。Host 因此能以该模型对请求进行准入检查、用量归属和定价。

## 用量与失败

媒体理解 Agent 和自定义 provider 的用量会独立记录，包含：

- 来源 `files.media_understanding`；
- 工具 ID `filesystem.view`；
- 当前工具调用 ID；
- provider、模型（`model_name`；自定义 provider 的 `ProviderUsage` 中为 `product`）、请求次数，以及可获得的 token 计数。

这些嵌套模型调用保留独立的 Pydantic AI 计数，同时计入调用方 Agent 的 Context 用量快照和模型预算。即使分析失败、超时或取消，已经确认的观测也会保留。如果模型调用已发出，但在收到响应之前失败，会将用量记录为不可获得，不会编造 token 或费用；发出前被拒绝则不记录请求。

遇到问题时，`view` 返回稳定、大小受限的失败结果，不会附加不受支持的媒体，也不会直接暴露 provider 异常。每种失败都是调用方 Agent 可见的普通工具结果，并非 Pydantic AI 重试提示，因此不会消耗调用方 Agent 的函数工具重试额度。常见错误码包括：

| 错误码                                      | 含义                                                                         |
| ------------------------------------------- | ---------------------------------------------------------------------------- |
| `media_understanding_unavailable`           | 当前模型没有原生支持，且该媒体类型未配置回退模型或自定义 provider。          |
| `media_understanding_configuration_invalid` | 配置的设置值不是有效且大小受限的 JSON 对象。                                 |
| `media_understanding_timeout`               | 专用分析超过了对应媒体类型的超时限制。                                       |
| `media_understanding_response_invalid`      | 自定义媒体理解 provider 或理解模型返回的内容为空，或不符合有效性与大小限制。 |
| `media_understanding_failed`                | 自定义媒体理解 provider 或理解模型失败，且没有更具体、可安全返回的错误码。   |

有关文件路由、授权和字节数限制，参阅[环境](environments.md)。
