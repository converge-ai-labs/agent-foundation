"""Workspace models, addressed by key, and their resolution for execution and for referencing resources.

A model is served by a model provider of its own workspace and spends that provider's credential, so creating
one or changing its configuration needs `write` on the provider too. Models are never deleted, so a key always
names the same model.
"""

import re
from dataclasses import dataclass

from a13n_harness import ModelCapability
from a13n_harness.pricing import ModelPricingEntry
from a13n_harness.providers.model.definition import ModelProviderDefinition
from a13n_harness.toolsets.file_media import NativeInputMediaKind
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.db import Storage, assign, short_session, transaction, unique_key
from a13n_service.infra.errors import disabled, invalid, not_found
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import KEY_MAX_LENGTH, new_object_id
from a13n_service.providers.registry import Registry
from a13n_service.resources.models.schemas import Model, ModelConfig, ModelCreate, ModelPage, ModelUpdate
from a13n_service.resources.models.tables import ModelRow
from a13n_service.resources.providers.service import ResolvedProvider, resolve_provider
from a13n_service.resources.providers.tables import ModelProviderRow
from a13n_service.resources.rows import audit_row, find_row, given, record_update
from a13n_service.tenancy.access import refuse_archived, workspace_scope
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, Verb, WorkspaceScope, authorize
from a13n_service.tenancy.tables import WorkspaceRow

# Characters a key cannot hold; a derived key replaces each run of them with `-`.
_NOT_KEY = re.compile(r"[^a-z0-9.-]+")


@dataclass(frozen=True, slots=True)
class ResolvedModel:
    """A model as execution uses it; its provider's credential stays encrypted until the model is opened."""

    id: str
    key: str
    version: int
    config: ModelConfig
    pricing: ModelPricingEntry | None
    provider: ResolvedProvider


async def resolve_model(
    session: AsyncSession,
    actor: Principal,
    scope: WorkspaceScope,
    key: str,
    *,
    verb: Verb = "run",
    authority: ExecutionAuthority | None = None,
) -> ResolvedModel:
    """An enabled model of an enabled provider of the workspace, read in the caller's short session."""
    row = await session.scalar(select(ModelRow).where(ModelRow.workspace_id == scope.workspace_id, ModelRow.key == key))
    if row is None:
        raise not_found(ModelRow.KIND, key)
    authorize(actor, scope, verb, authority=authority)
    if not row.enabled:
        raise disabled(ModelRow.KIND, key)
    provider = await resolve_provider(
        session, actor, ModelProviderRow, scope, row.provider_id, verb=verb, authority=authority
    )
    return ResolvedModel(
        id=row.id,
        key=row.key,
        version=row.version,
        config=ModelConfig.model_validate(row.config),
        pricing=_pricing(row),
        provider=provider,
    )


async def resolve_media_model(
    session: AsyncSession,
    actor: Principal,
    scope: WorkspaceScope,
    kind: NativeInputMediaKind,
    key: str,
    *,
    verb: Verb = "run",
    authority: ExecutionAuthority | None = None,
) -> ResolvedModel:
    """`resolve_model` for a model that describes `kind` media, which it must declare it understands."""
    model = await resolve_model(session, actor, scope, key, verb=verb, authority=authority)
    require_understanding(model, kind)
    return model


def require_understanding(model: ResolvedModel, kind: NativeInputMediaKind) -> None:
    if ModelCapability(f"{kind}_understanding") not in model.config.characteristics.capabilities:
        raise invalid("model", f"does not declare {kind}_understanding")


def default_key(provider_type: str, model_name: str) -> str:
    """The key a model created without one takes: `{provider type}-{upstream name}`, lowercased, with every run of
    characters a key cannot hold replaced by `-`."""
    key = _NOT_KEY.sub("-", f"{provider_type}-{model_name}".lower()).strip(".-")
    return key[:KEY_MAX_LENGTH].rstrip(".-")


async def create_model(
    storage: Storage, actor: Principal, workspace_id: str, body: ModelCreate, *, registry: Registry
) -> Model:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        provider = await find_row(session, actor, ModelProviderRow, scope, body.provider_id, "write")
        if not provider.enabled:
            raise disabled(provider.KIND, provider.id)
        _check_api(registry.get("model", provider.type), body.config)
        key = body.key or default_key(provider.type, body.config.model_name)
        row = ModelRow(
            id=new_object_id("mdl"),
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            provider_id=provider.id,
            key=key,
            name=body.name,
            description=body.description,
            config=body.config.model_dump(mode="json"),
            pricing=_dump(body.pricing),
            catalog_ref=_dump(body.catalog_ref),
            enabled=body.enabled,
            created_by_id=actor.id,
            updated_by_id=actor.id,
        )
        session.add(row)
        with unique_key(ModelRow.KIND, "uq_models_workspace_id_key", key):
            await session.flush()
        audit_row(session, actor, row, "create", {"key": key})
        return Model.model_validate(row)


async def get_model(storage: Storage, actor: Principal, workspace_id: str, key: str) -> Model:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        return Model.model_validate(await _find(session, actor, scope, key, "read"))


async def list_models(
    storage: Storage, actor: Principal, workspace_id: str, *, limit: int, cursor: str | None
) -> ModelPage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        rows, next_cursor = await cursors.id_page(
            session,
            select(ModelRow).where(ModelRow.workspace_id == scope.workspace_id),
            ModelRow.key,
            kind="models",
            owner=scope.workspace_id,
            cursor=cursor,
            limit=limit,
            max_length=KEY_MAX_LENGTH,
        )
    return ModelPage(items=[Model.model_validate(row) for row in rows], next_cursor=next_cursor)


async def update_model(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    key: str,
    body: ModelUpdate,
    *,
    if_match: str | None,
    registry: Registry,
) -> Model:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = await _find(session, actor, scope, key, "write", lock=True)
        require_match(if_match, row.key, row.version)
        values = given(body, "name", "description", "enabled")
        if body.config is not None and (config := body.config.model_dump(mode="json")) != row.config:
            # The model spends its provider's credential.
            provider = await find_row(session, actor, ModelProviderRow, scope, row.provider_id, "write")
            _check_api(registry.get("model", provider.type), body.config)
            values["config"] = config
        for field in ("pricing", "catalog_ref"):
            if field in body.model_fields_set:
                values[field] = _dump(getattr(body, field))
        changed = assign(row, values)
        if record_update(session, actor, row, changed):
            await session.flush()
        return Model.model_validate(row)


async def _find(
    session: AsyncSession, actor: Principal, scope: WorkspaceScope, key: str, verb: Verb, *, lock: bool = False
) -> ModelRow:
    query = select(ModelRow).where(ModelRow.workspace_id == scope.workspace_id, ModelRow.key == key)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = await session.scalar(query)
    if row is None:
        raise not_found(ModelRow.KIND, key)
    authorize(actor, scope, verb)
    if verb != "read":
        refuse_archived(await session.get_one(WorkspaceRow, scope.workspace_id))
    return row


def _dump(value: BaseModel | None) -> dict | None:
    return None if value is None else value.model_dump(mode="json")


def _pricing(row: ModelRow) -> ModelPricingEntry | None:
    return None if row.pricing is None else ModelPricingEntry.model_validate(row.pricing)


def _check_api(definition: ModelProviderDefinition, config: ModelConfig) -> None:
    if config.model_api not in definition.supported_model_apis:
        raise invalid("config.model_api", f"{definition.type} supports {', '.join(definition.supported_model_apis)}")
