---
title: Multimedia understanding
description: Understand image, video, and audio files and URLs through native model input or a dedicated media-understanding Agent.
---

Use `view` for media files in an Environment and `read_media_url` for direct HTTP(S) media resources. Ask for a specific result: OCR, a transcript, timestamps, or a UI review.

| Media configuration                                                    | Tool result                                              |
| ---------------------------------------------------------------------- | -------------------------------------------------------- |
| Current Model declares the matching native input capability            | Media attached as native `BinaryContent`                 |
| No native declaration; a matching understanding provider is configured | Text analysis from a dedicated media-understanding Agent |
| Neither is configured                                                  | `media_understanding_unavailable`                        |

Harness selects the path from the configuration for each media kind. [Declare native capabilities](#declare-model-input-capabilities) or [configure an understanding Agent](#configure-the-default-media-understanding-agents), then use the tools below.

## Use from `view`

Enable Environment file tools through `DynamicEnvironmentCapability`; the model can then call `view`. Text viewing requires text-read permission. Media viewing requires both stat and byte-read on the selected mount; permissions on separate mounts do not combine to authorize a media read:

```python
from a13n_harness.environment import (
    DynamicEnvironmentCapability,
    DynamicEnvironmentConfiguration,
)

capabilities = (
    DynamicEnvironmentCapability(DynamicEnvironmentConfiguration()),
)
```

The same tool accepts text and supported media paths. For media, `instructions` should state the focused analysis needed, such as OCR, speaker transcription, timestamps, UI review, or a specific uncertain region.

## Read media URLs

Call `read_media_url(url, instructions=None, media_type=None)` for a direct image, video, or audio resource. The tool appears automatically when the current Model or a configured understanding provider supports a media kind. Its description lists available kinds; image-only configuration does not enable video or audio.

Downloads enforce Run hostname policy on the source and each redirect, a 120-second total deadline, and bounded bytes. Response Content-Type determines the kind. When Content-Type is absent or generic binary, explicit `media_type` or the final URL extension supplies it. HTML pages are rejected. Images are limited to 100 MiB and audio/video to 16 MiB. Native video downloads also fit the current Model's encoded budget (10 MiB by default); fallback video is independent of that unsupported primary Model's budget. The receiving Model applies its own request preparation.

YouTube requires explicit native `url_input.video: [youtube]` support and unrestricted Run hostname policy. It stays a native URL, with no auxiliary-model or download fallback. Use `view` for local files; URL reads need no Environment mount or temporary file.

## Declare model input capabilities

Declare the media kinds accepted by your Model and provider path in `AgentSpec.model_characteristics`:

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

The three values are independent. An undeclared kind uses its configured understanding provider.

## Configure the default media-understanding Agents

Set a provider-qualified Pydantic AI model string for each media kind that needs fallback:

```bash
export A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL=google:gemini-3.7-flash
export A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL=google:gemini-3.7-flash
export A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL=google:gemini-3.7-flash
```

Supply these process environment variables through your shell, container runtime, or application. Harness reads them when fallback is needed; your application loads `.env` if you use one. Supply credentials through the selected provider's standard environment variables.

Each media-understanding Agent starts with `temperature=0.1`. Override or extend its native `ModelSettings` with a bounded JSON object:

```bash
export A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL_SETTINGS='{"temperature":0.2}'
export A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL_SETTINGS='{"temperature":0.1,"max_tokens":8192}'
export A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL_SETTINGS='{"temperature":0.1}'
```

The exact variables are:

| Media | Model string                             | Optional settings JSON                            |
| ----- | ---------------------------------------- | ------------------------------------------------- |
| Image | `A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL` | `A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL_SETTINGS` |
| Video | `A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL` | `A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL_SETTINGS` |
| Audio | `A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL` | `A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL_SETTINGS` |

Configure the kinds your application needs. A missing model string affects only that kind. Repository contributors can use `.env.harness.example` as a template.

## Default analysis behavior

Default image analysis covers visible content, OCR, layout, and style. Video and audio analysis follow the recording chronologically, including speech and on-screen or non-speech content. Transcriptions retain the original language and mark unclear content. Use `instructions` to focus either tool on the details you need.

`view` accepts source images up to 100 MiB and source audio/video up to 16 MiB, subject to any smaller Environment provider limit. E2B defaults to a 100 MiB file limit; other providers retain their own defaults. Default image preparation runs after reading and limits each image or segment to 5 MiB of base64-encoded data and static source processing to 80 million pixels. It changes the model request, not the original bytes retained in native image history. Audio and video do not use this image compression.

The runtime bounds each file read, media-understanding Agent timeout, output, output-validation retries, and transient HTTP retries. Video receives a longer timeout than image or audio. The Environment file scope is released after the bounded read, before the media-understanding Agent or custom provider performs external model work.

## Override one Run

Advanced integrations can replace the environment-configured default for one Run:

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

`supports(kind)` reports configured kinds without resolving credentials or making requests. Existing understand-only custom providers continue to work with `view`; implement this optional discovery method to expose URL fallback.

The custom provider bound for the Run serves both tools and takes precedence over all default environment model strings. Use it for a Host-owned model resolver, custom credentials, an existing provider client, or specialized analysis behavior. It does not change model input capability declarations.

`AgentMediaUnderstandingProvider` is also public for code-first construction with explicit Pydantic AI `Model` instances or model strings. A Host that resolved a `Model` instance from its own model ID passes that ID per kind in `model_ids`. Each understanding request of that kind carries the ID as its `ModelCall.model_id` and as the selected model ID of its pricing input. The Host can then admit, attribute, and price the request as that model.

## Usage and failures

Usage of media-understanding Agents and custom providers is recorded separately with:

- source `files.media_understanding` and tool ID `filesystem.view` for files;
- source `media.url_understanding` and stable tool ID `media.read_video_url` for URLs;
- the active tool-call ID;
- provider, model (`model_name`, or `product` in a custom provider's `ProviderUsage`), request count, and available token counters.

This nested model work retains independent Pydantic AI counters but contributes to the calling Agent's Context usage snapshot and model budget. Known observations survive analysis failure, timeout, or cancellation. Failure after Model dispatch but before a response records unavailable usage rather than inventing tokens or cost; rejection before dispatch records no request.

Both tools return stable bounded failures instead of attaching unsupported media or exposing provider exceptions. Each failure is an ordinary tool result visible to the calling Agent, not a Pydantic AI retry prompt, so it does not consume the calling Agent's function-tool retry allowance. Common codes include:

| Code                                        | Meaning                                                                                                      |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `media_understanding_unavailable`           | The active model lacks native support and no fallback model or custom provider is configured for that kind.  |
| `media_understanding_configuration_invalid` | A configured settings value is not a valid bounded JSON object.                                              |
| `media_understanding_timeout`               | The dedicated analysis exceeded its kind-specific timeout.                                                   |
| `media_understanding_response_invalid`      | The custom media-understanding provider or the understanding model returned empty or invalid bounded output. |
| `media_understanding_failed`                | The custom media-understanding provider or the understanding model failed without a more specific safe code. |

See [Environments](environments.md) for file routing, authorization, and byte limits.

## Migrate from `read_video_url`

The new tool replaces `read_video_url` and uses `MediaUrlToolset` in Python. Keep permission rules under the stable ID `media.read_video_url`; they now govern all three media kinds. Finish or close pending old-name tool calls before upgrading, because they cannot resume on the renamed surface.
