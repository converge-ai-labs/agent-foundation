---
title: 多媒体理解
description: 读取本地媒体或直接 URL，选择原生输入或分析 Model，并处理返回的文本和用量。
---

用 `view` 读取 Environment 中的媒体，用 `read_media_url` 读取直接 HTTP(S) 图像、视频或音频资源。为工具指定明确任务，例如读取标签、按说话人转写、定位事件时间戳或审查 UI。

Harness 按每种媒体分别选择处理路径：

| 配置                                | 交给调用方 Model 的结果                         |
| ----------------------------------- | ----------------------------------------------- |
| 声明了对应的原生输入能力            | 以 `BinaryContent` 附加原始媒体                 |
| 没有对应声明，但配置了理解 provider | 独立媒体理解 Agent 或自定义 provider 返回的文本 |
| 两者都不可用                        | `media_understanding_unavailable`               |

声明描述的是 Model 及其 provider 调用路径已经具备的能力，不会让纯文本 Model 获得感知能力。原生请求失败也不会切换为辅助推理。

## 通过 `view` 使用

先在本地验证从文件到分析的完整路径。示例创建一段很短的静音 WAV，挂载临时目录，通过 Agent 调用 `view`，再将字节发给第二个确定性 Model。它只需要[快速开始](getting-started.md)中的 Python 环境，不需要 API key，也不发出网络请求。保存为 `media_demo.py`，在仓库根目录运行 `uv run --locked python media_demo.py`。

```python
import asyncio
import wave
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.environment import DynamicEnvironmentCapability, DynamicEnvironmentConfiguration
from a13n_harness.environment.execution import EnvironmentConnector
from a13n_harness.environment.models import EnvironmentDescriptor, EnvironmentState
from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL
from a13n_harness.toolsets import AgentMediaUnderstandingProvider
from pydantic_ai.messages import (
    BinaryContent, ModelRequest, ModelResponse, TextPart, ToolReturnPart, UserPromptPart,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel


@dataclass(frozen=True)
class PreparedSource:
    connector: EnvironmentConnector

    @property
    def provider_key(self) -> str:
        return self.connector.provider_key

    @property
    def environment_id(self) -> str:
        return self.connector.environment_id

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return self.connector.descriptor

    @property
    def state(self) -> EnvironmentState | None:
        return self.connector.state

    async def ensure_ready(self) -> EnvironmentConnector:
        return self.connector


def analyze(messages, info):
    media = [
        item for message in messages if isinstance(message, ModelRequest)
        for part in message.parts if isinstance(part, UserPromptPart)
        and isinstance(part.content, list)
        for item in part.content if isinstance(item, BinaryContent)
    ]
    assert len(media) == 1 and media[0].data.startswith(b"RIFF")
    return ModelResponse(parts=[TextPart("The fixture audio reached the analysis Model.")])


async def respond(messages, info):
    returns = [
        part for message in messages if isinstance(message, ModelRequest)
        for part in message.parts if isinstance(part, ToolReturnPart)
    ]
    if not returns:
        assert "view" in {tool.name for tool in info.function_tools}
        yield {0: DeltaToolCall(
            name="view",
            json_args='{"file_path":"/workspace/sample.wav","instructions":"Describe the audio."}',
        )}
    else:
        assert "fixture audio reached" in str(returns[-1].content)
        yield "Audio fallback completed."


async def main():
    provider = AgentMediaUnderstandingProvider(models={"audio": FunctionModel(analyze)})
    agent = HarnessBuilder().build(
        AgentSpec(), output_type=str,
        model=FunctionModel(stream_function=respond),
        capabilities=(DynamicEnvironmentCapability(DynamicEnvironmentConfiguration()),),
    )
    with TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        with wave.open(str(root / "sample.wav"), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\x00\x00" * 160)
        connector = DIRECT_LOCAL.execution_connector(
            {"root": {"path": str(root)}}, environment_id="env-media",
        )
        result = await agent.run(
            "Describe sample.wav.", environment=PreparedSource(connector),
            bindings=RunBindings.embedded(file_media_understanding=provider),
        )
        print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

预期输出为 `Audio fallback completed.`。分析回调检查 WAV 字节已到达，然后返回固定文本；这里验证集成，不验证语音识别。将回调对应的 `FunctionModel` 替换为已认证、支持音频的 Model，即可进行真实分析。

`DynamicEnvironmentCapability` 提供文件工具，`PreparedSource` 将已创建的本地 connector 接入运行时。已有应用可以复用其 [Environment 绑定](environments.md)。查看文本需要文本读取权限；查看媒体需要同一选定挂载上的 stat 和字节读取权限，不能组合不同挂载的权限。

通过 `instructions` 说明所需输出，例如“按说话人转写，并标记听不清的词”。相同指令会随原生输入附加，或传给回退 provider。

## 读取媒体 URL

当前 Model 或已配置 provider 支持某种媒体时，`read_media_url(url, instructions=None, media_type=None)` 会自动出现，工具描述列出可用类型。示例中的 provider 同时启用音频 URL 回退和本地音频查看。

1. 取得直接媒体 URL，而不是包含播放器或图片的 HTML 页面。
2. 请求 Agent 使用该 URL 和明确指令调用 `read_media_url`。仅粘贴链接时，链接在工具调用前仍是文本。
3. 响应缺少有效 Content-Type 且最终 URL 没有可识别扩展名时，提供 `media_type`，例如 `audio/wav`。
4. 检查分析结果或错误，再决定是否发出下一次请求。

响应 Content-Type 优先；缺失或为通用二进制类型时，使用显式 MIME 或最终 URL 扩展名。HTML 和空响应体会被拒绝。下载会对源地址和每次重定向检查 Run 主机名策略，最多跟随五次重定向，总时限为 120 秒。图像上限为 100 MiB，音频和视频为 16 MiB。原生视频还需满足当前 Model 的编码后预算，默认 10 MiB。回退视频独立使用源文件上限，不受不支持该媒体的主 Model 预算限制。

URL 获取将字节保存在内存中，不需要 Environment 挂载或临时文件。接收媒体的 Model 应用自身的请求预处理。YouTube 使用独立的原生 URL 路径：声明 `url_input.video: [youtube]`，并使用不受限的 Run 主机名策略。它保持为原生 URL，不回退到下载或辅助 Model。

## 声明模型输入能力

需要原生输入时，在 `AgentSpec.model_characteristics` 中设置对应值，再用该 spec 构建 Agent：

```python
from a13n_harness import AgentSpec, HarnessModelCharacteristics, ModelCapability

