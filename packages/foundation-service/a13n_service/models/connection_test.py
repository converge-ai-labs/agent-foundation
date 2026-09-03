"""Bounded connectivity check for one configured Model API."""

from __future__ import annotations

from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters

from .domain import ModelExecutionSnapshot
from .model_factory import NativeModelFactory
from .provider_runtime import LiveProviderResolver


class NativeModelConnectionTester:
    """Perform one minimal real model request and retain no Provider response."""

    def __init__(self, *, provider_resolver: LiveProviderResolver, model_factory: NativeModelFactory) -> None:
        self._provider_resolver = provider_resolver
        self._model_factory = model_factory

    async def __call__(
        self,
        *,
        snapshot: ModelExecutionSnapshot,
        organization_id: str,
        workspace_id: str,
    ) -> None:
        provider = await self._provider_resolver.resolve(
            organization_id=organization_id,
            workspace_id=workspace_id,
            snapshot=snapshot,
        )
        model = self._model_factory.build(snapshot, provider)
        async with model:
            response = await model.request(
                [ModelRequest(parts=[UserPromptPart("Reply with OK.")])],
                {"max_tokens": 1},
                ModelRequestParameters(),
            )
        del response
