---
title: 多模态媒体理解
description: 在 Harness 中通过模型原生多模态输入或专用媒体分析智能体读取图像、音频、视频与远程媒体 URL。
---

智能体通过 `view(file_path, instructions=...)` 查看工作区媒体文件，通过 `read_media_url(url, instructions=None, media_type=None)` 读取 HTTP(S) 直链媒体资源：

| 配置情况                                         | 投递路径                                             |
| ------------------------------------------------ | ---------------------------------------------------- |
| `AgentSpec.model_characteristics` 已声明对应模态 | 将原始字节以 `BinaryContent` 原生传给当前主模型      |
| 未声明原生模态，但已配置回退分析提供方           | 交由独立的回退多模态模型或自定义提供方分析并返回文本 |
| 两者均未配置                                     | 返回 `media_understanding_unavailable`               |

## 声明模型原生多模态能力

仅声明当前模型及传输通道实际支持的二进制输入模态：

```python
from a13n_harness import AgentSpec, HarnessModelCharacteristics, ModelCapability

agent_spec = AgentSpec(
    model="google-cloud:gemini-2.5-pro",
    model_characteristics=HarnessModelCharacteristics(
        capabilities=frozenset({
            ModelCapability.IMAGE_UNDERSTANDING,
            ModelCapability.VIDEO_UNDERSTANDING,
            ModelCapability.AUDIO_UNDERSTANDING,
        })
    ),
)
```

## 配置媒体理解回退模型

当主模型为纯文本模型（或不支持某类媒体）时，可通过进程环境变量或按次 `RunBindings` 指定回退分析模型：

| 模态 | 模型路由环境变量                         | 可选 `ModelSettings` JSON 环境变量                |
| ---- | ---------------------------------------- | ------------------------------------------------- |
| 图像 | `A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL` | `A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL_SETTINGS` |
| 视频 | `A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL` | `A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL_SETTINGS` |
| 音频 | `A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL` | `A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL_SETTINGS` |

也可在 `RunBindings` 中直接注入已解析的模型实例（或实现了 `supports(kind)` 与 `async understand(request, *, usage=None)` 的自定义适配器）：

```python
from a13n_harness import RunBindings
from a13n_harness.toolsets import AgentMediaUnderstandingProvider

provider = AgentMediaUnderstandingProvider(
    models={"audio": audio_model, "image": image_model},
    model_ids={"audio": "audio-default", "image": "image-default"},
    model_settings={"audio": {"temperature": 0.1}},
)
bindings = RunBindings.embedded(file_media_understanding=provider)
```

## 体积上限与用量归因

- **读取上限：** 图片最大 100 MiB（预处理后单图/分片默认 5 MiB base64）；音频与视频最大 16 MiB（`read_media_url` 会对初始 URL 及最多 5 次重定向执行 `RunConfiguration.allowed_hosts` 校验）。
- **用量与权限：** 辅助分析调用分别归因到 `files.media_understanding`（`filesystem.view`）与 `media.url_understanding`（稳定权限 ID 为 `media.read_video_url`）。
