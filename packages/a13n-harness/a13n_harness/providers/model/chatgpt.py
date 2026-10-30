"""Account-scoped model discovery using Pydantic AI's ChatGPT provider."""

from dataclasses import dataclass

import httpx2
from pydantic_ai.exceptions import UserError
from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTCredentialSource, OpenAIChatGPTProvider


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
    """Preserve the account catalog's visible order and slugs."""
    provider = OpenAIChatGPTProvider(credential_source=credential_source, http_client=http_client)
    async with provider:
        payload = await provider.client.get("/models", cast_to=dict, options={"headers": extra_headers or {}})
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
