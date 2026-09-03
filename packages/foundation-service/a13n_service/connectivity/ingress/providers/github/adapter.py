"""GitHub App HTTP v1 ingress configuration and routing."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from a13n_service.connectivity.ingress.domain import InputBatchingPolicy, JsonObject
from a13n_service.connectivity.ingress.provider import (
    AdmissionReceipt,
    DefaultRoute,
    InboundEvent,
    ProviderEligibleEventRouting,
    ProviderEventRouting,
    ProviderHttpResponse,
    ProviderRequest,
    ProviderRequestDecision,
    ProviderRequestError,
)

from ..common.mapping import default_event_mapping
from ..common.origins import (
    normalize_provider_origins,
    provider_url_origin,
    require_provider_base_url,
    require_provider_origin,
)
from .token import load_github_private_key
from .wire import GitHubIdentity, authenticate_and_normalize, event_actions

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


class GitHubIngressConfig(_StrictModel):
    api_origin: str = Field(min_length=1, max_length=2048)
    web_origin: str = Field(min_length=1, max_length=2048)
    app_id: int = Field(gt=0)
    installation_id: int = Field(gt=0)
    installation_account_id: int = Field(gt=0)
    bot_account_id: int = Field(gt=0)


class GitHubRouteMatch(_StrictModel):
    repository_ids: tuple[int, ...] = Field(min_length=1, max_length=256)
    event_actions: tuple[str, ...] = Field(min_length=1, max_length=32)
    labels: tuple[str, ...] | None = Field(default=None, min_length=1, max_length=128)
    base_branches: tuple[str, ...] | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("repository_ids")
    @classmethod
    def unique_repository_ids(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if len(set(value)) != len(value) or any(item <= 0 for item in value):
            raise ValueError("GitHub repository scopes must contain unique positive IDs")
        return tuple(sorted(value))

    @field_validator("event_actions", "labels", "base_branches")
    @classmethod
    def unique_strings(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is None:
            return None
        if len(set(value)) != len(value):
            raise ValueError("GitHub Route scopes must be unique")
        if any(not item or len(item) > 1024 for item in value):
            raise ValueError("GitHub Route scopes must be bounded")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def supported_predicates(self) -> GitHubRouteMatch:
        supported = event_actions()
        if any(value not in supported for value in self.event_actions):
            raise ValueError("GitHub Route event/action is unsupported")
        if self.labels is not None and any(
            not value.endswith((".labeled", ".unlabeled")) for value in self.event_actions
        ):
            raise ValueError("GitHub label predicates require label actions")
        if self.base_branches is not None and any(not value.startswith("pull_request") for value in self.event_actions):
            raise ValueError("GitHub base branch predicates require pull-request events")
        return self


class GitHubIngressAdapter:
    provider_key = "github"
    config_versions = frozenset({_CONFIG_VERSION})
    allows_runtime_ambiguity = False
    max_request_bytes = _REQUEST_MAX_BYTES
    dedup_horizon_seconds = _DEDUP_HORIZON_SECONDS

    def __init__(self, *, allowed_provider_origins: tuple[str, ...] = ()) -> None:
        self._allowed_provider_origins = normalize_provider_origins(allowed_provider_origins)

    def validate_config(self, value: object, *, config_version: str) -> JsonObject:
        _require_version(config_version)
        config = GitHubIngressConfig.model_validate(value)
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
        config = GitHubIngressConfig.model_validate(value)
        return (
            config.api_origin,
            config.web_origin,
            config.app_id,
            config.installation_id,
            config.installation_account_id,
            config.bot_account_id,
        )

    def validate_route(
        self,
        *,
        match: object,
        provider_policy: object,
        ingress_config: JsonObject,
        config_version: str,
    ) -> tuple[JsonObject, JsonObject]:
        _require_version(config_version)
        GitHubIngressConfig.model_validate(ingress_config)
        if provider_policy != {}:
            raise ValueError("GitHub provider policy must be empty")
        return _model_json(GitHubRouteMatch.model_validate(match)), {}

    def prove_non_overlap(self, left: JsonObject, right: JsonObject) -> bool | None:
        left_match = GitHubRouteMatch.model_validate(left)
        right_match = GitHubRouteMatch.model_validate(right)
        if set(left_match.repository_ids).isdisjoint(right_match.repository_ids):
            return True
        if set(left_match.event_actions).isdisjoint(right_match.event_actions):
            return True
        if left_match.labels is not None and right_match.labels is not None:
            if set(left_match.labels).isdisjoint(right_match.labels):
                return True
        if left_match.base_branches is not None and right_match.base_branches is not None:
            if set(left_match.base_branches).isdisjoint(right_match.base_branches):
                return True
        return False

    async def authenticate_and_normalize(
        self,
        request: ProviderRequest,
        *,
        ingress_id: str,
        ingress_config: JsonObject,
        credentials: JsonObject,
        received_at: datetime,
    ) -> ProviderRequestDecision:
        del ingress_id
        config = GitHubIngressConfig.model_validate(ingress_config)
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

    def route_matches(self, event: InboundEvent, match: JsonObject, *, config_version: str) -> bool:
        _require_version(config_version)
        route = GitHubRouteMatch.model_validate(match)
        repository_id = event.context.get("repository_id")
        event_action = event.context.get("event_action")
        action_label = event.context.get("action_label")
        base_branch = event.context.get("base_branch")
        return (
            type(repository_id) is int
            and repository_id in route.repository_ids
            and isinstance(event_action, str)
            and event_action in route.event_actions
            and (route.labels is None or (isinstance(action_label, str) and action_label in route.labels))
            and (route.base_branches is None or (isinstance(base_branch, str) and base_branch in route.base_branches))
        )

    def default_route(
        self,
        event: InboundEvent,
        ingress_config: JsonObject,
        *,
        config_version: str,
    ) -> DefaultRoute:
        del event
        _require_version(config_version)
        GitHubIngressConfig.model_validate(ingress_config)
        return DefaultRoute(
            input_mapping=default_event_mapping(),
            input_batching=InputBatchingPolicy(min_interval_ms=1, max_batch_events=10),
            provider_policy={},
        )

    def classify(
        self,
        event: InboundEvent,
        provider_policy: JsonObject,
        ingress_config: JsonObject,
        *,
        config_version: str,
    ) -> ProviderEventRouting:
        _require_version(config_version)
        config = GitHubIngressConfig.model_validate(ingress_config)
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
