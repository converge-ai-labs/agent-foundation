"""Model definitions compose typed connections with native Pydantic AI APIs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Literal

import httpx2
from anyio import fail_after, move_on_after, to_thread
from pydantic import BaseModel, TypeAdapter

from ...http import EndpointValidator, ProviderHttpError, bounded_response_body
from ..definition import ProviderDefinition
from ..endpoint_policy import EndpointPolicy
from .apis import MODEL_APIS
from .credentials import ApiKeyCredential
from .headers import ExtraHeaders, validate_header_names
from .types import ModelConnection, ProviderConfiguration, ValidatedProviderConfiguration

if TYPE_CHECKING:
    from pydantic_ai.models import Model
    from pydantic_ai.providers import Provider

    from .oauth.chatgpt import OpenAIChatGPTCredentialSource


_PROBE_TIMEOUT_SECONDS = 10
_PROBE_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class ProviderOperationError(ValueError):
    """A bounded, safe provider-operation failure."""


@dataclass(frozen=True, slots=True)
class ConnectionProbeRequest:
    url: str
    headers: Mapping[str, str]


type NativeProviderBuilder[C: ProviderConfiguration, K: BaseModel] = Callable[
    [ModelConnection[C, K], httpx2.AsyncClient, str], Provider[Any]
]


@dataclass(frozen=True, slots=True)
class DiscoveredModel:
    """An upstream model choice, not public catalog metadata or a saved Model."""

    model_name: str
    display_name: str


type ModelDiscovery[C: ProviderConfiguration, K: BaseModel] = Callable[
    [ModelConnection[C, K], OpenAIChatGPTCredentialSource | None, httpx2.AsyncClient],
    Awaitable[tuple[DiscoveredModel, ...]],
]
type EndpointResolver = Callable[[Mapping[str, object]], str | None]
type NativeModelBuilder = Callable[[str, Provider[Any]], Model[Any]]


@dataclass(frozen=True, slots=True)
class ModelOAuth:
    """A runtime-source capability, separate from user-supplied static credentials."""

    scheme: Literal["openai-chatgpt"]
    build_provider: Callable[[OpenAIChatGPTCredentialSource, httpx2.AsyncClient, dict[str, str]], Provider[Any]]


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelProviderDefinition[C: ProviderConfiguration, K: BaseModel](ProviderDefinition[C, K]):
    DOMAIN: ClassVar[str] = "Model"

    supported_model_apis: tuple[str, ...]
    build_provider: NativeProviderBuilder[C, K] | None = None
    oauth: ModelOAuth | None = None
    build_model: NativeModelBuilder | None = None
    endpoint: str | EndpointResolver | None = None
    connection_probe: Callable[[ModelConnection[C, K]], ConnectionProbeRequest] | None = None
    model_discovery: ModelDiscovery[C, K] | None = None
    reserved_headers: tuple[str, ...] = ("authorization",)
    additional_endpoint_fields: tuple[str, ...] = ()
    # Public model-directory channels that publish this Provider's own model names.
    catalog_providers: tuple[str, ...] = ()

    def validate_domain(self) -> None:
        if not self.supported_model_apis or set(self.supported_model_apis) - MODEL_APIS.keys():
            raise ValueError("Model Provider must declare supported native calling APIs")
        if (self.build_provider is None) == (self.oauth is None):
            raise ValueError("Model Provider must declare exactly one static or OAuth constructor")
        # OAuth access tokens always come from the Host source. A definition may
        # separately accept a server-side client-authentication credential.

    @property
    def supports_connection_probe(self) -> bool:
        return self.connection_probe is not None

    @property
    def supports_model_discovery(self) -> bool:
        return self.model_discovery is not None

    async def discover_models(
        self,
        *,
        configuration: Mapping[str, object],
        credential: object = None,
        credential_source: OpenAIChatGPTCredentialSource | None = None,
        http_client: httpx2.AsyncClient,
        extra_headers: Mapping[str, str] | None = None,
        endpoint_policy: EndpointValidator | None = None,
    ) -> tuple[DiscoveredModel, ...]:
        """Discover current upstream choices using Host-owned credentials and HTTP bounds."""
        if self.model_discovery is None:
            raise ProviderOperationError("Model discovery is not supported by this Provider")
        connection = self.bind(configuration, credential, extra_headers=extra_headers)
        if (self.oauth is not None) != (credential_source is not None):
            raise ProviderOperationError("Model discovery requires the Provider's authentication source")
        if connection.endpoint is not None:
            await (endpoint_policy or EndpointPolicy()).validate(connection.endpoint)
        return await self.model_discovery(connection, credential_source, http_client)

    def validate_configuration(
        self, configuration: Mapping[str, object], *, credential_configured: bool, header_names: Sequence[str] = ()
    ) -> ValidatedProviderConfiguration:
        parsed = self.configuration_model.model_validate(configuration)
        self.authentication.validate_presence(parsed, credential_configured)
        reserved = (*self.reserved_headers, *parsed.authentication_headers)
        affinity_header = parsed.session_affinity_header
        if affinity_header is not None:
            validate_header_names((affinity_header,), reserved=reserved)
            reserved = (*reserved, affinity_header)
        validate_header_names(header_names, reserved=reserved)
        normalized = parsed.model_dump(mode="json", by_alias=True, exclude_none=False, exclude_defaults=True)
        endpoint = parsed.base_url or (self.endpoint(normalized) if callable(self.endpoint) else self.endpoint)
        return ValidatedProviderConfiguration(configuration=normalized, endpoint=endpoint)

    def bind(
        self,
        configuration: Mapping[str, object],
        credential: object = None,
        *,
        extra_headers: Mapping[str, str] | None = None,
    ) -> ModelConnection[C, K]:
        headers = TypeAdapter(ExtraHeaders).validate_python(extra_headers or {})
        validated = self.validate_configuration(
            configuration, credential_configured=credential is not None, header_names=tuple(headers)
        )
        parsed = self.configuration_model.model_validate(validated.configuration)
        return ModelConnection(
            self.type, parsed, validated.endpoint, self.parse_credential(parsed, credential), headers
        )

    async def probe(
        self,
        connection: ModelConnection[C, K],
        *,
        http_client: httpx2.AsyncClient,
        endpoint_policy: EndpointValidator | None = None,
    ) -> None:
        """Test this connection without exposing or retaining the upstream response."""
        if self.connection_probe is None:
            raise ProviderOperationError("Test a saved Model to verify this connection")
        try:
            with fail_after(_PROBE_TIMEOUT_SECONDS):
                request = self.connection_probe(connection)
                await (endpoint_policy or EndpointPolicy()).validate(request.url)
                async with http_client.stream(
                    "GET",
                    request.url,
                    headers={**connection.extra_headers, **request.headers},
                    timeout=_PROBE_TIMEOUT_SECONDS,
                    follow_redirects=False,
                ) as response:
                    response.raise_for_status()
                    await bounded_response_body(response, max_bytes=_PROBE_MAX_RESPONSE_BYTES)
        except TimeoutError:
            raise ProviderOperationError("Provider connection probe timed out") from None
        except ProviderHttpError as error:
            raise ProviderOperationError("Provider probe response exceeds its size limit") from error
        except httpx2.HTTPError as error:
            raise ProviderOperationError("Provider connection probe failed") from error

    async def build(
        self,
        model_name: str,
        *,
        configuration: Mapping[str, object],
        credential: object = None,
        credential_source: OpenAIChatGPTCredentialSource | None = None,
        model_api: str | None = None,
        http_client: httpx2.AsyncClient,
        extra_headers: Mapping[str, str] | None = None,
        endpoint_policy: EndpointValidator | None = None,
    ) -> Model[Any]:
        """Return a native Model. The caller owns the supplied HTTP client and enters the Model."""
        from ...models.configuration import configured_model

        api = model_api or self.supported_model_apis[0]
        if api not in self.supported_model_apis:
            raise ValueError("the Model API is not supported by this Provider")
        connection = self.bind(configuration, credential, extra_headers=extra_headers)
        policy = endpoint_policy or EndpointPolicy()
        if (
            api == "bedrock.converse"
            and isinstance(policy, EndpointPolicy)
            and policy.configuration.allowed_hosts is not None
        ):
            raise ValueError("Bedrock Converse SDK transport cannot enforce Run allowed hosts")
        if connection.endpoint is not None:
            await policy.validate(connection.endpoint)
        for name in self.additional_endpoint_fields:
            endpoint = getattr(connection.configuration, name)
            if endpoint is not None:
                await policy.validate(endpoint)
        if self.oauth is not None:
            if credential_source is None:
                raise ValueError("the OAuth Model Provider requires an authorized Host credential source")
            native = self.oauth.build_provider(credential_source, http_client, connection.extra_headers)
        else:
            if credential_source is not None:
                raise ValueError("the Model Provider does not accept an OAuth credential source")
            assert self.build_provider is not None
            native = await to_thread.run_sync(self.build_provider, connection, http_client, api)
        try:
            await policy.validate(str(native.base_url))
            model = (
                self.build_model(model_name, native)
                if self.build_model is not None
                else MODEL_APIS[api].build(model_name, native)
            )
            return configured_model(model, policy.configuration) if isinstance(policy, EndpointPolicy) else model
        except BaseException as error:
            with move_on_after(5, shield=True):
                await native.__aenter__()
                await native.__aexit__(type(error), error, error.__traceback__)
            raise


def join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def require_endpoint(provider: ModelConnection) -> str:
    if provider.endpoint is None:
        raise ValueError("the Model Provider endpoint is missing")
    return provider.endpoint


def require_credential(provider: ModelConnection[Any, ApiKeyCredential]) -> str:
    if provider.credential is None:
        raise ValueError("the Model Provider credential is unavailable")
    return provider.credential.api_key.get_secret_value()


def bearer_models_request(provider: ModelConnection[Any, ApiKeyCredential]) -> ConnectionProbeRequest:
    return ConnectionProbeRequest(
        url=join_url(require_endpoint(provider), "models"),
        headers={"authorization": f"Bearer {require_credential(provider)}"},
    )
