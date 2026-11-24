---
title: Multimedia understanding
description: Inspect images, audio, video, and media URLs in Harness using native multimodal model inputs or dedicated media-analysis agents.
---

Agents inspect workspace media files with `view(file_path, instructions=...)` and direct HTTP(S) media resources with `read_media_url(url, instructions=None, media_type=None)`:

| Configuration                                          | Delivery path                                            |
| ------------------------------------------------------ | -------------------------------------------------------- |
| Modality declared in `AgentSpec.model_characteristics` | Original bytes sent natively as `BinaryContent`          |
| Undeclared modality + fallback provider configured     | Analyzed by a dedicated fallback Model / custom provider |
| Neither configured                                     | Returns `media_understanding_unavailable`                |

## Declare native model capabilities

Declare only the binary input modalities that the active Model and transport natively support:

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

## Configure fallback media-understanding Models

For text-only primary Models (or unsupported modalities), supply fallback Models either via environment variables or per-Run `RunBindings`:

| Modality | Model route env var                      | Optional `ModelSettings` JSON env var             |
| -------- | ---------------------------------------- | ------------------------------------------------- |
| Image    | `A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL` | `A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL_SETTINGS` |
| Video    | `A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL` | `A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL_SETTINGS` |
| Audio    | `A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL` | `A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL_SETTINGS` |

Or bind resolved Models (or a custom provider implementing `supports(kind)` and `async understand(request, *, usage=None)`) on `RunBindings`:

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

## Limits and attribution

- **Bounds:** Images up to 100 MiB (5 MiB base64 per prepared image/segment by default); audio and video up to 16 MiB (`read_media_url` checks `RunConfiguration.allowed_hosts` on initial URL and up to 5 redirects).
- **Usage & permissions:** Auxiliary calls record usage under `files.media_understanding` (`filesystem.view`) and `media.url_understanding` (permission ID `media.read_video_url`).
