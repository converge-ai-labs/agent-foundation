"""Bounded provider authentication and connectivity checks for unsaved candidates."""

from __future__ import annotations

import httpx2
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters

from a13n_service.iam.authorization import AuthenticatedActor

from .domain import ModelCredential, ModelExecutionSnapshot
from .endpoint_policy import EndpointPolicy
from .providers import ValidatedProviderSelection
from .runtime import NativeModelFactory, RuntimeSecretValueResolver, effective_execution_endpoint

_CANDIDATE_MODEL_ID = "mdl_connectiontest000000000000"


class NativeModelConnectionTester:
    """Perform one minimal real model request and retain no provider response."""

    def __init__(
        self,
        *,
        secret_resolver: RuntimeSecretValueResolver,
        endpoint_policy: EndpointPolicy,
        http_client: httpx2.AsyncClient,
    ) -> None:
        self._secret_resolver = secret_resolver
        self._endpoint_policy = endpoint_policy
        self._model_factory = NativeModelFactory(http_client)

    async def __call__(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        provider_type: str,
        model_name: str,
        credential: ModelCredential,
        selection: ValidatedProviderSelection,
    ) -> None:
        snapshot = ModelExecutionSnapshot(
            model_id=_CANDIDATE_MODEL_ID,
            provider_type=provider_type,
            model_name=model_name,
            base_url=selection.base_url,
            credential=credential,
            provider_config=selection.provider_config,
            adapter_key=selection.adapter_key,
            adapter_version=selection.adapter_version,
        )
        endpoint = effective_execution_endpoint(snapshot)
        if endpoint is not None:
            await self._endpoint_policy.validate(endpoint, resolve_dns=True)
        credential_value = await self._secret_resolver.resolve(
            principal=actor.principal,
            organization_id=organization_id,
            workspace_id=workspace_id,
            credential=credential,
        )
        model = self._model_factory.build(snapshot, credential_value)
        response = await model.request(
            [ModelRequest(parts=[UserPromptPart("Reply with OK.")])],
            {"max_tokens": 1},
            ModelRequestParameters(),
        )
        del response
