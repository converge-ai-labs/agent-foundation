"""Contracts for trusted, in-process OAuth Model Provider plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, Literal
from urllib.parse import urlsplit

import httpx2
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from a13n_harness_ui.configuration.models import ModelCharacteristics
from a13n_harness_ui.model_accounts.usage import UsageWindow

if TYPE_CHECKING:
    from pydantic_ai.models import Model

PROVIDER_KEY_PATTERN = r"^[a-z][a-z0-9_-]{1,62}$"
type LoginMethod = Literal["device", "browser", "manual_callback"]


def https_url(value: str) -> httpx2.URL:
    if not value.startswith("https://") or any(ord(ch) <= 32 or ord(ch) == 127 or ch == "\\" for ch in value):
        raise ValueError("Expected an HTTPS URL")
    parts = urlsplit(value)
    url = httpx2.URL(value)
    if not url.host or "@" in parts.netloc or "#" in value or parts.port == 0:
        raise ValueError("Expected an HTTPS URL without credentials or fragment")
    return url


def origin(url: httpx2.URL) -> tuple[str, bytes, int]:
    return url.scheme, url.raw_host, 443 if url.port is None else url.port


class _Contract(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", hide_input_in_errors=True)


class LoopbackCallbackSpec(_Contract):
    host: Literal["127.0.0.1", "localhost"] = "127.0.0.1"
    port: int = Field(default=0, ge=0, le=65535)
    path: str = Field(default="/auth/callback", pattern=r"^/(?:[A-Za-z0-9_~-]+/)*[A-Za-z0-9_~-]+$", max_length=256)


class PluginModelSuggestion(_Contract):
    model_id: str = Field(min_length=1, max_length=480, pattern=r"^[^\s:@/][^\s:@]*$")
    label: str = Field(min_length=1, max_length=256)
    base_model: str | None = Field(default=None, min_length=1, max_length=512)
    characteristics: ModelCharacteristics | None = None


class ModelProviderDeclaration(_Contract):
    api_version: Literal[1] = 1
    provider_key: str = Field(pattern=PROVIDER_KEY_PATTERN)
    display_name: str = Field(min_length=1, max_length=128)
    brand: str | None = Field(default=None, min_length=1, max_length=128)
    logo_url: str | None = Field(default=None, max_length=2048)
    login_methods: tuple[LoginMethod, ...] = Field(min_length=1, max_length=3)
    loopback_callback: LoopbackCallbackSpec | None = None
    allowed_origins: tuple[str, ...] = Field(min_length=1)
    supports_model_discovery: bool = False
    supports_usage: bool = False
    models: tuple[PluginModelSuggestion, ...] = ()
    default_model: str = ""

    @field_validator("logo_url")
    @classmethod
    def _logo(cls, value: str | None) -> str | None:
        if value is not None:
            https_url(value)
        return value

    @field_validator("allowed_origins")
    @classmethod
    def _origins(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        seen = set()
        for value in values:
            url = https_url(value)
            parts = urlsplit(value)
            if parts.path or "?" in value or "*" in parts.netloc or "%" in parts.netloc:
                raise ValueError("An allowed origin must be an exact HTTPS scheme, host and optional port")
            key = origin(url)
            if key in seen:
                raise ValueError("Duplicate allowed origin")
            seen.add(key)
        return values

    @model_validator(mode="after")
    def _consistent(self) -> ModelProviderDeclaration:
        methods = set(self.login_methods)
        if len(methods) != len(self.login_methods):
            raise ValueError("Duplicate login methods")
        if bool(methods - {"device"}) != (self.loopback_callback is not None):
            raise ValueError("Loopback callback settings must accompany callback login methods")
        models = {model.model_id for model in self.models}
        if len(models) != len(self.models) or (self.default_model and self.default_model not in models):
            raise ValueError("Model suggestions must be unique and include the declared default")
        return self


@dataclass(frozen=True, slots=True)
class PkceChallenge:
    verifier: str = field(repr=False)
    challenge_s256: str
    state: str = field(repr=False)
    nonce: str = field(repr=False)


class DeviceChallenge(_Contract):
    verification_url: str = Field(max_length=8192)
    user_code: str = Field(min_length=1, max_length=256)
    expires_in: float = Field(gt=0, allow_inf_nan=False)
    poll_interval: float = Field(gt=0, allow_inf_nan=False)
    state: JsonValue = Field(repr=False, exclude=True)


class CallbackChallenge(_Contract):
    authorization_url: str = Field(max_length=8192, repr=False)
    expires_in: float = Field(gt=0, allow_inf_nan=False)
    state: JsonValue = Field(repr=False, exclude=True)


class DevicePollSlowDown(Exception):
    """The authorization server requested RFC 8628 slow_down; increase polling interval."""


class AuthorizedAccount(_Contract):
    upstream_account_id: str = Field(min_length=1, max_length=512)
    credentials: dict[str, JsonValue] = Field(repr=False)
    expires_at: AwareDatetime | None = None

    def expired(self, now: datetime) -> bool:
        return self.expires_at is not None and self.expires_at <= now


class PluginUsageSnapshot(_Contract):
    windows: tuple[UsageWindow, ...] = Field(max_length=64)


@dataclass(frozen=True, slots=True)
class OAuthContext:
    http_client: httpx2.AsyncClient
    redirect_uri: str | None = None
    pkce: PkceChallenge | None = None


@dataclass(frozen=True, slots=True)
class AccountContext:
    http_client: httpx2.AsyncClient
    _bearer_auth: Callable[[Mapping[str, str]], httpx2.Auth]

    def bearer_auth(self, *, extra_headers: Mapping[str, str] | None = None) -> httpx2.Auth:
        return self._bearer_auth(extra_headers or {})


@dataclass(frozen=True, slots=True)
class BuildModelContext(AccountContext):
    model_id: str
    configuration: BaseModel | None


class ModelProviderPlugin(ABC):
    """Implement upstream protocol differences; use Host clients for requests.

    Import, construction, schema generation, and declaration are pure. Installed
    plugins run as trusted Host code, with the same authority as Connectors.
    Credential envelopes used with bearer_auth include a string access_token.
    """

    configuration_model: ClassVar[type[BaseModel] | None] = None

    @abstractmethod
    def declaration(self) -> ModelProviderDeclaration: ...

    async def start_device_login(self, ctx: OAuthContext) -> DeviceChallenge:
        raise NotImplementedError

    async def poll_device_login(self, ctx: OAuthContext, challenge: DeviceChallenge) -> AuthorizedAccount | None:
        raise NotImplementedError

    async def begin_callback_login(self, ctx: OAuthContext) -> CallbackChallenge:
        raise NotImplementedError

    async def complete_callback_login(
        self, ctx: OAuthContext, challenge: CallbackChallenge, callback_params: Mapping[str, str]
    ) -> AuthorizedAccount:
        raise NotImplementedError

    @abstractmethod
    async def refresh_credentials(self, ctx: OAuthContext, current: AuthorizedAccount) -> AuthorizedAccount: ...

    @abstractmethod
    def build_model(self, ctx: BuildModelContext) -> Model: ...

    async def discover_models(self, ctx: AccountContext) -> tuple[PluginModelSuggestion, ...]:
        raise NotImplementedError

    async def fetch_usage(self, ctx: AccountContext) -> PluginUsageSnapshot:
        raise NotImplementedError
