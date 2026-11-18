"""Codex subscription metadata used when authoring a Model."""

from __future__ import annotations

import json
from typing import Annotated

import httpx2
from a13n_harness.http import outbound_tls_verify
from pydantic import BaseModel, Field
from pydantic_ai.providers.openai_codex import OpenAICodexCredentialSource, OpenAICodexProvider

type CodexEffort = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")]


class CodexCatalogModel(BaseModel):
    slug: str = Field(pattern=r"^[^\s:]+$", max_length=480)
    display_name: str = Field(min_length=1, max_length=256)
    reasoning_efforts: tuple[CodexEffort, ...]
    default_effort: CodexEffort | None = None
    context_window: int | None = Field(default=None, gt=0)
    image_input: bool = False


def parse_models(payload: dict) -> tuple[CodexCatalogModel, ...]:
    models = []
    for row in payload["models"]:
        if row.get("visibility") != "list":
            continue
        efforts = tuple(dict.fromkeys(item["effort"] for item in row.get("supported_reasoning_levels", ())))
        default = row.get("default_reasoning_level")
        models.append(
            CodexCatalogModel(
                slug=row["slug"],
                display_name=row["display_name"],
                reasoning_efforts=efforts,
                default_effort=default if default in efforts else None,
                context_window=row.get("context_window"),
                image_input="image" in row.get("input_modalities", ()),
            )
        )
    return tuple(models)


async def discover_codex_models(source: OpenAICodexCredentialSource) -> tuple[CodexCatalogModel, ...]:
    async with httpx2.AsyncClient(verify=outbound_tls_verify(), timeout=6) as client:
        provider = OpenAICodexProvider(credential_source=source, http_client=client)
        # The Codex catalog protocol requires a supported client version.
        async with client.stream(
            "GET", provider.base_url + "/models", params={"client_version": "0.169.0"}
        ) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > 8 * 1024 * 1024:
                    raise ValueError("Codex model catalog exceeds its response limit.")
                body.extend(chunk)
        return parse_models(json.loads(body))
