"""Bounded HTTP transport; no automatic mutation retries or absence inference."""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

import httpx2
from pydantic import BaseModel, JsonValue, ValidationError

from ..errors import EnvironmentProviderErrorCategory as Category
from ..errors import EnvironmentProviderOutcomeCertainty as Certainty
from .errors import failure


class NativeHTTP:
    def __init__(self, key: str, url: str, token: str, timeout: float, *, params: dict[str, str] | None = None):
        self.key = key
        self.client = httpx2.AsyncClient(
            base_url=url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            params=params,
            follow_redirects=False,
        )

    def check(self, response: httpx2.Response, *, mutation: bool, missing: bool = False) -> None:
        status = response.status_code
        if 200 <= status < 300 or (missing and status == 404):
            return
        if status in {401, 403}:
            raise failure(self.key, "provider_denied", Category.DENIED, certainty=Certainty.KNOWN)
        if status in {400, 422}:
            raise failure(self.key, "provider_spec_invalid", Category.INVALID, certainty=Certainty.KNOWN)
        if status == 409:
            raise failure(self.key, "provider_target_conflict", Category.CONFLICT, certainty=Certainty.KNOWN)
        if status == 404:
            raise failure(self.key, "provider_target_missing", Category.MISSING, certainty=Certainty.KNOWN)
        uncertain = mutation and status >= 500
        raise failure(
            self.key,
            "provider_unknown_outcome" if uncertain else "provider_unavailable",
            Category.UNKNOWN_OUTCOME if uncertain else Category.UNAVAILABLE,
            certainty=Certainty.UNKNOWN if uncertain else Certainty.KNOWN,
        )

    @asynccontextmanager
    async def stream(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, object] | None = None,
        params: dict[str, str] | None = None,
        missing: bool = False,
        headers: dict[str, str] | None = None,
    ) -> AsyncIterator[httpx2.Response]:
        mutation = is_mutation(method, params)
        try:
            async with self.client.stream(method, path, json=body, params=params, headers=headers) as response:
                self.check(response, mutation=mutation, missing=missing)
                yield response
        except (ValueError, TypeError, KeyError, AttributeError):
            raise response_invalid(self.key, mutation=mutation) from None
        except httpx2.HTTPError:
            raise failure(
                self.key,
                "provider_unknown_outcome" if mutation else "provider_unavailable",
                Category.UNKNOWN_OUTCOME if mutation else Category.UNAVAILABLE,
                certainty=Certainty.UNKNOWN if mutation else Certainty.KNOWN,
            ) from None

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, object] | None = None,
        params: dict[str, str] | None = None,
        missing: bool = False,
        headers: dict[str, str] | None = None,
    ) -> JsonValue:
        async with self.stream(method, path, body=body, params=params, missing=missing, headers=headers) as response:
            if response.status_code == 404:
                return None
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 32 * 1024 * 1024:
                    raise response_invalid(self.key, mutation=is_mutation(method, params))
            if not data:
                return None
            from pydantic import TypeAdapter

            try:
                return TypeAdapter(JsonValue).validate_json(data)
            except ValueError:
                raise response_invalid(self.key, mutation=is_mutation(method, params)) from None

    async def close(self) -> None:
        await self.client.aclose()


def response_invalid(key: str, *, mutation: bool):
    return failure(
        key,
        "provider_response_invalid",
        Category.UNKNOWN_OUTCOME if mutation else Category.UNAVAILABLE,
        certainty=Certainty.UNKNOWN if mutation else Certainty.KNOWN,
    )


def decode_response[M: BaseModel](model: type[M], raw: JsonValue, key: str, *, mutation: bool = False) -> M:
    try:
        return model.model_validate(raw)
    except ValidationError:
        raise response_invalid(key, mutation=mutation) from None


def is_mutation(method: str, params: dict[str, str] | None) -> bool:
    return method != "GET" or bool(params and params.get("resume") == "true")