agent_spec = AgentSpec(
    model_characteristics=HarnessModelCharacteristics(
        capabilities=frozenset({
            ModelCapability.IMAGE_UNDERSTANDING,
            ModelCapability.VIDEO_UNDERSTANDING,
            ModelCapability.AUDIO_UNDERSTANDING,
        })
    )
)
```

只保留所选 Model 和传输路径接受的类型。三种值相互独立，未声明的类型使用已配置的理解 provider。本地示例有意不声明三种能力，以选择音频回退。原生图像预处理可以改变发出的请求，同时在历史中保留原始字节。

## 配置默认的媒体理解 Agent

部署时若不通过代码绑定 provider，可为各回退类型配置带 provider 前缀的 Model 字符串及对应凭据：

| 类型 | Model 字符串变量                         | 可选原生设置 JSON                                 |
| ---- | ---------------------------------------- | ------------------------------------------------- |
| 图像 | `A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL` | `A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL_SETTINGS` |
| 视频 | `A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL` | `A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL_SETTINGS` |
| 音频 | `A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL` | `A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL_SETTINGS` |

例如，在 shell 导出前，将下面的占位符替换为账号支持的音频 Model 字符串：

```bash
export A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL='PROVIDER:AUDIO_MODEL'
export A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL_SETTINGS='{"temperature":0.1}'
```

通过 shell、容器运行时或应用提供变量。Harness 在需要回退时读取对应类型；`.env` 文件由应用加载。缺少 Model 字符串只影响该类型。仓库贡献者可以从 `.env.harness.example` 开始配置。

每个理解 Agent 的初始设置为 `temperature=0.1`。设置 JSON 覆盖或扩展其原生 `ModelSettings`，例如 `{"temperature":0.2,"max_tokens":8192}`。使用有效且大小受限的 JSON 对象，并确保接收 Model 支持其中的设置。要在本地示例中改用这些默认值，应将测试 Model 替换为真实 Model，并移除显式 `file_media_understanding` 绑定。

## 默认分析行为

默认图像提示要求分析可见内容、OCR、布局和风格。视频与音频提示按录制顺序分析语音、屏幕内容和非语音声音。转写保留原语言，并标注不清楚的内容。通过明确的 `instructions`，让分析聚焦应用需要的结果。

文件获取接受最大 100 MiB 的图像及最大 16 MiB 的音频或视频，Environment provider 更小的限制仍然生效。E2B 默认文件限制为 100 MiB，其他 provider 保留各自限制。读取后，默认图像预处理将每张图像或切片限制为 5 MiB base64 数据，并将静态源图像处理限制为 8000 万像素。音频和视频不使用图像压缩。

内置理解 Agent 的图像超时为 120 秒，视频为 300 秒，音频为 180 秒，同时限制文本输出、输出校验重试和临时 HTTP 错误重试。文件作用域在有界读取后结束，随后分析 Agent 或自定义 provider 才执行外部工作。自定义 provider 自行管理其分析实现的执行限制。

## 覆盖单次执行的配置

Host 已经解析 Model、管理凭据或需要专用分析时，使用 Run 绑定。以下工厂接收明确的原生 Model 和 Host 选择的模型 ID：

```python
from a13n_harness import RunBindings
from a13n_harness.toolsets import AgentMediaUnderstandingProvider
from pydantic_ai.models import Model


