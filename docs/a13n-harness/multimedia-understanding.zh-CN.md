---
title: 多媒体理解
description: 通过模型原生输入或专用媒体理解 Agent，理解图像、视频和音频文件及 URL。
---

用 `view` 读取 Environment 中的媒体文件，用 `read_media_url` 读取直接 HTTP(S) 媒体资源。在请求中说明需要 OCR、转写、时间戳或 UI 审查等具体结果。

| 媒体能力配置                                     | 工具返回内容                      |
| ------------------------------------------------ | --------------------------------- |
| 当前 Model 声明了对应原生输入能力                | 将媒体附加为原生 `BinaryContent`  |
| 当前 Model 未声明该能力，已配置对应理解 provider | 专用媒体理解 Agent 的文字分析     |
| 两者均未配置                                     | `media_understanding_unavailable` |

Harness 按每种媒体的配置自动选择路径。先[声明原生能力](#declare-model-input-capabilities)或[配置理解 Agent](#configure-the-default-media-understanding-agents)，再使用下面的工具。

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

## 读取媒体 URL

对直接图像、视频或音频资源调用 `read_media_url(url, instructions=None, media_type=None)`。当前 Model 或已配置的理解 provider 支持至少一种媒体时，工具会自动出现。工具描述只列出可用类型；仅配置图像不会启用视频或音频。

下载会对源地址和每次重定向执行 Run 主机名策略，并限制为 120 秒总时限和有界字节数。响应 Content-Type 决定媒体类型；缺失或为通用二进制类型时，使用显式 `media_type` 或最终 URL 扩展名。HTML 页面会被拒绝。图像上限为 100 MiB，音频和视频为 16 MiB。原生视频下载还受当前 Model 的编码后预算约束（默认 10 MiB）；回退视频不受不支持该媒体的主模型预算影响。接收媒体的 Model 使用自身的请求预处理策略。

YouTube 需要显式声明原生 `url_input.video: [youtube]` 支持，且 Run 主机名策略不受限。它保留为原生 URL，不回退到辅助模型或下载。本地文件使用 `view`；URL 读取不需要 Environment 挂载或临时文件。

## 声明模型输入能力

在 `AgentSpec.model_characteristics` 中声明 Model 和 provider 调用路径支持的媒体类型：

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

三种能力相互独立。未声明的媒体类型使用对应的理解 provider。

## 配置默认的媒体理解 Agent

为需要回退处理的每种媒体，设置一个带 provider 前缀的 Pydantic AI 模型字符串：

```bash
export A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL=google:gemini-3.7-flash
export A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL=google:gemini-3.7-flash
export A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL=google:gemini-3.7-flash
```

通过 shell、容器运行时或应用提供这些进程环境变量。Harness 在需要回退时读取它们；如使用 `.env`，由应用负责加载。凭据通过所选 provider 的标准环境变量提供。

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

按应用需要配置媒体类型。缺少模型字符串只影响对应类型。仓库贡献者可使用 `.env.harness.example` 作为模板。

## 默认分析行为

默认图像分析覆盖可见内容、OCR、布局和风格。视频与音频分析按录制时间顺序展开，包括语音、屏幕内容或非语音声音。转写保留原语言，并标明不清楚的内容。使用 `instructions` 让任一工具聚焦所需细节。

`view` 接受最大 100 MiB 的图像源文件，以及最大 16 MiB 的音频或视频源文件；Environment provider 设置的更小限制仍然有效。E2B 的默认文件限制为 100 MiB，其他 provider 保留各自的默认值。默认图像预处理在读取后执行，将每张图像或切片限制在 5 MiB 的 base64 编码数据内，并将静态源图像处理限制在 8000 万像素以内。它修改模型请求，原生图像历史仍保留原始字节。音频和视频不使用这套图像压缩。

运行时会限制每次文件读取、媒体理解 Agent 的超时、输出大小、输出校验重试次数，以及临时 HTTP 错误的重试次数。视频的超时时间比图像或音频更长。受限读取完成后，Environment 的文件作用域就会释放，随后媒体理解 Agent 或自定义 provider 才进行外部模型调用。

## 覆盖单次执行的配置

更复杂的集成可以在某次 Run 中替换环境变量配置的默认实现：

```python
from a13n_harness import RunBindings
from a13n_harness.metering import ModelUsageBinding
from a13n_harness.toolsets import (
    MediaUnderstandingRequest,
    MediaUnderstandingResult,
)


class CustomMediaUnderstandingProvider:
    def supports(self, kind: str) -> bool:
        return kind in {"image", "video", "audio"}

    async def understand(
        self,
        request: MediaUnderstandingRequest,
        *,
        usage: ModelUsageBinding | None = None,
    ) -> MediaUnderstandingResult:
        text, usage = await analyze_with_custom_model(request)
        return MediaUnderstandingResult(text=text, usage=usage)


bindings = RunBindings.embedded(
    file_media_understanding=CustomMediaUnderstandingProvider(),
)
```

`supports(kind)` 报告已配置类型，不解析凭据或发出请求。已有仅实现 understand 的自定义 provider 仍可用于 `view`；实现此可选发现方法后即可启用 URL 回退。

为当前 Run 绑定的自定义 provider 服务于两个工具，优先于所有默认环境模型字符串。可以用它接入 Host 管理的模型解析器、自定义凭据、已有 provider 客户端，或专门的分析逻辑。它不会改变模型输入能力声明。

`AgentMediaUnderstandingProvider` 也是公开接口，可直接通过代码传入明确的 Pydantic AI `Model` 实例或模型字符串。如果 Host 根据自身的模型 ID 解析出了 `Model` 实例，应通过 `model_ids` 为每种媒体传入该 ID。该媒体类型的每个理解请求都会将该 ID 用作 `ModelCall.model_id`，以及定价输入中的选定模型 ID。Host 因此能以该模型对请求进行准入检查、用量归属和定价。

## 用量与失败

媒体理解 Agent 和自定义 provider 的用量会独立记录，包含：

- 文件的来源为 `files.media_understanding`，工具 ID 为 `filesystem.view`；
- URL 的来源为 `media.url_understanding`，稳定工具 ID 为 `media.read_video_url`；
- 当前工具调用 ID；
- provider、模型（`model_name`；自定义 provider 的 `ProviderUsage` 中为 `product`）、请求次数，以及可获得的 token 计数。

这些嵌套模型调用保留独立的 Pydantic AI 计数，同时计入调用方 Agent 的 Context 用量快照和模型预算。即使分析失败、超时或取消，已经确认的观测也会保留。如果模型调用已发出，但在收到响应之前失败，会将用量记录为不可获得，不会编造 token 或费用；发出前被拒绝则不记录请求。

遇到问题时，两个工具都返回稳定、大小受限的失败结果，不会附加不受支持的媒体，也不会直接暴露 provider 异常。每种失败都是调用方 Agent 可见的普通工具结果，并非 Pydantic AI 重试提示，因此不会消耗调用方 Agent 的函数工具重试额度。常见错误码包括：

| 错误码                                      | 含义                                                                         |
| ------------------------------------------- | ---------------------------------------------------------------------------- |
| `media_understanding_unavailable`           | 当前模型没有原生支持，且该媒体类型未配置回退模型或自定义 provider。          |
| `media_understanding_configuration_invalid` | 配置的设置值不是有效且大小受限的 JSON 对象。                                 |
| `media_understanding_timeout`               | 专用分析超过了对应媒体类型的超时限制。                                       |
| `media_understanding_response_invalid`      | 自定义媒体理解 provider 或理解模型返回的内容为空，或不符合有效性与大小限制。 |
| `media_understanding_failed`                | 自定义媒体理解 provider 或理解模型失败，且没有更具体、可安全返回的错误码。   |

有关文件路由、授权和字节数限制，参阅[环境](environments.md)。

## 从 `read_video_url` 迁移

新工具替代 `read_video_url`，Python 接口为 `MediaUrlToolset`。权限规则继续使用稳定 ID `media.read_video_url`，现在控制全部三种媒体类型。升级前应完成或关闭旧名称的待处理工具调用，因为这些调用无法在重命名后的工具面上恢复。
