"""One model-authoring contract for the browser and terminal.

Connections are release-owned recipes, not persisted Provider resources. Model
recipes contain only existing Model fields. Discovery never resolves a secret,
logs in, or constructs an executable Model.
"""

from __future__ import annotations

from typing import Literal

from a13n_harness.model_affinity import SESSION_AFFINITY_PRESETS, SessionAffinityPreset
from a13n_harness.spec import HarnessModelCharacteristics
from pydantic import Field, JsonValue, TypeAdapter, model_validator

from a13n_harness_ui.configuration.models import ModelAuthentication, ModelCharacteristics, StrictModel
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.model_presets import (
    API_MODEL_SUGGESTIONS,
    API_PROVIDERS,
    SettingsPreset,
    known_context_window,
    known_model_capabilities,
    settings_presets,
    validate_base_url,
)
from a13n_harness_ui.model_reasoning_mode import ReasoningModeControl, describe_reasoning_mode
from a13n_harness_ui.resource_names import model_name

type AuthenticationKind = Literal["api_key", "codex_subscription", "grok_subscription", "copilot_subscription"]


class ModelChoice(StrictModel):
    value: str
    label: str


class ContextChoice(StrictModel):
    value: int
    label: str
    description: str


CODEX_CONTEXT_CHOICES = (
    ContextChoice(value=272000, label="Standard", description="Conservative working budget"),
    ContextChoice(value=350000, label="Balanced", description="Recommended for repository work"),
    ContextChoice(value=872000, label="Extended", description="Account support required; greater latency and usage"),
)


class AccountConnection(StrictModel):
    """Built-in account actions shared by terminal and browser authoring."""

    provider: Literal["codex", "grok", "copilot"]
    label: str
    login_methods: tuple[Literal["device", "browser"], ...]
    model_discovery: bool = False
    source_selection: bool = False


class ModelConnection(StrictModel):
    id: str
    label: str
    provider: str
    authentication: AuthenticationKind
    account: AccountConnection | None = None
    base_url: str = ""
    credential_env: str | None = None
    supports_base_url: bool = False
    supports_session_affinity: bool = False
    models: tuple[ModelChoice, ...]
    default_model: str


SUBSCRIPTION_CONNECTIONS = (
    ModelConnection(
        id="codex",
        label="Codex subscription",
        provider="openai-codex",
        authentication="codex_subscription",
        account=AccountConnection(provider="codex", label="Codex", login_methods=("device", "browser")),
        models=tuple(
            ModelChoice(value=value, label=model_name("codex", value))
            for value in ("gpt-6.1-sol", "gpt-6-astra", "gpt-5.6-terra", "gpt-6-sol", "gpt-5.6-sol")
        ),
        default_model="gpt-6.1-sol",
    ),
    ModelConnection(
        id="grok-subscription",
        label="Grok subscription",
        provider="grok",
        authentication="grok_subscription",
        account=AccountConnection(provider="grok", label="Grok", login_methods=("device", "browser")),
        models=tuple(
            ModelChoice(value=value, label=model_name("grok-subscription", value))
            for value in ("grok-4.7", "grok-4.5", "grok-4.20-0309-reasoning")
        ),
        default_model="grok-4.7",
    ),
    ModelConnection(
        id="copilot-subscription",
        label="GitHub Copilot subscription",
        provider="github-copilot",
        authentication="copilot_subscription",
        account=AccountConnection(
            provider="copilot",
            label="GitHub Copilot",
            login_methods=("device",),
            model_discovery=True,
            source_selection=True,
        ),
        models=(),
        default_model="",
    ),
)


def subscription_connection(provider: str) -> ModelConnection:
    for connection in SUBSCRIPTION_CONNECTIONS:
        if connection.account is not None and connection.account.provider == provider:
            return connection
    raise HarnessUiError("Choose a supported account provider.", code="account_provider_unknown")


def account_connection(provider: str) -> AccountConnection:
    account = subscription_connection(provider).account
    assert account is not None
    return account


def model_connections() -> tuple[ModelConnection, ...]:
    return (
        *SUBSCRIPTION_CONNECTIONS,
        *(
            ModelConnection(
                id=provider.route,
                label=provider.label,
                provider=provider.route,
                authentication="api_key",
                base_url=provider.base_url,
                credential_env=provider.credential_env,
                supports_base_url=provider.transport != "xai",
                supports_session_affinity=provider.supports_session_affinity,
                models=tuple(
                    ModelChoice(value=value, label=model_name(provider.route, value))
                    for value in API_MODEL_SUGGESTIONS[provider.route]
                ),
                default_model=API_MODEL_SUGGESTIONS[provider.route][0],
            )
            for provider in API_PROVIDERS
        ),
    )


def model_connection(connection_id: str) -> ModelConnection:
    connection = next((item for item in model_connections() if item.id == connection_id), None)
    if connection is None:
        raise HarnessUiError("Choose a supported model connection.", code="model_connection_unknown")
    return connection