def audio_bindings(model: Model, model_id: str) -> RunBindings:
    provider = AgentMediaUnderstandingProvider(
        models={"audio": model},
        model_ids={"audio": model_id},
        model_settings={"audio": {"temperature": 0.1}},
    )
    return RunBindings.embedded(file_media_understanding=provider)
```

将返回值作为 `bindings` 传给 `agent.run()` 或 `agent.stream()`。内置 provider 接受 Model 实例或带 provider 前缀的字符串。使用 `model_ids` 时，每次分析请求都会把 Host ID 用作 `ModelCall.model_id` 和定价输入中的选定模型 ID，使准入、归属和定价使用同一选择。

自定义 provider 可以适配其他分析客户端。以下类显式接收应用的异步分析函数；该函数取得已脱离文件作用域的有界字节，并返回 `MediaUnderstandingResult`：

```python
from collections.abc import Awaitable, Callable

from a13n_harness.metering import ModelUsageBinding
from a13n_harness.toolsets import MediaUnderstandingRequest, MediaUnderstandingResult


class CustomAudioProvider:
    def __init__(
        self,
        analyze: Callable[
            [MediaUnderstandingRequest, ModelUsageBinding | None],
            Awaitable[MediaUnderstandingResult],
        ],
    ):
        self.analyze = analyze

    def supports(self, kind: str) -> bool:
        return kind == "audio"

    async def understand(
        self, request: MediaUnderstandingRequest, *, usage: ModelUsageBinding | None = None,
    ) -> MediaUnderstandingResult:
        return await self.analyze(request, usage)
```

通过 `RunBindings.embedded(file_media_understanding=CustomAudioProvider(your_analyze_function))` 绑定。回调需要实现外部分析，并通过传入的用量绑定或结果回执返回可获得的用量，避免重复记录同一工作。仅构造适配器不会执行分析。

`supports(kind)` 报告可用类型，不解析凭据或发出请求。旧的仅实现 understand 的 provider 仍可用于 `view`，实现发现接口后才能暴露 URL 回退。自定义 Run provider 服务于两个工具，优先于所有环境 Model 字符串；其失败不会触发环境默认值重试。已声明原生能力的类型仍优先走原生输入。

## 用量与失败

辅助分析保留独立的原生计数，并计入调用方 Agent 的 Context 用量快照和模型预算。归属信息包含当前工具调用 ID，并使用以下来源：

| 输入 | 用量来源                    | 稳定工具 ID            |
| ---- | --------------------------- | ---------------------- |
| 文件 | `files.media_understanding` | `filesystem.view`      |
| URL  | `media.url_understanding`   | `media.read_video_url` |

记录包含 provider、模型（`model_name`，或自定义 `ProviderUsage.product`）、请求数及可获得的 token 计数。分析失败、超时或取消后，已经确认的观测仍会保留。已发出调用但未收到响应时记录用量不可获得，发出前被拒绝则不记录请求。

媒体或 provider 错误返回大小受限的普通工具结果，Agent 可以检查这些结果，而不消耗函数工具重试额度。取消、Harness 准入失败和用量限制失败通过各自的 Run 边界传播。根据错误码选择后续操作：

| 错误码                                      | 后续操作                                   |
| ------------------------------------------- | ------------------------------------------ |
| `media_understanding_unavailable`           | 为该类型配置原生支持或回退                 |
| `media_understanding_configuration_invalid` | 修正 Model 或大小受限的设置 JSON           |
| `media_understanding_timeout`               | 缩小输入、明确重点，或检查分析 provider    |
| `media_understanding_response_invalid`      | 让 provider 返回有效、非空且大小受限的文本 |
| `media_understanding_failed`                | 再次尝试前检查已配置的 provider            |

获取阶段还可能因 MIME、大小、HTTP 状态、超时或主机名策略失败。先解决对应原因，再重试。文件路由与授权参阅[环境](environments.md)。

## 从 `read_video_url` 迁移

新集成使用模型可见名称 `read_media_url` 和公开的 `MediaUrlToolset`。权限规则保留稳定 ID `media.read_video_url`，这些规则现在控制全部三种媒体类型。升级前完成或关闭旧名称的待处理调用，因为它们无法在重命名后的工具面上恢复。
