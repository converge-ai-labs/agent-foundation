# Multimedia Understanding

Agent Harness provides a first-party path for understanding image, video, and audio files in an Environment. The model-facing `view` tool selects one of two paths automatically:

1. attach the file as native `BinaryContent` when the active Agent model declares the matching capability;
2. run a dedicated media-understanding Agent and return its textual analysis otherwise.

The calling model does not choose the path, and the Harness never guesses support from a model name or Pydantic AI `Model.profile`.

## Declare native model capabilities

Native media support is Harness-owned `AgentSpec` configuration. Callers use the `model_characteristics` construction key; Python reads the resolved value through `spec.model_characteristics`:

```python
from a13n_harness import AgentSpec, ModelCapability, HarnessModelCharacteristics

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

Declare only capabilities the active model and provider path can actually accept. The three values are independent. An absent value is conservative: `view` uses dedicated understanding rather than attaching unsupported content.

## Configure the default understanding Agents

Set a provider-qualified Pydantic AI model key for each media kind that needs fallback:

```bash
export A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL=google:gemini-3.7-flash
export A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL=google:gemini-3.7-flash
export A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL=google:gemini-3.7-flash
```

Harness reads these process environment variables lazily when `view` needs fallback. It does not load `.env`; an executable, shell, container runtime, or process manager can provide the variables by any ordinary mechanism. Provider credentials continue to use the selected Pydantic AI provider's standard environment variables.

Each Agent starts with `temperature=0.1`. Override or extend its native `ModelSettings` with a bounded JSON object:

```bash
export A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL_SETTINGS='{"temperature":0.2}'
export A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL_SETTINGS='{"temperature":0.1,"max_tokens":8192}'
export A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL_SETTINGS='{"temperature":0.1}'
```

The exact variables are:

| Media | Model key                                | Optional settings JSON                            |
| ----- | ---------------------------------------- | ------------------------------------------------- |
| Image | `A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL` | `A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL_SETTINGS` |
| Video | `A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL` | `A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL_SETTINGS` |
| Audio | `A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL` | `A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL_SETTINGS` |

You can configure one, two, or all three kinds. A missing model key affects only its matching media kind. Repository contributors can copy these entries from the root `.env.harness.example` template into `.env`; the Harness itself still reads only the resulting process environment.

## Default analysis behavior

The dedicated Agents use packaged, kind-specific production prompts:

- image analysis covers all visible elements, exact OCR, layout, visual style, and actionable UI/CSS details;
- video analysis follows the complete chronology, captures speech and on-screen content, distinguishes speakers conservatively, and extracts user intent only when present;
- audio analysis follows the recording chronologically and separates speech, music, sound effects, ambience, and audio-quality observations.

All three prompts prioritize accuracy over speculation, preserve original-language transcription, mark unreadable or inaudible content explicitly, and avoid assigning identities that are not stated. A focused `view(..., instructions=...)` value narrows the analysis while those anti-hallucination rules remain in force. Without focused instructions, the Agent uses a comprehensive kind-specific default and reports unclear or omitted content.

The runtime bounds each file read, dedicated Agent timeout, output, output-validation retries, and transient HTTP retries. Video receives a longer timeout than image or audio. The Environment file scope is released after the bounded read, before the dedicated Agent or custom provider performs external model work.

## Use from `view`

Enable Environment file tools through `DynamicEnvironmentCapability`, then use `view` normally:

```python
from a13n_harness import DynamicEnvironmentCapability, DynamicEnvironmentConfiguration

capabilities = (
    DynamicEnvironmentCapability(
        DynamicEnvironmentConfiguration(
            file_tools=True,
            shell_tools=False,
        )
    ),
)
```

The same tool accepts text and supported media paths. For media, `instructions` should state the focused analysis needed, such as OCR, speaker transcription, timestamps, UI review, or a specific uncertain region.

## Override one run

Advanced integrations can replace the environment-configured default for one run:

```python
from a13n_harness import (
    FileMediaUnderstandingRunCapability,
    MediaUnderstandingRequest,
    MediaUnderstandingResult,
    RunBindings,
)


class CustomMediaUnderstandingProvider:
    async def understand(
        self,
        request: MediaUnderstandingRequest,
    ) -> MediaUnderstandingResult:
        text, usage = await analyze_with_custom_model(request)
        return MediaUnderstandingResult(text=text, usage=usage)


bindings = RunBindings.embedded(
    capabilities=(
        FileMediaUnderstandingRunCapability(
            provider=CustomMediaUnderstandingProvider(),
        ),
    )
)
```

The run provider has higher precedence than all default environment model keys. Use it for a Host-owned model resolver, custom credentials, an existing provider client, or specialized analysis behavior. It does not change native capability declarations.

`AgentMediaUnderstandingProvider` is also public for code-first construction with explicit Pydantic AI `Model` instances or model strings.

## Usage and failures

Dedicated-Agent and custom-provider usage is recorded separately with:

- source `files.media_understanding`;
- tool ID `filesystem.view`;
- the active tool-call ID;
- provider, model product, request count, and available token counters.

This nested model work does not change the active Agent's Pydantic AI `RunUsage` or native `UsageLimits`. If analysis fails or times out after model responses have already contributed counters, those proven counters are still recorded; a failure before any measured request does not invent usage.

`view` returns stable bounded failures instead of attaching unsupported media or exposing provider exceptions. Each failure is an ordinary tool result visible to the active Agent, not a Pydantic AI retry prompt, so it does not consume the main Agent's function-tool retry allowance. Common codes include:

| Code                                        | Meaning                                                                                                     |
| ------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `media_understanding_unavailable`           | The active model lacks native support and no fallback model or custom provider is configured for that kind. |
| `media_understanding_configuration_invalid` | A configured settings value is not a valid bounded JSON object.                                             |
| `media_understanding_timeout`               | The dedicated analysis exceeded its kind-specific timeout.                                                  |
| `media_understanding_response_invalid`      | The provider returned empty or invalid bounded output.                                                      |
| `media_understanding_failed`                | The provider or model failed without a more specific safe code.                                             |

See [Environments](environments.md) for file routing, authorization, and byte limits.
