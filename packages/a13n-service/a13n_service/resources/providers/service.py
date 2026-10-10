"""The one implementation of provider resources; a kind is its row class (table, registry kind, ID prefix).

`config` and `credential` are validated by the registered definition for `type`. The credential, and each
extra header value a model provider sends, is encrypted for its exact row and column (a header also for its
name) and never returned; views expose only whether a credential is configured and which header names are.
Both are bound to the configuration they were entered with: a change of `config` must replace or remove the
credential and every stored header, so a new endpoint or account never inherits them.
"""

import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field, replace

from a13n_environment.credential_policy import CredentialPolicy
from a13n_environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.authentication import Authentication
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.model.apis import MODEL_APIS
from a13n_harness.providers.model.definition import ModelProviderDefinition
from a13n_harness.providers.web.definition import WebProviderDefinition
from pydantic import JsonValue, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.crypto import Envelope, KeyRing, SecretLocation
from a13n_service.infra.db import Storage, assign, short_session, transaction
from a13n_service.infra.errors import invalid, not_found
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.providers.registry import ProviderKind, RegisteredProvider, Registry, web_operations
from a13n_service.resources.providers.probe import probe, supports_probe
from a13n_service.resources.providers.schemas import (
    Credential,
    Provider,
    ProviderCreate,
    ProviderPage,
    ProviderTest,
    ProviderType,
    ProviderTypePage,
    ProviderUpdate,
)
from a13n_service.resources.providers.tables import ModelProviderOAuthRow, ModelProviderRow, ProviderRow
from a13n_service.resources.rows import audit_row, find_row, given, record_update, usable_row
from a13n_service.settings import Providers
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, Verb, WorkspaceScope


@dataclass(frozen=True, slots=True)
class ResolvedProvider:
    """A provider as execution uses it: plain values, with its secrets encrypted until revealed."""

    id: str
    version: int
    type: str
    config: Mapping[str, JsonValue]
    enabled: bool
    credential: Envelope | None = field(repr=False)
    extra_headers: Mapping[str, Envelope] = field(repr=False)
    location: SecretLocation = field(repr=False)

    def reveal_credential(self, keys: KeyRing) -> JsonValue:
        return None if self.credential is None else json.loads(keys.reveal(self.credential, self.location))

    def reveal_headers(self, keys: KeyRing) -> dict[str, str]:
        return {
            name: keys.reveal(envelope, _header_location(self.location, name)).decode()
            for name, envelope in self.extra_headers.items()
        }