class ModelChoices(StrictModel):
    connections: tuple[ModelConnection, ...] = Field(default_factory=model_connections)
    session_affinity_presets: tuple[SessionAffinityPreset, ...] = SESSION_AFFINITY_PRESETS


class ModelRecipe(StrictModel):
    route: str = Field(min_length=3, max_length=512)
    authentication: ModelAuthentication
    settings: dict[str, JsonValue] = Field(default_factory=dict)
    model_configuration: dict[str, JsonValue] = Field(default_factory=dict)
    model_characteristics: ModelCharacteristics | None = None

    @model_validator(mode="after")
    def _subscription_connection(self) -> ModelRecipe:
        route = next(
            (item.provider for item in SUBSCRIPTION_CONNECTIONS if item.authentication == self.authentication.kind),
            None,
        )
        if route is not None and (self.route.partition(":")[0] != route or self.model_configuration):
            raise ValueError("Subscriptions require their native route and no endpoint configuration")
        return self


class ModelOptionsRequest(StrictModel):
    connection: str = Field(min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=480)
    base_url: str | None = Field(default=None, max_length=4096)
    settings: dict[str, JsonValue] | None = None


class ModelRecipeRequest(ModelOptionsRequest):
    authentication: ModelAuthentication | None = None
    preset: str | None = None
    model_configuration: dict[str, JsonValue] = Field(default_factory=dict)
    model_characteristics: ModelCharacteristics | None = None


class ModelSettingsChoice(StrictModel):
    value: str
    label: str
    description: str
    settings: dict[str, JsonValue]


class ModelToolChoice(StrictModel):
    value: str
    label: str
    description: str
    recommended: bool
    capability: dict[str, JsonValue] | None = None
    replaces_host_operation: Literal["search", "scrape"] | None = None


def model_tool_choices(route: str, authentication: str, base_url: str) -> tuple[ModelToolChoice, ...]:
    from a13n_harness_ui.tool_presets import selected_tool_capabilities, tool_choices

    return tuple(
        ModelToolChoice(
            value=choice.key,
            label=choice.label,
            description=choice.description,
            recommended=choice.recommended,
            capability=(
                selected_tool_capabilities((choice.key,), authentication=authentication)[-1]
                if choice.key not in {"mcp_server", "file_search", "advisor"}
                else None
            ),
            replaces_host_operation="search"
            if choice.key == "web_search"
            else "scrape"
            if choice.key == "web_fetch"
            else None,
        )
        for choice in tool_choices(route, authentication=authentication, base_url=base_url or None)
    )


class ModelOptions(StrictModel):
    name: str
    route: str
    presets: tuple[ModelSettingsChoice, ...]
    context_window: int | None
    known_context_window: int | None
    context_choices: tuple[ContextChoice, ...] = ()
    characteristics: ModelCharacteristics
    known_capabilities: bool
    supports_service_tier: bool
    reasoning_mode: ReasoningModeControl
    native_tools: tuple[ModelToolChoice, ...] = ()


def _base_url(connection: ModelConnection, value: str | None) -> str:
    base_url = connection.base_url if value is None else value
    if connection.supports_base_url:
        try:
            validate_base_url(base_url)
        except ValueError as exc:
            raise HarnessUiError(str(exc), code="model_base_url_invalid") from exc
    elif base_url:
        raise HarnessUiError("This connection uses its native endpoint.", code="model_base_url_unsupported")
    return base_url


def authoring_presets(connection: ModelConnection, model_id: str) -> tuple[SettingsPreset, ...]:
    if connection.authentication == "codex_subscription":
        # Only reviewed subscription IDs receive effort recommendations. An old
        # or custom saved Model remains editable without guessed support.
        if model_id not in {item.value for item in connection.models}:
            return (
                SettingsPreset(
                    "default", "Subscription defaults", "Use native settings for this model", {"openai_store": False}
                ),
            )
        native_effort = model_id == "gpt-6.1-sol"
        return tuple(
            SettingsPreset(
                effort,
                f"{effort.title()} reasoning",
                "Codex reasoning; no API output-token cap",
                {
                    "openai_reasoning_effort" if native_effort else "thinking": effort,
                    "openai_reasoning_summary": "detailed",
                    "openai_store": False,
                },
            )
            for effort in (
                ("high", "medium", "low", "xhigh", "max") if native_effort else ("high", "medium", "low", "xhigh")
            )
        )
    if connection.authentication in {"grok_subscription", "copilot_subscription"}:
        return (SettingsPreset("default", "Subscription defaults", "Keep native subscription behavior", {}),)
    if (
        model_id not in API_MODEL_SUGGESTIONS.get(connection.provider, ())
        and known_model_capabilities(f"{connection.provider}:{model_id}") is None
    ):
        return (
            SettingsPreset("default", "Provider defaults", "Unreviewed model; configure native settings if needed", {}),
        )
    return settings_presets(connection.provider, model_id)


