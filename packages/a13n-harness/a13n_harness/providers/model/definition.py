"""Model definitions compose typed connections with native Pydantic AI APIs."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

import httpx2
from anyio import fail_after, move_on_after, to_thread
from pydantic import BaseModel, TypeAdapter

from ..definition import ProviderDefinition
from ..endpoint_policy import EndpointPolicy
from ..http import EndpointValidator
from .apis import MODEL_APIS
from .credentials import ApiKeyCredential
from .headers import ExtraHeaders, validate_header_names
from .types import ModelConnection, ProviderConfiguration, ValidatedProviderConfiguration

if TYPE_CHECKING:
    from pydantic_ai.models import Model
    from pydantic_ai.profiles import ModelProfileSpec
    from pydantic_ai.providers import Provider


_PROBE_TIMEOUT_SECONDS = 10


class ProviderOperationError(ValueError):
    """A bounded, safe connection-probe failure."""


class ProviderOperationUnsupported(ProviderOperationError):
    """This provider has no native connection probe."""


@dataclass(frozen=True, slots=True)
class ConnectionProbeRequest:
    url: str
    headers: Mapping[str, str]


type NativeProviderBuilder[C: ProviderConfiguration, K: BaseModel] = Callable[
    [ModelConnection[C, K], httpx2.AsyncClient, str], Provider[Any]
]
type EndpointResolver = Callable[[Mapping[str, object]], str | None]


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelProviderDefinition[C: ProviderConfiguration, K: BaseModel](ProviderDefinition[C, K]):
    DOMAIN: ClassVar[str] = "Model"

    supported_model_apis: tuple[str, ...]
    build_provider: NativeProviderBuilder[C, K]
    endpoint: str | EndpointResolver | None = None
    endpoint_configuration_field: str | None = None
    connection_probe: Callable[[ModelConnection[C, K]], ConnectionProbeRequest] | None = None
    reserved_headers: tuple[str, ...] = ("authorization",)
    additional_endpoint_fields: tuple[str, ...] = ()

    def validate_domain(self) -> None:
        if not self.supported_model_apis or set(self.supported_model_apis) - MODEL_APIS.keys():
            raise ValueError("Model Provider must declare supported native calling APIs")

    @property
    def supports_connection_probe(self) -> bool:
        return self.connection_probe is not None

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

    def with_validated_endpoint(
        self, validated: ValidatedProviderConfiguration, endpoint: str
    ) -> ValidatedProviderConfiguration:
        normalized = dict(validated.configuration)
        if "base_url" in normalized:
            normalized["base_url"] = endpoint
        elif self.endpoint_configuration_field is not None:
            normalized[self.endpoint_configuration_field] = endpoint
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
            raise ProviderOperationUnsupported("Test a saved Model to verify this connection")
        try:
            with fail_after(_PROBE_TIMEOUT_SECONDS):
                request = self.connection_probe(connection)
                await (endpoint_policy or EndpointPolicy()).validate(request.url, resolve_dns=True)
                async with http_client.stream(
                    "GET",
                    request.url,
                    headers={**connection.extra_headers, **request.headers},
                    timeout=_PROBE_TIMEOUT_SECONDS,
                    follow_redirects=False,
                ) as response:
                    response.raise_for_status()
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 4 * 1024 * 1024:
                            raise ProviderOperationError("Provider probe response exceeds its size limit")
        except TimeoutError:
            raise ProviderOperationError("Provider connection probe timed out") from None
        except httpx2.HTTPError as error:
            raise ProviderOperationError("Provider connection probe failed") from error

    async def build(
        self,
        model_name: str,
        *,
        configuration: Mapping[str, object],
        credential: object = None,
        model_api: str | None = None,
        http_client: httpx2.AsyncClient,
        extra_headers: Mapping[str, str] | None = None,
        endpoint_policy: EndpointValidator | None = None,
        profile: ModelProfileSpec | None = None,
        profile_resolver: Callable[[Provider[Any]], ModelProfileSpec | None] | None = None,
    ) -> Model[Any]:
        """Return a native Model. The caller owns the supplied HTTP client and enters the Model."""
        api = model_api or self.supported_model_apis[0]
        if api not in self.supported_model_apis:
            raise ValueError("the Model API is not supported by this Provider")
        connection = self.bind(configuration, credential, extra_headers=extra_headers)
        policy = endpoint_policy or EndpointPolicy()
        if connection.endpoint is not None:
            await policy.validate(connection.endpoint, resolve_dns=True)
        for name in self.additional_endpoint_fields:
            endpoint = getattr(connection.configuration, name)
            if endpoint is not None:
                await policy.validate(endpoint, resolve_dns=True)
        native = await to_thread.run_sync(self.build_provider, connection, http_client, api)
        try:
            await policy.validate(str(native.base_url), resolve_dns=True)
            selected_profile = profile_resolver(native) if profile_resolver else profile
            return MODEL_APIS[api].build(model_name, native, profile=selected_profile)
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