def provider_view(row: ProviderRow) -> Provider:
    return Provider(
        id=row.id,
        organization_id=row.organization_id,
        workspace_id=row.workspace_id,
        type=row.type,
        name=row.name,
        config=row.config,
        credential_configured=row.credential is not None,
        header_names=sorted(row.extra_headers),
        enabled=row.enabled,
        version=row.version,
        created_by_id=row.created_by_id,
        updated_by_id=row.updated_by_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def create_provider[R: ProviderRow](
    storage: Storage,
    actor: Principal,
    row_type: type[R],
    workspace_id: str,
    body: ProviderCreate,
    *,
    registry: Registry,
    keys: KeyRing,
) -> Provider:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        row = await insert_provider(session, actor, row_type, scope, body, registry=registry, keys=keys)
        return provider_view(row)


async def insert_provider[R: ProviderRow](
    session: AsyncSession,
    actor: Principal | None,
    row_type: type[R],
    scope: WorkspaceScope,
    body: ProviderCreate,
    *,
    registry: Registry,
    keys: KeyRing,
) -> R:
    """Validate, encrypt, insert and audit in the caller's authorized transaction; None is the system actor."""
    definition = registry.get(row_type.PROVIDER_KIND, body.type)
    config = _validated_config(
        definition, body.config, body.credential, stored_credential=False, header_names=body.extra_headers.keys()
    )
    row = row_type(
        id=new_object_id(row_type.ID_PREFIX),
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        type=body.type,
        name=body.name,
        config=config,
        extra_headers={},
        enabled=body.enabled,
        created_by_id=None if actor is None else actor.id,
        updated_by_id=None if actor is None else actor.id,
    )
    row.credential = None if body.credential is None else _protect(keys, row, body.credential)
    row.extra_headers = _updated_headers(keys, row, body.extra_headers)
    session.add(row)
    await session.flush()
    audit_row(session, actor, row, "create")
    return row


async def get_provider[R: ProviderRow](
    storage: Storage, actor: Principal, row_type: type[R], workspace_id: str, provider_id: str
) -> Provider:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        return provider_view(await find_row(session, actor, row_type, scope, provider_id, "read"))


async def list_providers[R: ProviderRow](
    storage: Storage, actor: Principal, row_type: type[R], workspace_id: str, *, limit: int, cursor: str | None
) -> ProviderPage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        rows, next_cursor = await cursors.id_page(
            session,
            select(row_type).where(row_type.workspace_id == scope.workspace_id),
            row_type.id,
            kind=row_type.KIND,
            owner=scope.workspace_id,
            cursor=cursor,
            limit=limit,
        )
    return ProviderPage(items=[provider_view(row) for row in rows], next_cursor=next_cursor)


async def update_provider[R: ProviderRow](
    storage: Storage,
    actor: Principal,
    row_type: type[R],
    workspace_id: str,
    provider_id: str,
    body: ProviderUpdate,
    *,
    if_match: str | None,
    registry: Registry,
    keys: KeyRing,
) -> Provider:
    replaces_credential = "credential" in body.model_fields_set
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = await find_row(session, actor, row_type, scope, provider_id, "write", lock=True)
        require_match(if_match, row.id, row.version)
        headers = _updated_headers(keys, row, body.extra_headers)
        config = row.config if body.config is None else body.config
        if body.config is not None or replaces_credential or body.extra_headers:
            config = _validated_config(
                registry.get(row_type.PROVIDER_KIND, row.type),
                config,
                body.credential,
                stored_credential=not replaces_credential and row.credential is not None,
                header_names=headers.keys(),
            )
        # Only a configuration the body sets is compared: the stored one stays as it was normalized.
        changed = [] if body.config is None else assign(row, {"config": config})
        if changed:
            if row.credential is not None and not replaces_credential:
                raise invalid("credential", "a configuration change must replace or remove the credential")
            if not row.extra_headers.keys() <= body.extra_headers.keys():
                raise invalid("extra_headers", "a configuration change must replace or remove every stored header")
        oauth_config_changed = bool(changed) or replaces_credential
        if replaces_credential and (body.credential is not None or row.credential is not None):
            row.credential = None if body.credential is None else _protect(keys, row, body.credential)
            changed.append("credential")
        if oauth_config_changed and isinstance(row, ModelProviderRow) and row.type == "openai_chatgpt":
            oauth_row = await session.get(ModelProviderOAuthRow, row.id, with_for_update=True)
            if oauth_row is not None:
                # A changed registration supersedes both unconsumed and in-flight sign-ins.
                # Existing grants retain their own client authentication for renewal.
                oauth_row.pending = None
                oauth_row.pending_id = None
                oauth_row.login_claim = None
        changed += assign(row, {"extra_headers": headers, **given(body, "name", "enabled")})
        if record_update(session, actor, row, changed):
            await session.flush()
        return provider_view(row)


async def test_provider[R: ProviderRow](
    storage: Storage,
    actor: Principal,
    row_type: type[R],
    workspace_id: str,
    provider_id: str,
    *,
    registry: Registry,
    keys: KeyRing,
    policy: EndpointPolicy,
    settings: Providers,
) -> ProviderTest:
    """One non-billable probe of the current configuration, outside any transaction; changes nothing."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        provider = _resolved(await find_row(session, actor, row_type, scope, provider_id, "run"))
    result = await probe(
        registry,
        row_type.PROVIDER_KIND,
        provider.type,
        config=provider.config,
        credential=provider.reveal_credential(keys),
        extra_headers=provider.reveal_headers(keys),
        policy=policy,
        timeout=settings.operation_seconds,
        max_bytes=settings.response_bytes,
    )
    return ProviderTest(
        provider_id=provider.id, provider_version=provider.version, status=result.status, message=result.message
    )


async def resolve_provider[R: ProviderRow](
    session: AsyncSession,
    actor: Principal,
    row_type: type[R],
    scope: WorkspaceScope,
    provider_id: str,
    *,
    verb: Verb = "run",
    authority: ExecutionAuthority | None = None,
) -> ResolvedProvider:
    """An enabled provider of the workspace, read in the caller's short session."""
    return _resolved(await usable_row(session, actor, row_type, scope, provider_id, verb=verb, authority=authority))


async def read_provider[R: ProviderRow](session: AsyncSession, row_type: type[R], provider_id: str) -> ResolvedProvider:
    """The current provider of existing work, enabled or not; lifecycle maintenance acts for no principal."""
    row = await session.get(row_type, provider_id)
    if row is None:
        raise not_found(row_type.KIND, provider_id)
    return _resolved(row)


def list_provider_types(registry: Registry, kind: ProviderKind) -> ProviderTypePage:
    return ProviderTypePage(
        items=[_describe(registry, definition) for definition in registry.types(kind)], next_cursor=None
    )


def _credential_policy(definition: RegisteredProvider) -> CredentialPolicy | Authentication:
    if isinstance(definition, EnvironmentProviderDefinition):
        return definition.credential_policy
    return definition.authentication


def _validated_config(
    definition: RegisteredProvider,
    config: Mapping[str, JsonValue],
    credential: Credential | None,
    *,
    stored_credential: bool,
    header_names: Collection[str],
) -> dict[str, JsonValue]:
    """The normalized configuration to store, after checking it, the credential and the names of the extra
    headers the provider will send against the type.

    `stored_credential` keeps an existing credential: only its presence is checked against the new configuration.
    """
    try:
        parsed = definition.configuration_model.model_validate(config)
    except ValidationError as error:
        raise invalid("config", rejection_reason(error)) from None
    try:
        if credential is None and stored_credential:
            _credential_policy(definition).validate_presence(parsed, True)
        else:
            definition.parse_credential(parsed, credential)
    except ValueError as error:
        raise invalid("credential", rejection_reason(error)) from None
    normalized = parsed.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    if header_names:
        credential_configured = stored_credential or credential is not None
        _check_header_names(definition, normalized, header_names, credential_configured=credential_configured)
    return normalized


def _check_header_names(
    definition: RegisteredProvider,
    config: Mapping[str, JsonValue],
    names: Collection[str],
    *,
    credential_configured: bool,
) -> None:
    """Only a model provider sends extra headers, never under a name its authentication or protocol owns."""
    if not isinstance(definition, ModelProviderDefinition):
        raise invalid("extra_headers", f"{definition.type} sends no extra headers")
    try:
        definition.validate_configuration(
            config, credential_configured=credential_configured, header_names=tuple(names)
        )
    except ValueError as error:
        raise invalid("extra_headers", rejection_reason(error)) from None


def rejection_reason(error: ValueError) -> str:
    """Why a type's schema refused a value: its locations and the validators' own messages, never the input, which
    can be a credential."""
    if isinstance(error, ValidationError):
        return "; ".join(
            f"{'.'.join(map(str, item['loc'])) or 'value'}: {item['msg']}"
            for item in error.errors(include_input=False, include_url=False)[:5]
        )
    return str(error)


def _resolved(row: ProviderRow) -> ResolvedProvider:
    return ResolvedProvider(
        id=row.id,
        version=row.version,
        type=row.type,
        config=dict(row.config),
        enabled=row.enabled,
        credential=None if row.credential is None else Envelope.model_validate(row.credential),
        extra_headers={name: Envelope.model_validate(value) for name, value in row.extra_headers.items()},
        location=_location(row),
    )


def _location(row: ProviderRow) -> SecretLocation:
    return SecretLocation(row.organization_id, row.__tablename__, "credential", row.id)


def _header_location(location: SecretLocation, name: str) -> SecretLocation:
    """A header value is bound to its row and its own name, so one can never be revealed as another."""
    return replace(location, column=f"extra_headers.{name}")


def _protect(keys: KeyRing, row: ProviderRow, credential: Credential) -> dict[str, JsonValue]:
    plaintext = json.dumps(credential, separators=(",", ":"), allow_nan=False).encode()
    return keys.protect(plaintext, _location(row)).model_dump(mode="json")


def _updated_headers(keys: KeyRing, row: ProviderRow, updates: Mapping[str, str | None]) -> dict[str, JsonValue]:
    """The row's header envelopes after `updates`: a value is encrypted and replaces the stored one, `None`
    removes the header, and names left out keep theirs."""
    headers: dict[str, JsonValue] = dict(row.extra_headers)
    for name, value in updates.items():
        if value is None:
            headers.pop(name, None)
        else:
            envelope = keys.protect(value.encode(), _header_location(_location(row), name))
            headers[name] = envelope.model_dump(mode="json")
    return headers


def _describe(registry: Registry, definition: RegisteredProvider) -> ProviderType:
    credential = definition.credential_model
    described = ProviderType(
        type=definition.type,
        display_name=definition.display_name,
        configuration_schema=definition.configuration_model.model_json_schema(),
        credential_schema=None if credential is None else credential.model_json_schema(),
        authentication=Authentication.model_validate(_credential_policy(definition).model_dump(mode="json")),
        setup_url=definition.setup_url,
        setup_label=definition.setup_label,
        supports_test=supports_probe(definition),
    )
    if isinstance(definition, ModelProviderDefinition):
        described.supports_model_discovery = definition.supports_model_discovery
        described.oauth_scheme = definition.oauth.scheme if definition.oauth else None
        apis = definition.supported_model_apis
        described.model_apis = list(apis)
        # The Harness calls through the first API when a model selects none.
        described.default_model_api = apis[0]
        described.model_api_labels = {api: MODEL_APIS[api].display_name for api in apis}
        described.settings_schemas = {api: dict(registry.model_settings[api]) for api in apis}
        described.catalog_providers = list(definition.catalog_providers)
    elif isinstance(definition, WebProviderDefinition):
        described.operations = list(web_operations(definition))
    elif isinstance(definition, EnvironmentProviderDefinition):
        described.environment_schema = definition.environment_model.model_json_schema()
        described.supports_stop = definition.supports_stop
        described.supports_destroy = definition.supports_destroy
    return described
