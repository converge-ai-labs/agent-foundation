"""GitHub App HTTP v1 ingress configuration and routing."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from a13n_service.connectivity.accounts.reception import InputBatchingPolicy
from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.ingress.provider import (
    AdmissionReceipt,
    InboundEvent,
    ProviderEligibleEventRouting,
    ProviderEventRouting,
    ProviderHttpResponse,
    ProviderRequest,
    ProviderRequestDecision,
    ProviderRequestError,
    ReceptionDefaults,
)

from ..common.origins import (
    normalize_provider_origins,
    provider_url_origin,
    require_provider_base_url,
    require_provider_origin,
)
from .token import load_github_private_key
from .wire import GitHubIdentity, authenticate_and_normalize

_CONFIG_VERSION = "github_app_http_v1"
_REQUEST_MAX_BYTES = 8 * 1024 * 1024
_DEDUP_HORIZON_SECONDS = 7 * 24 * 60 * 60
_NATIVE_ACTIONS = ("github.add_comment", "github.read_comments", "github.read_issue_or_pr")
_PR_NATIVE_ACTIONS = (*_NATIVE_ACTIONS, "github.list_pr_files")
_OFFICIAL_API_BASES = frozenset({"https://api.github.com"})
_OFFICIAL_WEB_ORIGINS = frozenset({"https://github.com"})
_JSON_OBJECT = TypeAdapter(JsonObject)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GitHubAccountConfig(_StrictModel):
    api_origin: str = Field(min_length=1, max_length=2048)
    web_origin: str = Field(min_length=1, max_length=2048)
    app_id: int = Field(gt=0)
    installation_id: int = Field(gt=0)
    installation_account_id: int = Field(gt=0)
    bot_account_id: int = Field(gt=0)


class GitHubIngressAdapter:
    provider_key = "github"
    config_versions = frozenset({_CONFIG_VERSION})
    max_request_bytes = _REQUEST_MAX_BYTES
    dedup_horizon_seconds = _DEDUP_HORIZON_SECONDS

    def __init__(self, *, allowed_provider_origins: tuple[str, ...] = ()) -> None:
        self._allowed_provider_origins = normalize_provider_origins(allowed_provider_origins)

    def validate_config(self, value: object, *, config_version: str) -> JsonObject:
        _require_version(config_version)
        config = GitHubAccountConfig.model_validate(value)
        api_origin = require_provider_base_url(
            config.api_origin,
            official_base_urls=_OFFICIAL_API_BASES,
            allowed_custom_origins=self._allowed_provider_origins,
        )
        web_origin = require_provider_origin(
            config.web_origin,
            official_origins=_OFFICIAL_WEB_ORIGINS,
            allowed_custom_origins=self._allowed_provider_origins,
        )
        if (api_origin in _OFFICIAL_API_BASES) != (web_origin in _OFFICIAL_WEB_ORIGINS):
            raise ValueError("GitHub API and Web origins must select the same deployment")
        if api_origin not in _OFFICIAL_API_BASES and provider_url_origin(api_origin) != web_origin:
            raise ValueError("GitHub Enterprise API and Web origins must share one origin")
        return _model_json(config.model_copy(update={"api_origin": api_origin, "web_origin": web_origin}))

    def validate_credentials(self, value: dict[str, str], *, config_version: str) -> JsonObject:
        _require_version(config_version)
        if set(value) != {"webhook_secret", "app_private_key_pem"}:
            raise ValueError("GitHub App credentials are incomplete")
        if not 1 <= len(value["webhook_secret"]) <= 4096:
            raise ValueError("GitHub webhook secret is invalid")
        if not 1 <= len(value["app_private_key_pem"]) <= 32 * 1024:
            raise ValueError("GitHub App private key is invalid")
        load_github_private_key(value["app_private_key_pem"])
        return dict(value)

    def configuration_identity(self, value: JsonObject, *, config_version: str) -> object:
        _require_version(config_version)
        config = GitHubAccountConfig.model_validate(value)
        return (
            config.api_origin,
            config.web_origin,
            config.app_id,
            config.installation_id,
            config.installation_account_id,
            config.bot_account_id,
        )

    def validate_reception_policy(self, value: object, *, config_version: str) -> JsonObject:
        _require_version(config_version)
        if value != {}:
            raise ValueError("GitHub provider policy must be empty")
        return {}

    def validate_target(self, kind: str, external_id: str) -> str:
        if (
            kind != "repository"
            or not 1 <= len(external_id) <= 128
            or not external_id.isascii()
            or not external_id.isdecimal()
            or str(int(external_id)) != external_id
            or int(external_id) < 1
        ):
            raise ValueError("Invalid github target")
        return external_id

    def event_target(self, event: InboundEvent) -> tuple[str, str]:
        identifier = str(event.context["repository_id"])
        return "repository", self.validate_target("repository", identifier)

    async def authenticate_and_normalize(
        self,
        request: ProviderRequest,
        *,
        account_id: str,
        account_config: JsonObject,
        credentials: JsonObject,
        received_at: datetime,
    ) -> ProviderRequestDecision:
        del account_id
        config = GitHubAccountConfig.model_validate(account_config)
        return authenticate_and_normalize(
            request,
            identity=GitHubIdentity(
                app_id=config.app_id,
                installation_id=config.installation_id,
                installation_account_id=config.installation_account_id,
                bot_account_id=config.bot_account_id,
            ),
            webhook_secret=_required_credential(credentials, "webhook_secret"),
            received_at=received_at,
        )

    def reception_defaults(
        self,
        event: InboundEvent,
        account_config: JsonObject,
        *,
        config_version: str,
    ) -> ReceptionDefaults:
        del event
        _require_version(config_version)
        GitHubAccountConfig.model_validate(account_config)
        return ReceptionDefaults(
            input_batching=InputBatchingPolicy(min_interval_ms=1, max_batch_events=10),
            provider_policy={},
        )

    def classify(
        self,
        event: InboundEvent,
        provider_policy: JsonObject,
        account_config: JsonObject,
        *,
        config_version: str,
    ) -> ProviderEventRouting:
        _require_version(config_version)
        config = GitHubAccountConfig.model_validate(account_config)
        if provider_policy:
            raise ValueError("GitHub provider policy must be empty")
        target_kind = event.context.get("target_kind")
        native_actions = _PR_NATIVE_ACTIONS if target_kind == "pull_request" else _NATIVE_ACTIONS
        return ProviderEligibleEventRouting(
            external_ref_key="target",
            provider_context={
                "api_origin": config.api_origin,
                "web_origin": config.web_origin,
                "installation_id": config.installation_id,
                "repository_id": event.context["repository_id"],
                "repository_owner": event.context["repository_owner"],
                "repository_name": event.context["repository_name"],
                "number": event.context["number"],
                "target_kind": target_kind,
            },
            native_actions=native_actions,
        )

    def acknowledge(self, receipt: AdmissionReceipt) -> ProviderHttpResponse:
        return ProviderHttpResponse(status_code=202 if receipt.status == "pending" and not receipt.duplicate else 200)

    def failure_response(self, reason_code: str) -> ProviderHttpResponse:
        if reason_code == "delivery_identity_conflict":
            return ProviderHttpResponse(status_code=409)
        return ProviderHttpResponse(status_code=503, headers={"retry-after": "1"})


def _required_credential(value: JsonObject, key: str) -> str:
    selected = value.get(key)
    if not isinstance(selected, str) or not selected:
        raise ProviderRequestError(
            ProviderHttpResponse(status_code=503),
            reason_code="credential_unavailable",
        )
    return selected


def _model_json(value: BaseModel) -> JsonObject:
    return _JSON_OBJECT.validate_python(value.model_dump(mode="json"))


def _require_version(value: str) -> None:
    if value != _CONFIG_VERSION:
        raise ValueError("unsupported GitHub configuration version")
