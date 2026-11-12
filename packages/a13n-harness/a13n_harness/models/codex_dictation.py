"""One-shot speech transcription using an existing Codex subscription source."""

import json

import httpx2
from pydantic_ai.exceptions import ModelAuthenticationError
from pydantic_ai.providers.openai_codex import (
    CredentialsPersistenceError,
    CredentialsRefreshError,
    OpenAICodexCredentialSource,
    OpenAICodexProvider,
)

from a13n_harness.configuration import RunConfiguration
from a13n_harness.http import ProviderHttpError, bounded_response_body
from a13n_harness.models.transport import create_model_http_client

TRANSCRIBE_URL = "https://chatgpt.com/backend-api/transcribe"


class CodexDictationError(Exception):
    """A bounded transcription failure without provider bodies or credentials."""


async def transcribe(
    audio: bytes,
    credential_source: OpenAICodexCredentialSource,
    *,
    configuration: RunConfiguration | None = None,
) -> str:
    """Upload one WAV. The service, not a Responses model name, selects ASR.

    The native provider owns host-scoped authentication, refresh and persistence.
    Ambiguous uploads are not replayed by an HTTP retry or across accounts.
    """
    async with create_model_http_client(retry=None, timeout=60, configuration=configuration) as client:
        OpenAICodexProvider(credential_source=credential_source, http_client=client)
        try:
            async with client.stream(
                "POST",
                TRANSCRIBE_URL,
                files={"file": ("audio.wav", audio, "audio/wav")},
                follow_redirects=False,
            ) as response:
                if not response.is_success:
                    if response.headers.get("cf-mitigated") == "challenge":
                        message = "Codex transcription was blocked by a transport challenge. Try again later."
                    elif response.status_code == 401:
                        message = "Codex transcription authentication was rejected. Sign in again."
                    elif response.status_code == 429:
                        message = "Codex transcription is rate limited. Try again later."
                    else:
                        message = f"Codex transcription returned HTTP {response.status_code}."
                    raise CodexDictationError(message)
                body = await bounded_response_body(response, max_bytes=512 * 1024)
            payload = json.loads(body)
            if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
                raise CodexDictationError("Codex transcription returned an invalid transcript.")
            return payload["text"].strip()
        except (CredentialsRefreshError, CredentialsPersistenceError, ModelAuthenticationError):
            raise CodexDictationError("Codex transcription authentication was rejected. Sign in again.") from None
        except (ValueError, ProviderHttpError):
            raise CodexDictationError("Codex transcription returned an invalid response.") from None
        except httpx2.HTTPError:
            raise CodexDictationError("Could not reach Codex transcription.") from None
