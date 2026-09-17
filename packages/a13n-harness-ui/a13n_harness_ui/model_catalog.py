"""Public model directory for authoring only; never part of Run composition."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from time import monotonic
from typing import Literal

import httpx2
from anyio import Lock, fail_after
from pydantic import Field

from a13n_harness_ui.configuration.models import StrictModel
from a13n_harness_ui.model_authoring import model_connections

_DIRECTORY_URL = "https://models.dev/catalog.json"
_MAX_BYTES = 8 * 1024 * 1024
_CHANNELS = {
    "openai": ("openai-responses", "openai-chat"),
    "anthropic": ("anthropic",),
    "google": ("google",),
    "openrouter": ("openrouter",),
    "deepseek": ("deepseek",),
    "zai": ("zai",),
    "z-ai": ("zai",),
    "moonshotai": ("moonshotai",),
    "groq": ("groq",),
    "mistral": ("mistral",),
    "togetherai": ("together",),
    "together": ("together",),
    "fireworks-ai": ("fireworks",),
    "fireworks": ("fireworks",),
    "xai": ("grok", "xai"),
    "x-ai": ("grok", "xai"),
}


class CatalogModel(StrictModel):
    connection: str
    model_id: str
    name: str
    recommended: bool = False
    source: Literal["bundled", "directory"] = "bundled"
    released: date | None = None
    context_window: int | None = None
    input_modalities: tuple[str, ...] = ()
    supports_tools: bool | None = None


def bundled_models() -> tuple[CatalogModel, ...]:
    return tuple(
        CatalogModel(connection=connection.id, model_id=model.value, name=model.label, recommended=True)
        for connection in model_connections()
        for model in connection.models
    )


class ModelCatalogSnapshot(StrictModel):
    items: tuple[CatalogModel, ...] = Field(default_factory=bundled_models)
    status: Literal["bundled", "ready", "stale", "unavailable"] = "bundled"
    updated_at: datetime | None = None


def parse_directory(payload: object) -> tuple[CatalogModel, ...]:
    if not isinstance(payload, dict) or not isinstance(payload.get("providers"), dict):
        raise ValueError("Model directory must contain providers")
    entries: dict[tuple[str, str], CatalogModel] = {}
    for channel, provider in payload["providers"].items():
        if channel not in _CHANNELS or not isinstance(provider, dict):
            continue
        models = provider.get("models")
        if not isinstance(models, dict):
            continue
        for key, model in models.items():
            if not isinstance(model, dict):
                continue
            modalities = model.get("modalities")
            if not isinstance(modalities, dict):
                continue
            outputs = modalities.get("output")
            if not isinstance(outputs, list) or "text" not in outputs:
                continue
            model_id = model.get("id", key)
            name = model.get("name", model_id)
            if (
                not isinstance(model_id, str)
                or not model_id
                or len(model_id) > 480
                or any(char.isspace() for char in model_id)
            ):
                continue
            if not isinstance(name, str):
                name = model_id
            try:
                released = date.fromisoformat(model.get("release_date", ""))
            except (TypeError, ValueError):
                released = None
            limits = model.get("limit")
            context = limits.get("context") if isinstance(limits, dict) else None
            inputs = modalities.get("input")
            tool_call = model.get("tool_call")
            for connection in _CHANNELS[channel]:
                entries[connection, model_id] = CatalogModel(
                    connection=connection,
                    model_id=model_id,
                    name=name[:256],
                    source="directory",
                    released=released,
                    context_window=context
                    if isinstance(context, int) and not isinstance(context, bool) and context > 0
                    else None,
                    input_modalities=tuple(item for item in inputs if isinstance(item, str))
                    if isinstance(inputs, list)
                    else (),
                    supports_tools=tool_call if isinstance(tool_call, bool) else None,
                )
            if len(entries) > 50000:
                raise ValueError("Model directory exceeds its entry limit")
    if not entries:
        raise ValueError("Model directory contains no supported text models")
    # Recommended subscription entries are always release-owned. In particular,
    # xAI directory rows cannot add models to the Grok subscription connection.
    for bundled in bundled_models():
        key = bundled.connection, bundled.model_id
        entries[key] = entries[key].model_copy(update={"recommended": True}) if key in entries else bundled
    return tuple(
        sorted(
            entries.values(),
            key=lambda item: (
                item.connection,
                not item.recommended,
                -(item.released or date.min).toordinal(),
                item.name,
                item.model_id,
            ),
        )
    )


async def fetch_directory() -> tuple[CatalogModel, ...]:
    async with httpx2.AsyncClient(timeout=5) as client:
        async with client.stream("GET", _DIRECTORY_URL) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > _MAX_BYTES:
                    raise ValueError("Model directory exceeds its response limit")
                body.extend(chunk)
    return parse_directory(json.loads(body))


class ModelCatalog:
    """App-local last-good cache, refreshed lazily by authoring surfaces."""

    def __init__(self) -> None:
        self._snapshot = ModelCatalogSnapshot()
        self._refresh_after = 0.0
        self._lock = Lock()

    async def read(self) -> ModelCatalogSnapshot:
        if monotonic() < self._refresh_after:
            return self._snapshot
        async with self._lock:
            if monotonic() < self._refresh_after:
                return self._snapshot
            try:
                with fail_after(6):
                    items = await fetch_directory()
            except (TimeoutError, httpx2.HTTPError, ValueError, TypeError):
                self._snapshot = self._snapshot.model_copy(
                    update={"status": "stale" if self._snapshot.updated_at else "unavailable"}
                )
                self._refresh_after = monotonic() + 60
            else:
                self._snapshot = ModelCatalogSnapshot(items=items, status="ready", updated_at=datetime.now(UTC))
                self._refresh_after = monotonic() + 3600
            return self._snapshot
