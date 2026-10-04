---
title: Multimedia understanding
description: Understand image, video, and audio files through native model input or a dedicated media-understanding Agent.
---

Harness provides a first-party path for understanding image, video, and audio files in an Environment. The model-facing `view` tool selects one of two paths automatically:

1. attach the file as native `BinaryContent` when the active Agent's `model_characteristics` declare the matching model input capability;
2. run a dedicated media-understanding Agent and return its textual analysis otherwise.

The calling model does not choose the path, and the Harness never guesses support from a model name or Pydantic AI `Model.profile`.

## Declare model input capabilities

Native media support is Harness-owned `AgentSpec` configuration. Callers use the `model_characteristics` construction key; Python reads the resolved value through `spec.model_characteristics`:

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

Declare only model input capabilities that the active model and provider path can actually accept. The three values are independent. An absent value is conservative: `view` uses a dedicated media-understanding Agent rather than attaching unsupported content.

## Configure the default media-understanding Agents

Set a provider-qualified Pydantic AI model string for each media kind that needs fallback:

```bash
export A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL=google:gemini-3.7-flash
export A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL=google:gemini-3.7-flash
export A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL=google:gemini-3.7-flash
```

Harness reads these process environment variables lazily when `view` needs fallback. It does not load `.env`; an executable, shell, container runtime, or process manager can provide the variables by any ordinary mechanism. Provider credentials continue to use the selected Pydantic AI provider's standard environment variables.

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

You can configure one, two, or all three kinds. A missing model string affects only its matching media kind. Repository contributors can copy these entries from the root `.env.harness.example` template into `.env`; the Harness itself still reads only the resulting process environment.

## Default analysis behavior

The dedicated media-understanding Agents use packaged, kind-specific production prompts:

- image analysis covers all visible elements, exact OCR, layout, visual style, and actionable UI/CSS details;
- video analysis follows the complete chronology, captures speech and on-screen content, distinguishes speakers conservatively, and extracts user intent only when present;
- audio analysis follows the recording chronologically and separates speech, music, sound effects, ambience, and audio-quality observations.

All three prompts prioritize accuracy over speculation, preserve original-language transcription, mark unreadable or inaudible content explicitly, and avoid assigning identities that are not stated. A focused `view(..., instructions=...)` value narrows the analysis while those anti-hallucination rules remain in force. Without focused instructions, the media-understanding Agent uses a comprehensive kind-specific default and reports unclear or omitted content.

The runtime bounds each file read, media-understanding Agent timeout, output, output-validation retries, and transient HTTP retries. Video receives a longer timeout than image or audio. The Environment file scope is released after the bounded read, before the media-understanding Agent or custom provider performs external model work.

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

## Override one Run

Advanced integrations can replace the environment-configured default for one Run:

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

The custom provider bound for the Run takes precedence over all default environment model strings. Use it for a Host-owned model resolver, custom credentials, an existing provider client, or specialized analysis behavior. It does not change model input capability declarations.

`AgentMediaUnderstandingProvider` is also public for code-first construction with explicit Pydantic AI `Model` instances or model strings. A Host that resolved a `Model` instance from its own model ID passes that ID per kind in `model_ids`. Each understanding request of that kind carries the ID as its `ModelCall.model_id` and as the selected model ID of its pricing input. The Host can then admit, attribute, and price the request as that model.

## Usage and failures

Usage of media-understanding Agents and custom providers is recorded separately with:

- source `files.media_understanding`;
- tool ID `filesystem.view`;
- the active tool-call ID;
- provider, model (`model_name`, or `product` in a custom provider's `ProviderUsage`), request count, and available token counters.

This nested model work retains independent Pydantic AI counters but contributes to the calling Agent's Context usage snapshot and model budget. Known observations survive analysis failure, timeout, or cancellation. Failure after Model dispatch but before a response records unavailable usage rather than inventing tokens or cost; rejection before dispatch records no request.

`view` returns stable bounded failures instead of attaching unsupported media or exposing provider exceptions. Each failure is an ordinary tool result visible to the calling Agent, not a Pydantic AI retry prompt, so it does not consume the calling Agent's function-tool retry allowance. Common codes include:

| Code                                        | Meaning                                                                                                      |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `media_understanding_unavailable`           | The active model lacks native support and no fallback model or custom provider is configured for that kind.  |
| `media_understanding_configuration_invalid` | A configured settings value is not a valid bounded JSON object.                                              |
| `media_understanding_timeout`               | The dedicated analysis exceeded its kind-specific timeout.                                                   |
| `media_understanding_response_invalid`      | The custom media-understanding provider or the understanding model returned empty or invalid bounded output. |
| `media_understanding_failed`                | The custom media-understanding provider or the understanding model failed without a more specific safe code. |

See [Environments](environments.md) for file routing, authorization, and byte limits.
