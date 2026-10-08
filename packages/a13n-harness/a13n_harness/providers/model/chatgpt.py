"""Native OpenAI Provider for ChatGPT plan usage; no Host discovery or storage."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx2
from openai import AsyncOpenAI
from pydantic_ai import ModelProfile, UserError
from pydantic_ai.native_tools import WebSearchTool
from pydantic_ai.profiles import merge_profile
from pydantic_ai.profiles.openai import OpenAIModelProfile, openai_model_profile
from pydantic_ai.providers.openai import OpenAIProvider

from .oauth.chatgpt import (
    DYNAMIC_CLIENT_ID,
    ISSUER,
    RESOURCE,
    OpenAIChatGPTCredentials,
    OpenAIChatGPTCredentialSource,
    OpenAIChatGPTRefresh,
    refresh_chatgpt_credentials,
)
from .oauth.models import ModelAuthenticationError
from .oauth.rotation import require_same_account


class _ChatGPTAuth(httpx2.Auth):
    def __init__(
        self,
        source: OpenAIChatGPTCredentialSource,
        *,
        client: httpx2.AsyncClient,
        refresh: OpenAIChatGPTRefresh | None,
        refresh_window: timedelta,
    ):
        self.source = source
        self.client = client
        self.refresh = refresh
        self.refresh_window = refresh_window
        self.bound: OpenAIChatGPTCredentials | None = None

    def sync_auth_flow(self, request: httpx2.Request):
        del request
        raise UserError("ChatGPT OAuth requires an async HTTP client.")

    async def _current(self) -> OpenAIChatGPTCredentials:
        current = await self.source.load()
        if not isinstance(current, OpenAIChatGPTCredentials) or not (
            current.issuer == ISSUER
            and current.subject
            and current.ext_agent_host_id
            and current.client_id
            and current.client_id != DYNAMIC_CLIENT_ID
            and current.expires_at.tzinfo is not None
            and current.access_token
            and current.refresh_token
            and {"resource.invoke", "chatgpt.tokens.use.direct"}.issubset(current.scopes)
        ):
            raise ModelAuthenticationError("openai-chatgpt", "The ChatGPT credential source returned an invalid value.")
        if self.bound is not None:
            require_same_account(self.bound, current)
            if current.ext_agent_host_id != self.bound.ext_agent_host_id:
                raise ModelAuthenticationError("openai-chatgpt", "The ChatGPT registration changed hosts.")
        self.bound = current
        return current

    async def _rotate(self, current: OpenAIChatGPTCredentials) -> OpenAIChatGPTCredentials:
        async def exchange(expected: OpenAIChatGPTCredentials) -> OpenAIChatGPTCredentials:
            rotated = (
                await self.refresh(expected)
                if self.refresh is not None
                else await refresh_chatgpt_credentials(expected, http_client=self.client)
            )
            require_same_account(expected, rotated)
            if rotated.ext_agent_host_id != expected.ext_agent_host_id or datetime.now(UTC) >= rotated.expires_at:
                raise ModelAuthenticationError("openai-chatgpt", "ChatGPT refresh returned invalid credentials.")
            return rotated

        rotated = await self.source.rotate(current, exchange)
        require_same_account(current, rotated)
        return rotated

    async def async_auth_flow(self, request: httpx2.Request) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        parsed = urlsplit(str(request.url))
        if parsed.scheme != "https" or parsed.hostname != "api.openai.com" or parsed.port not in (None, 443):
            request.headers.pop("Authorization", None)
            yield request
            return
        await request.aread()
        current = await self._current()
        now = datetime.now(UTC)
        if now >= current.expires_at - self.refresh_window and (
            current.earliest_refresh_at is None or now >= current.earliest_refresh_at
        ):
            current = await self._rotate(current)
        request.headers["Authorization"] = f"Bearer {current.access_token}"
        response = yield request
        if response.status_code == 401:
            await response.aread()
            current = await self._rotate(current)
            request.headers["Authorization"] = f"Bearer {current.access_token}"
            yield request


class OpenAIChatGPTProvider(OpenAIProvider):
    """Inject an explicit credential source into the public OpenAI Responses endpoint."""

    def __init__(
        self,
        *,
        credential_source: OpenAIChatGPTCredentialSource,
        http_client: httpx2.AsyncClient | None = None,
        refresh: OpenAIChatGPTRefresh | None = None,
        refresh_window: timedelta = timedelta(minutes=5),
        extra_headers: dict[str, str] | None = None,
    ):
        if refresh_window < timedelta(0):
            raise ValueError("refresh_window must not be negative")
        owns_client = http_client is None
        client = http_client or httpx2.AsyncClient(follow_redirects=False)
        if client.auth is not None:
            raise UserError("The ChatGPT HTTP client must not already have authentication configured.")
        client.follow_redirects = False
        auth = _ChatGPTAuth(credential_source, client=client, refresh=refresh, refresh_window=refresh_window)
        client.auth = auth
        super().__init__(
            openai_client=AsyncOpenAI(
                base_url=RESOURCE,
                api_key="chatgpt-oauth",
                http_client=client,
                max_retries=0,
                default_headers=extra_headers,
            )
        )
        if owns_client:
            self._own_http_client = client

            def reopen() -> httpx2.AsyncClient:
                reopened = httpx2.AsyncClient(auth=auth, follow_redirects=False)
                auth.client = reopened
                return reopened

            self._http_client_factory = reopen

    @property
    def name(self) -> str:
        return "openai-chatgpt"

    @property
    def model_id_namespace(self) -> str:
        return "openai"

    @staticmethod
    def model_profile(model_name: str) -> ModelProfile:
        return merge_profile(
            openai_model_profile(model_name),
            OpenAIModelProfile(
                openai_responses_requires_streaming=True,
                openai_responses_requires_store_false=True,
                openai_supports_input_token_counting=False,
                openai_system_prompt_role="developer",
                openai_unsupported_model_settings=("temperature", "top_p", "max_tokens"),
                tool_addition_mode="with_definitions",
                tool_deferral_mode=None,
                supports_image_output=False,
                supported_native_tools=frozenset({WebSearchTool}),
            ),
        )


@dataclass(frozen=True, slots=True)
class ChatGPTModel:
    slug: str
    display_name: str


async def discover_chatgpt_models(
    *,
    credential_source: OpenAIChatGPTCredentialSource,
    http_client: httpx2.AsyncClient | None = None,
    extra_headers: dict[str, str] | None = None,
) -> tuple[ChatGPTModel, ...]:
    """Account-scoped catalog, preserving the server's visible order and slugs."""
    before = await credential_source.load()
    provider = OpenAIChatGPTProvider(
        credential_source=credential_source, http_client=http_client, extra_headers=extra_headers
    )
    async with provider:
        payload = await provider.client.get("/models", cast_to=dict)
    require_same_account(before, await credential_source.load())
    models = payload.get("models")
    if not isinstance(models, list):
        raise UserError("ChatGPT returned an invalid model catalog.")
    result: list[ChatGPTModel] = []
    for model in models:
        if not isinstance(model, dict) or model.get("visibility") != "list":
            continue
        slug, label = model.get("slug"), model.get("display_name")
        if not isinstance(slug, str) or not slug or not isinstance(label, str) or not label:
            raise UserError("ChatGPT returned an invalid model catalog entry.")
        result.append(ChatGPTModel(slug=slug, display_name=label))
    return tuple(result)
