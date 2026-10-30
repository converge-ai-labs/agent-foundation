"""Host-owned subscription identities and one-time adoption of external logins."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal
from uuid import uuid4

from a13n_harness.providers.model.oauth import CodexLoginResult, GrokCredentials
from anyio import CancelScope
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from a13n_harness_ui.configuration.models import SubscriptionGroupMember, SubscriptionGroupResource

from ._common import read_json_snapshot, write_json_if_unchanged
from .codex import CodexAccountStore, CodexLoginCallback, CodexLoginRequest, resolve_codex_policy
from .coordination import store_lock
from .grok import (
    DEFAULT_GROK_OAUTH_SCOPE,
    GrokAccountStore,
    GrokLoginCallback,
    GrokLoginRequest,
    resolve_grok_policy,
    resolve_grok_scope,
)
from .models import AccountProjection, AccountStoreConflictError, AccountStoreError, Provider, StoreKind, StorePolicy

type SubscriptionProvider = Literal["codex", "grok"]


class SubscriptionAccount(BaseModel):
    """Nonsecret local identity; credentials retain their provider-owned envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^acct-[a-zA-Z0-9_-]+$", max_length=128)
    provider: SubscriptionProvider
    name: str = Field(min_length=1, max_length=256)
    enabled: bool = True
    scope: str | None = None
    deleted: bool = False
    upstream_account_id: str | None = None
    login_revision: int = 0


class Adoption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["adopted", "absent", "unsupported", "invalid"]
    message: str | None = None
    group_created: bool = False


