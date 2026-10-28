---
title: Multimedia understanding
description: Read local media or direct URLs, select native input or an analysis Model, and handle the resulting text and usage.
---

Use `view` for media in an Environment and `read_media_url` for a direct HTTP(S) image, video, or audio resource. Give the tool a focused task, such as reading a label, transcribing speakers, locating an event by timestamp, or reviewing a UI.

Harness chooses a path for each media kind:

| Configuration                                              | Result delivered to the calling Model                                    |
| ---------------------------------------------------------- | ------------------------------------------------------------------------ |
| Matching native input capability declared                  | Original media attached as `BinaryContent`                               |
| No matching declaration; understanding provider configured | Text returned by a separate media-understanding Agent or custom provider |
| Neither available                                          | `media_understanding_unavailable`                                        |

A declaration describes support already present in the Model and its provider path. It does not add perception to a text-only Model. Native request failures do not switch to auxiliary inference.

## Use from `view`

First verify the complete file-to-analysis path locally. The example creates a tiny silent WAV, mounts its temporary directory, calls `view` through an Agent, and sends the bytes to a second deterministic Model. It needs the Python environment from [Quickstart](getting-started.md), with no API key or network request. Save it as `media_demo.py` and run `uv run --locked python media_demo.py` from the checkout root.

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

Expected output is `Audio fallback completed.`. The analysis callback checks that WAV bytes arrived and returns fixed text; it tests the integration, not speech recognition. Replace that callback's `FunctionModel` with an authenticated audio-capable Model to perform real analysis.

`DynamicEnvironmentCapability` supplies the file tools, while `PreparedSource` adapts the already-created local connector. For an existing application, reuse its [Environment binding](environments.md) instead. Text viewing needs text-read permission. Media viewing needs stat and byte-read on the same selected mount; permissions from separate mounts cannot be combined.

Set `instructions` to the desired output: for example, “Transcribe each speaker and mark uncertain words.” The same instructions accompany native input or reach the fallback provider.

## Read media URLs

`read_media_url(url, instructions=None, media_type=None)` becomes available automatically when the current Model or a configured provider supports a media kind. Its description lists those kinds. The provider in the example exposes audio URL fallback as well as local audio viewing.

1. Obtain a direct media URL, not the HTML page containing the player or image.
2. Ask the Agent to call `read_media_url` with that URL and focused instructions. Pasting a link alone leaves it as text until the tool is called.
3. Supply `media_type`, such as `audio/wav`, when the response has no useful Content-Type and the final URL has no recognizable extension.
4. Inspect the returned analysis or error before deciding on another request.

Response Content-Type takes precedence; absent or generic binary Content-Type falls back to explicit MIME or the final URL extension. HTML and empty bodies are rejected. The download checks Run hostname policy for the source and every redirect, follows at most five redirects, and uses a 120-second total deadline. Images are bounded to 100 MiB; audio and video to 16 MiB. Native video also fits the current Model's encoded budget, 10 MiB by default. Fallback video uses the source bound independently of an unsupported primary Model's budget.

URL acquisition keeps bytes in memory and needs no Environment mount or temporary file. The receiving Model applies its own request preparation. YouTube is a separate native-URL path: declare `url_input.video: [youtube]` and use unrestricted Run hostname policy. It stays a native URL, with no download or auxiliary-model fallback.

## Declare model input capabilities

For native input, set the matching values in `AgentSpec.model_characteristics` and build the Agent with that spec:

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

Keep only the kinds that the selected Model and transport accept. The values are independent; an undeclared kind uses its configured understanding provider. In the local example, leaving all three undeclared deliberately selects the audio fallback. Native image preparation can change the outgoing request while retaining original bytes in history.

## Configure the default media-understanding Agents

For deployment without a code-bound provider, configure each fallback kind with a provider-qualified Model string and that provider's credentials:

| Kind  | Model string variable                    | Optional native settings JSON                     |
| ----- | ---------------------------------------- | ------------------------------------------------- |
| Image | `A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL` | `A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL_SETTINGS` |
| Video | `A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL` | `A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL_SETTINGS` |
| Audio | `A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL` | `A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL_SETTINGS` |

For example, replace the placeholder below with an account-supported audio Model string before exporting it in your shell:

```bash
export A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL='PROVIDER:AUDIO_MODEL'
export A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL_SETTINGS='{"temperature":0.1}'
```

