"""Model requests refresh live Providers before each bounded outbound attempt."""

from __future__ import annotations

import math
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from random import uniform
from typing import Any, cast

from a13n_logging import get_logger
from anyio import current_time, fail_after, sleep
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model as PydanticModel
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from .domain import ModelExecutionSnapshot
from .model_factory import NativeModelFactory
from .provider_runtime import LiveProviderResolver
from .settings import JsonObject, validate_settings

logger = get_logger(__name__)

_MAX_ATTEMPTS = 3
_DEFAULT_REQUEST_TIMEOUT_SECONDS = 600


class LiveProviderModel(WrapperModel):
    """Preserve Model identity while refreshing its Provider for every outbound call."""

    def __init__(
        self,
        *,
        initial: PydanticModel[Any],
        snapshot: ModelExecutionSnapshot,
        organization_id: str,
        workspace_id: str | None,
        provider_resolver: LiveProviderResolver,
        model_factory: NativeModelFactory,
        harness_thread_id: str | None = None,
    ) -> None:
        super().__init__(initial)
        self._snapshot = snapshot
        self._organization_id = organization_id
        self._workspace_id = workspace_id
        self._provider_resolver = provider_resolver
        self._model_factory = model_factory
        self._harness_thread_id = harness_thread_id

    async def __aenter__(self) -> LiveProviderModel:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def _fresh(self) -> PydanticModel[Any]:
        provider = await self._provider_resolver.resolve(
            organization_id=self._organization_id,
            workspace_id=self._workspace_id,
            snapshot=self._snapshot,
        )
        return await self._model_factory.build(self._snapshot, provider)

    @classmethod
    async def create(
        cls,
        *,
        snapshot: ModelExecutionSnapshot,
        organization_id: str,
        workspace_id: str | None,
        provider_resolver: LiveProviderResolver,
        model_factory: NativeModelFactory,
        harness_thread_id: str | None = None,
    ) -> LiveProviderModel:
        provider = await provider_resolver.resolve(
            organization_id=organization_id, workspace_id=workspace_id, snapshot=snapshot
        )
        initial = await model_factory.build(snapshot, provider)
        # The prototype supplies native identity/profile to Harness. Each request
        # opens its own current connection; do not retain prototype-owned clients.
        async with initial:
            return cls(
                initial=initial,
                snapshot=snapshot,
                organization_id=organization_id,
                workspace_id=workspace_id,
                provider_resolver=provider_resolver,
                model_factory=model_factory,
                harness_thread_id=harness_thread_id,
            )

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        self._validate_request_settings(model_settings)
        with fail_after(_request_timeout(model_settings)):
            for attempt in range(_MAX_ATTEMPTS):
                model = await self._fresh()
                try:
                    async with model:
                        return await model.request(messages, model_settings, model_request_parameters)
                except ModelHTTPError as error:
                    await _wait_to_retry(error, attempt)
        raise AssertionError("request attempts exhausted without a result or error")

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any = None,
    ) -> AsyncIterator[StreamedResponse]:
        self._validate_request_settings(model_settings)
        deadline = current_time() + _request_timeout(model_settings)
        for attempt in range(_MAX_ATTEMPTS):
            handed_off = False
            try:
                async with AsyncExitStack() as stack:
                    # Pydantic AI can consume and close a stream in different tasks.
                    # Never keep an AnyIO cancel scope open across the handoff.
                    with fail_after(max(0, deadline - current_time())):
                        model = await self._fresh()
                        await stack.enter_async_context(model)
                        stream = await stack.enter_async_context(
                            model.request_stream(messages, model_settings, model_request_parameters, run_context)
                        )
                    handed_off = True
                    yield stream
                    return
            except ModelHTTPError as error:
                if handed_off:
                    raise
                with fail_after(max(0, deadline - current_time())):
                    await _wait_to_retry(error, attempt)

    def _validate_request_settings(self, settings: ModelSettings | None) -> None:
        value = dict(settings or {})
        if self._harness_thread_id is not None and value.get("openai_prompt_cache_key") == self._harness_thread_id:
            del value["openai_prompt_cache_key"]
        headers = value.get("extra_headers")
        if isinstance(headers, dict) and self._harness_thread_id is not None:
            headers = dict(headers)
            if headers.get("x-session-id") == self._harness_thread_id:
                del headers["x-session-id"]
            value["extra_headers"] = headers
        # Accepted caller settings retain the strict schema. Only Harness's exact
        # fresh Thread correlation is permitted in addition at the outbound boundary.
        validate_settings(self._snapshot.model_api, cast(JsonObject, value))


def _request_timeout(settings: ModelSettings | None) -> float:
    return cast(float, (settings or {}).get("timeout", _DEFAULT_REQUEST_TIMEOUT_SECONDS))


async def _wait_to_retry(error: ModelHTTPError, attempt: int) -> None:
    # Explicit rejection can be retried; timeouts and transport failures have an
    # uncertain outcome. A stream already handed to Harness is never replayed.
    if error.status_code not in {429, 503} or attempt + 1 >= _MAX_ATTEMPTS:
        raise error
    delay = error.retry_after
    if delay is None:
        delay = uniform(0.5, 0.75) * 2**attempt
    if not math.isfinite(delay) or delay > 30:
        raise error
    logger.info(
        "model_request_retry", extra={"status_code": error.status_code, "attempt": attempt + 2, "delay_seconds": delay}
    )
    await sleep(delay)