class _Index(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    accounts: dict[str, SubscriptionAccount] = Field(default_factory=dict)
    adoption: dict[SubscriptionProvider, Adoption] = Field(default_factory=dict)


class SubscriptionAccounts:
    def __init__(self, data_root: Path) -> None:
        self.root = data_root / "oauth"
        self.path = self.root / "accounts.json"

    async def _read(self):
        snapshot = await read_json_snapshot(self.path, provider=Provider.CODEX)
        try:
            index = _Index.model_validate(snapshot.document or {})
        except ValidationError:
            raise AccountStoreError(
                "The subscription account catalog is invalid.",
                code="account_store_incompatible",
                provider=Provider.CODEX,
            ) from None
        return snapshot, index

    async def list(self) -> tuple[SubscriptionAccount, ...]:
        _, index = await self._read()
        return tuple(item for item in index.accounts.values() if not item.deleted)

    async def get(self, account_id: str, *, available: bool = False) -> SubscriptionAccount:
        _, index = await self._read()
        account = index.accounts.get(account_id)
        if account is None or account.deleted or (available and not account.enabled):
            raise AccountStoreError(
                "The subscription account is unavailable.",
                code="subscription_account_unavailable",
                provider=Provider(account.provider) if account else Provider.CODEX,
            )
        return account

    def store(self, account: SubscriptionAccount) -> CodexAccountStore | GrokAccountStore:
        policy = StorePolicy(
            provider=Provider(account.provider),
            kind=StoreKind.FILE,
            path=self.root / "accounts" / f"{account.id}.json",
            supported=True,
            writable=True,
            shared_with_cli=False,
        )
        if account.provider == "codex":
            return CodexAccountStore(policy)
        return GrokAccountStore(policy, scope=account.scope or DEFAULT_GROK_OAUTH_SCOPE)

    async def create(self, provider: SubscriptionProvider, name: str) -> SubscriptionAccount:
        account = SubscriptionAccount(id=f"acct-{uuid4().hex}", provider=provider, name=name)
        async with store_lock(self.path):
            snapshot, index = await self._read()
            index.accounts[account.id] = account
            await write_json_if_unchanged(
                self.path, index.model_dump(mode="json"), snapshot.digest, provider=Provider(provider)
            )
        return account

    async def update(
        self, account_id: str, *, name: str | None = None, enabled: bool | None = None
    ) -> SubscriptionAccount:
        async with store_lock(self.path):
            snapshot, index = await self._read()
            account = await self.get(account_id)
            updated = SubscriptionAccount.model_validate(
                {
                    **account.model_dump(),
                    "name": account.name if name is None else name,
                    "enabled": account.enabled if enabled is None else enabled,
                }
            )
            index.accounts[account_id] = updated
            await write_json_if_unchanged(
                self.path, index.model_dump(mode="json"), snapshot.digest, provider=Provider(account.provider)
            )
            return updated

    async def delete(self, account_id: str) -> None:
        async with store_lock(self.path):
            snapshot, index = await self._read()
            account = index.accounts.get(account_id)
            if account is None:
                return
            # Keep a tombstone so captured groups cannot resurrect an identity.
            index.accounts[account_id] = account.model_copy(update={"deleted": True, "enabled": False})
            await write_json_if_unchanged(
                self.path, index.model_dump(mode="json"), snapshot.digest, provider=Provider(account.provider)
            )
            await self.store(account).logout()

    async def adopt_once(
        self,
        configuration_root: Path | None,
        *,
        environ: Mapping[str, str] | None = None,
        user_home: Path | None = None,
        grok_scope: str | None = None,
        group_providers: frozenset[str] = frozenset({"codex", "grok"}),
        existing_groups: frozenset[str] = frozenset(),
    ) -> None:
        """Bootstrap only: never called by YAML validation or account rediscovery.

        Each observed provider result is durable before default-group publication.
        Retrying publication never rediscovers the external store.
        """
        async with store_lock(self.path):
            for provider in ("codex", "grok"):
                snapshot, index = await self._read()
                if provider not in index.adoption:
                    account = SubscriptionAccount(
                        id=f"acct-{provider}-default", provider=provider, name=f"{provider.title()} default"
                    )
                    result = Adoption(status="absent")
                    native_policy = self.store(account).policy
                    target = native_policy.path
                    assert target is not None
                    current = await read_json_snapshot(target, provider=Provider(provider))
                    document = current.document
                    try:
                        # A previous bootstrap may have published credentials before its catalog.
                        # Recover that copy, including its scope, without rediscovering the CLI.
                        if document is not None:
                            if provider == "grok":
                                scope = await resolve_grok_scope(native_policy) or DEFAULT_GROK_OAUTH_SCOPE
                                account = account.model_copy(update={"scope": scope})
                        else:
                            if provider == "codex":
                                external = CodexAccountStore(
                                    await resolve_codex_policy(environ=environ, user_home=user_home)
                                )
                            else:
                                policy = resolve_grok_policy(environ=environ, user_home=user_home)
                                scope = grok_scope or await resolve_grok_scope(policy) or DEFAULT_GROK_OAUTH_SCOPE
                                account = account.model_copy(update={"scope": scope})
                                external = GrokAccountStore(policy, scope=scope)
                            if not external.policy.supported:
                                result = Adoption(
                                    status="unsupported", message="Sign in to create a managed subscription account."
                                )
                            else:
                                document = await external.export_login()
                    except AccountStoreError:
                        result = Adoption(
                            status="invalid", message="The external login could not be imported. Sign in here."
                        )
                        document = None
                    if document is not None:
                        if current.document is None:
                            await write_json_if_unchanged(target, document, None, provider=Provider(provider))
                        # Bind to the published native credential, not a later external snapshot.
                        identity = (await self.store(account).load()).account_id
                        account = account.model_copy(update={"upstream_account_id": identity})
                        result = Adoption(
                            status="adopted",
                            message=(
                                "Imported the existing login. Its refresh grant is still shared with the original CLI; "
                                "sign in again here if that grant is rejected."
                            ),
                        )
                    index.accounts.setdefault(account.id, account)
                    index.adoption[provider] = result
                    await write_json_if_unchanged(
                        self.path, index.model_dump(mode="json"), snapshot.digest, provider=Provider(provider)
                    )
        await self.publish_default_groups(configuration_root, group_providers, existing_groups=existing_groups)

    async def publish_default_groups(
        self,
        configuration_root: Path | None,
        providers: frozenset[str],
        *,
        existing_groups: frozenset[str] = frozenset(),
    ) -> bool:
        """Publish native defaults when a provider first enters configuration, without rediscovery."""
        changed = False
        async with store_lock(self.path):
            for provider in ("codex", "grok"):
                snapshot, index = await self._read()
                adoption = index.adoption.get(provider)
                if (
                    configuration_root is not None
                    and provider in providers
                    and adoption is not None
                    and not adoption.group_created
                ):
                    changed = True
                    group = SubscriptionGroupResource(
                        schema_version="1",
                        kind="subscription_group",
                        id=f"{provider}-default",
                        name=f"{provider.title()} default",
                        provider=provider,
                        accounts=(SubscriptionGroupMember(account=f"acct-{provider}-default"),),
                    )
                    target = configuration_root / "subscription_groups" / f"{group.id}.yaml"
                    try:
                        # JSON is a YAML subset. CAS preserves any user-authored default group.
                        if group.id not in existing_groups:
                            await write_json_if_unchanged(
                                target, group.model_dump(mode="json"), None, provider=Provider(provider)
                            )
                    except AccountStoreConflictError:
                        pass
                    index.adoption[provider] = index.adoption[provider].model_copy(update={"group_created": True})
                    await write_json_if_unchanged(
                        self.path, index.model_dump(mode="json"), snapshot.digest, provider=Provider(provider)
                    )
        return changed

    async def login_codex(self, account_id: str, login: CodexLoginCallback) -> AccountProjection:
        account = await self.get(account_id)
        if account.provider != "codex":
            raise ValueError("A Codex account is required.")
        result = await login(CodexLoginRequest(replacing_shared_account=False))
        return await self._publish_login(account, result)

    async def login_grok(self, account_id: str, login: GrokLoginCallback) -> AccountProjection:
        account = await self.get(account_id)
        if account.provider != "grok":
            raise ValueError("A Grok account is required.")
        result = await login(
            GrokLoginRequest(scope=account.scope or DEFAULT_GROK_OAUTH_SCOPE, replacing_shared_account=False)
        )
        return await self._publish_login(account, result)

    async def _publish_login(
        self, initial: SubscriptionAccount, result: CodexLoginResult | GrokCredentials
    ) -> AccountProjection:
        identity = result.credentials.account_id if isinstance(result, CodexLoginResult) else result.account_id
        async with store_lock(self.path):
            snapshot, index = await self._read()
            account = await self.get(initial.id)
            if account.login_revision != initial.login_revision:
                raise AccountStoreConflictError(
                    "The account changed during login. Start login again.",
                    code="account_store_conflict",
                    provider=Provider(account.provider),
                )
            if account.upstream_account_id is not None and account.upstream_account_id != identity:
                raise AccountStoreError(
                    "Sign in to the same account, or add a new subscription account.",
                    code="account_identity_changed",
                    provider=Provider(account.provider),
                )
            store = self.store(account)
            projection: AccountProjection | None = None
            with CancelScope(shield=True):
                if isinstance(store, CodexAccountStore) and isinstance(result, CodexLoginResult):

                    async def codex_result(request: CodexLoginRequest) -> CodexLoginResult:
                        return result

                    projection = await store.login(codex_result)
                elif isinstance(store, GrokAccountStore) and isinstance(result, GrokCredentials):

                    async def grok_result(request: GrokLoginRequest) -> GrokCredentials:
                        return result

                    projection = await store.login(grok_result)
                else:
                    raise ValueError("The login result does not match the subscription provider.")
                index.accounts[account.id] = account.model_copy(
                    update={"upstream_account_id": identity, "login_revision": account.login_revision + 1}
                )
                await write_json_if_unchanged(
                    self.path, index.model_dump(mode="json"), snapshot.digest, provider=Provider(account.provider)
                )
            assert projection is not None
            return projection

    async def logout(self, account_id: str) -> bool:
        async with store_lock(self.path):
            snapshot, index = await self._read()
            account = await self.get(account_id)
            index.accounts[account_id] = account.model_copy(update={"login_revision": account.login_revision + 1})
            await write_json_if_unchanged(
                self.path, index.model_dump(mode="json"), snapshot.digest, provider=Provider(account.provider)
            )
            return await self.store(account).logout()

    async def adoption(self) -> dict[SubscriptionProvider, Adoption]:
        _, index = await self._read()
        return index.adoption
