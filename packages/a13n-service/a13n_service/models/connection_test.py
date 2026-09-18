"""Bounded connectivity check for one configured Model API."""

from __future__ import annotations

from collections.abc import Awaitable
from time import monotonic
from typing import cast

import httpx2
from a13n_harness.errors import ModelResolutionError
from a13n_harness.providers.model.definition import ProviderOperationError, ProviderOperationUnsupported
from anyio import fail_after
from pydantic_ai.exceptions import ModelAPIError, UnexpectedModelBehavior
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.settings import ModelSettings

from .domain import ModelConnectionTestResult, ModelExecutionSnapshot
from .model_factory import NativeModelFactory
from .provider_runtime import LiveProviderResolver
from .requests import LiveProviderModel
from .settings import JsonObject, validate_settings


class NativeModelConnectionTester:
    """Perform one minimal real model request and retain no Provider response."""

    def __init__(self, *, provider_resolver: LiveProviderResolver, model_factory: NativeModelFactory) -> None:
        self._provider_resolver = provider_resolver
        self._model_factory = model_factory

    async def __call__(
        self,
        *,
        snapshot: ModelExecutionSnapshot,
        settings: JsonObject,
        organization_id: str,
        workspace_id: str | None,
    ) -> None:
        model = await LiveProviderModel.create(
            snapshot=snapshot,
            organization_id=organization_id,
            workspace_id=workspace_id,
            provider_resolver=self._provider_resolver,
            model_factory=self._model_factory,
        )
        async with model:
            response = await model.request(
                [ModelRequest(parts=[UserPromptPart("Reply with OK.")])],
                cast(ModelSettings, validate_settings(snapshot.model_api, settings)),
                ModelRequestParameters(),
            )
        del response


async def test_connection(
    operation: Awaitable[None], *, timeout_seconds: float, subject: str
) -> ModelConnectionTestResult:
    started = monotonic()
    success, code, message = True, "connection_succeeded", f"The {subject} connection succeeded."
    try:
        with fail_after(timeout_seconds):
            await operation
    except ProviderOperationUnsupported:
        success, code, message = (
            False,
            "connection_test_unsupported",
            f"The {subject} has no supported connection test.",
        )
    except TimeoutError:
        success, code, message = False, "connection_timeout", f"The {subject} connection timed out."
    except (ModelResolutionError, ModelAPIError, UnexpectedModelBehavior, httpx2.HTTPError, ProviderOperationError):
        success, code, message = False, "connection_failed", f"The {subject} connection failed."
    return ModelConnectionTestResult(
        success=success,
        elapsed_ms=max(0, round((monotonic() - started) * 1000)),
        code=code,
        message=message,
    )