Supply the variables through the shell, container runtime, or application. Harness reads the corresponding kind when fallback is needed; your application loads `.env` files. A missing Model string affects only that kind. Repository contributors can start from `.env.harness.example`.

Each understanding Agent starts with `temperature=0.1`. Settings JSON overrides or extends its native `ModelSettings`, for example `{"temperature":0.2,"max_tokens":8192}`. Use a valid bounded JSON object and settings supported by that receiving Model. To use these defaults in the local example, replace the fixture Models with real ones and omit its explicit `file_media_understanding` binding.

## Default analysis behavior

The default image prompt asks for visible content, OCR, layout, and style. Video and audio prompts follow the recording chronologically, including speech, on-screen content, and non-speech sounds. Transcripts preserve the source language and mark unclear content. Focused `instructions` direct the analysis toward the result your application needs.

File acquisition accepts images up to 100 MiB and audio/video up to 16 MiB, subject to a smaller Environment provider limit. E2B defaults to a 100 MiB file limit; other providers keep their own limits. After reading, default image preparation bounds each image or segment to 5 MiB of base64 data and static source processing to 80 million pixels. Audio and video do not use image compression.

The built-in understanding Agent uses a 120-second image, 300-second video, or 180-second audio timeout. It also bounds text output, output-validation retries, and transient HTTP retries. File scope ends after the bounded read, before the analysis Agent or custom provider performs external work. Custom providers own the execution limits of their analysis implementation.

## Override one Run

Use a Run binding when your Host has already resolved a Model, owns credentials, or needs specialized analysis. This factory takes an explicit native Model and your Host's selected model ID:

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

Pass its return value as `bindings` to `agent.run()` or `agent.stream()`. The built-in provider accepts Model instances or provider-qualified strings. With `model_ids`, each analysis request carries the Host's ID as `ModelCall.model_id` and as the selected model ID in pricing input, so admission, attribution, and pricing use the same selection.

A custom provider can adapt another analysis client. The following class takes your async analysis function explicitly; that function receives detached bounded bytes and returns a `MediaUnderstandingResult`:

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

Bind it with `RunBindings.embedded(file_media_understanding=CustomAudioProvider(your_analyze_function))`. The callback must implement the external analysis and return its available usage through the supplied binding or result receipts, without counting the same work twice. Constructing this adapter alone does not run analysis.

`supports(kind)` reports availability without resolving credentials or making requests. Older understand-only providers still work for `view`; implement discovery to expose URL fallback. A custom Run provider serves both tools and takes precedence over all environment Model strings. Its failure does not trigger an ambient default retry. Native capability declarations still take precedence for native-supported kinds.

## Usage and failures

Auxiliary analysis retains independent native counters and contributes to the calling Agent's Context usage snapshot and model budget. Attribution identifies the active tool-call ID and uses these sources:

| Input | Usage source                | Stable tool ID         |
| ----- | --------------------------- | ---------------------- |
| File  | `files.media_understanding` | `filesystem.view`      |
| URL   | `media.url_understanding`   | `media.read_video_url` |

Records include provider, model (`model_name`, or custom `ProviderUsage.product`), request count, and available token counters. Known observations survive analysis failure, timeout, or cancellation. Dispatch without a response records unavailable usage; rejection before dispatch records no request.

Media/provider failures return bounded ordinary tool results, so the Agent can inspect them without consuming its function-tool retry allowance. Cancellation, Harness admission failures, and usage-limit failures propagate through their own Run boundaries. Use the error code to choose the next action:

| Code                                        | Next action                                                 |
| ------------------------------------------- | ----------------------------------------------------------- |
| `media_understanding_unavailable`           | Configure native support or a fallback for that kind        |
| `media_understanding_configuration_invalid` | Correct the Model or bounded settings JSON                  |
| `media_understanding_timeout`               | Reduce or focus the input, or inspect the analysis provider |
| `media_understanding_response_invalid`      | Make the provider return valid non-empty bounded text       |
| `media_understanding_failed`                | Inspect the configured provider before another attempt      |

Acquisition can also fail on MIME, size, HTTP status, timeout, or hostname policy. Resolve that cause before retrying. See [Environments](environments.md) for file routing and authorization.

## Migrate from `read_video_url`

Use the model-facing name `read_media_url` and public `MediaUrlToolset` in new integrations. Keep permission rules under the stable ID `media.read_video_url`; those rules now govern all three media kinds. Finish or close pending old-name calls before upgrading, because they cannot resume on the renamed tool surface.