def model_options(request: ModelOptionsRequest) -> ModelOptions:
    connection = model_connection(request.connection)
    base_url = _base_url(connection, request.base_url)
    if any(char.isspace() for char in request.model_id):
        raise HarnessUiError("Model IDs cannot contain whitespace.", code="model_id_invalid")
    route = f"{connection.provider}:{request.model_id}"
    known_context = (
        known_context_window(connection.provider, request.model_id, base_url)
        if connection.authentication == "api_key"
        else None
    )
    known_capabilities = known_model_capabilities(route)
    context = (
        HarnessModelCharacteristics().context_window_tokens
        if connection.authentication in {"grok_subscription", "copilot_subscription"}
        else min(350000, known_context)
        if known_context
        else 350000
    )
    return ModelOptions(
        name=model_name(connection.id, request.model_id),
        route=route,
        presets=tuple(
            ModelSettingsChoice(value=p.key, label=p.label, description=p.description, settings=p.settings)
            for p in authoring_presets(connection, request.model_id)
        ),
        context_window=context,
        known_context_window=known_context,
        context_choices=CODEX_CONTEXT_CHOICES if connection.id == "codex" else (),
        characteristics=HarnessModelCharacteristics(
            context_window_tokens=context,
            capabilities=known_capabilities or frozenset(),
            proactive_context_management_threshold=0.65,
            compact_threshold=0.90,
        ),
        known_capabilities=known_capabilities is not None,
        supports_service_tier=connection.id in {"codex", "openai-responses", "openai-chat"},
        reasoning_mode=describe_reasoning_mode(route, request.settings or {}),
        native_tools=model_tool_choices(route, connection.authentication, base_url),
    )


def prepare_model(request: ModelRecipeRequest) -> ModelRecipe:
    """Expand omitted creation fields; explicit native mappings remain authoritative."""
    connection = model_connection(request.connection)
    configured_url = request.model_configuration.get("base_url")
    if "base_url" in request.model_configuration and not isinstance(configured_url, str):
        raise HarnessUiError("Base URL must be a string.", code="model_base_url_invalid")
    if request.base_url is not None and configured_url is not None and request.base_url != configured_url:
        raise HarnessUiError("Specify one consistent base URL.", code="model_base_url_conflict")
    effective_url = (
        request.base_url
        if request.base_url is not None
        else configured_url
        if isinstance(configured_url, str)
        else None
    )
    options = model_options(
        ModelOptionsRequest(connection=request.connection, model_id=request.model_id, base_url=effective_url)
    )
    authentication = request.authentication
    if authentication is None and connection.authentication != "api_key":
        authentication = TypeAdapter(ModelAuthentication).validate_python({"kind": connection.authentication})
    if authentication is None or authentication.kind != connection.authentication:
        raise HarnessUiError("Choose authentication matching this connection.", code="model_authentication_invalid")
    settings = request.settings
    if settings is None:
        preset = next(
            (item for item in options.presets if item.value == (request.preset or options.presets[0].value)), None
        )
        if preset is None:
            raise HarnessUiError("Choose a setting offered for this connection.", code="model_preset_unknown")
        settings = dict(preset.settings)
        if connection.id == "codex":
            settings["openai_service_tier"] = "priority"
    configuration = dict(request.model_configuration)
    base_url = _base_url(connection, effective_url)
    if base_url:
        configuration["base_url"] = base_url
    if not connection.supports_base_url and "base_url" in configuration:
        raise HarnessUiError("This connection does not accept a base URL.", code="model_base_url_unsupported")
    return ModelRecipe(
        route=options.route,
        authentication=authentication,
        settings=settings,
        model_configuration=configuration,
        model_characteristics=_characteristics(
            options.route,
            request.model_characteristics if request.model_characteristics is not None else options.characteristics,
        ),
    )


def recipe_name(recipe: ModelRecipe) -> str:
    provider, _, model_id = recipe.route.partition(":")
    naming_provider = next(
        (item.id for item in SUBSCRIPTION_CONNECTIONS if item.authentication == recipe.authentication.kind), provider
    )
    return model_name(naming_provider, model_id)


def _characteristics(route: str, authored: ModelCharacteristics | None) -> ModelCharacteristics:
    """Materialize omitted capabilities before any JSON or YAML boundary."""
    characteristics = authored if authored is not None else HarnessModelCharacteristics()
    if "capabilities" not in characteristics.model_fields_set:
        known = known_model_capabilities(route)
        if known is not None:
            characteristics = characteristics.model_copy(update={"capabilities": known})
    return characteristics


def recipe_document(recipe: ModelRecipe) -> dict[str, JsonValue]:
    """Stable creation bytes, including omitted reviewed input capabilities."""
    document = recipe.model_dump(mode="json", exclude_none=True)
    characteristics = _characteristics(recipe.route, recipe.model_characteristics)
    policy = characteristics.model_dump(mode="json")
    policy["capabilities"] = sorted(item.value for item in characteristics.capabilities)
    document["model_characteristics"] = policy
    return document
