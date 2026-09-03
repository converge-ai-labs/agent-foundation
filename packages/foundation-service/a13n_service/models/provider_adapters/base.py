"""Shared contracts and mechanics for Provider adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, NoReturn, Protocol, cast

import httpx2
from a13n_harness.errors import ModelResolutionError
from openai import AsyncOpenAI
from pydantic_ai.models import Model as PydanticModel

from ..domain import ModelExecutionSnapshot
from .types import RuntimeProvider

DiscoveredModelIdentity = tuple[str, str | None]
BuiltModel = PydanticModel[Any]
ModelBuilder = Callable[[ModelExecutionSnapshot, RuntimeProvider, httpx2.AsyncClient], BuiltModel]


@dataclass(frozen=True, slots=True)
class ModelListRequest:
    url: str
    headers: Mapping[str, str]


class ModelDiscoveryAdapter(Protocol):
    def request(self, provider: RuntimeProvider) -> ModelListRequest: ...

    def parse(self, payload: Any) -> list[DiscoveredModelIdentity]: ...


@dataclass(frozen=True, slots=True)
class ModelListSchema:
    collection_field: str
    identifier_field: str
    display_name_fields: tuple[str, ...] = ()
    identifier_prefix: str = ""


@dataclass(frozen=True, slots=True)
class JsonModelDiscoveryAdapter:
    """Describe one Provider's bounded JSON model-list operation."""

    request_builder: Callable[[RuntimeProvider], ModelListRequest]
    schema: ModelListSchema

    def request(self, provider: RuntimeProvider) -> ModelListRequest:
        return self.request_builder(provider)

    def parse(self, payload: Any) -> list[DiscoveredModelIdentity]:
        if not isinstance(payload, Mapping):
            raise ValueError("the Provider model-list response is invalid")
        values = payload.get(self.schema.collection_field)
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise ValueError("the Provider model-list response is invalid")

        parsed: list[DiscoveredModelIdentity] = []
        for value in values:
            if not isinstance(value, Mapping):
                continue
            raw_id = value.get(self.schema.identifier_field)
            if not isinstance(raw_id, str):
                continue
            model_id = raw_id.removeprefix(self.schema.identifier_prefix).strip()
            if not 1 <= len(model_id) <= 256:
                continue
            parsed.append((model_id, _display_name(value, self.schema.display_name_fields, model_id)))
        return parsed


@dataclass(frozen=True, slots=True)
class ProviderAdapter:
    build_model: ModelBuilder
    model_discovery: ModelDiscoveryAdapter | None = None


def openai_style_discovery(
    request_builder: Callable[[RuntimeProvider], ModelListRequest],
) -> JsonModelDiscoveryAdapter:
    return JsonModelDiscoveryAdapter(
        request_builder=request_builder,
        schema=ModelListSchema(
            collection_field="data",
            identifier_field="id",
            display_name_fields=("name",),
        ),
    )


def bearer_models_request(provider: RuntimeProvider) -> ModelListRequest:
    return ModelListRequest(
        url=join_url(require_endpoint(provider), "models"),
        headers={"authorization": f"Bearer {require_credential(provider)}"},
    )


def openai_client(provider: RuntimeProvider, http_client: httpx2.AsyncClient) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=require_credential(provider),
        base_url=require_endpoint(provider),
        http_client=http_client,
    )


def model_name(snapshot: ModelExecutionSnapshot) -> Any:
    return cast(Any, snapshot.upstream_model)


def require_api(snapshot: ModelExecutionSnapshot, expected: str) -> None:
    if snapshot.model_api != expected:
        unsupported_model_api(snapshot)


def unsupported_model_api(snapshot: ModelExecutionSnapshot) -> NoReturn:
    raise ModelResolutionError(
        "The accepted Model API is unavailable.",
        code="model_api_unavailable",
        details={"model_api": snapshot.model_api},
    )


def join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def require_endpoint(provider: RuntimeProvider) -> str:
    if provider.endpoint is None:
        raise ValueError("the Model Provider endpoint is missing")
    return provider.endpoint


def require_credential(provider: RuntimeProvider) -> str:
    if not provider.credential:
        raise ModelResolutionError(
            "The Model Provider credential is unavailable.",
            code="model_provider_credential_unavailable",
            details={"provider_type": provider.type},
        )
    return provider.credential


def _display_name(value: Mapping[object, object], fields: tuple[str, ...], model_id: str) -> str | None:
    for field in fields:
        raw_display_name = value.get(field)
        if isinstance(raw_display_name, str):
            display_name = raw_display_name.strip()
            if display_name != model_id and 1 <= len(display_name) <= 128:
                return display_name
    return None
