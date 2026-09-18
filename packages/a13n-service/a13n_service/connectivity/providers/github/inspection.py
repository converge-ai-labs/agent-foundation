"""Verify GitHub identity and discover only credential-accessible repositories."""

from urllib.parse import quote

import httpx2
from a13n_harness.providers.http import EndpointValidator, ProviderHttpError
from pydantic import JsonValue

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.inspection import (
    ConversationCandidate,
    ConversationInfo,
    ConversationPage,
    InstallationInfo,
)
from a13n_service.temporal import utc_now

from .adapter import GitHubAccountConfig, GitHubAccountCredentials
from .polling_config import GitHubPollingConfig, GitHubPollingCredentials
from .rest import GitHubREST
from .token import GitHubInstallationTokenProvider


class GitHubInspection:
    def __init__(
        self, http: httpx2.AsyncClient, endpoints: EndpointValidator, config: JsonObject, credentials: JsonObject | None
    ) -> None:
        self.config = (GitHubPollingConfig if "user_id" in config else GitHubAccountConfig).model_validate(config)
        self.rest = GitHubREST(http, endpoints, self.config.api_origin)
        self.tokens: GitHubInstallationTokenProvider | None = None
        self.personal_token: str | None = None
        if isinstance(self.config, GitHubPollingConfig):
            self.personal_token = GitHubPollingCredentials.model_validate(
                credentials
            ).personal_access_token.get_secret_value()
        else:
            secret = GitHubAccountCredentials.model_validate(credentials)
            self.tokens = GitHubInstallationTokenProvider(
                http,
                endpoints,
                api_origin=self.config.api_origin,
                app_id=self.config.app_id,
                installation_id=self.config.installation_id,
                private_key_pem=secret.app_private_key_pem.get_secret_value(),
                permissions={"issues": "write", "pull_requests": "write"},
            )

    async def installation(self) -> InstallationInfo:
        if isinstance(self.config, GitHubPollingConfig):
            assert self.personal_token is not None
            result = await discover_user(self.rest, self.personal_token)
            if result.bot_id != str(self.config.user_id):
                raise ProviderHttpError("bot_identity_mismatch")
            return result
        assert self.tokens is not None
        jwt = self.tokens.app_token(utc_now())
        app = await self.rest.object("/app", token=jwt)
        installation = await self.rest.object(f"/app/installations/{self.config.installation_id}", token=jwt)
        owner = installation.get("account")
        slug = app.get("slug")
        if not isinstance(owner, dict) or not isinstance(slug, str):
            raise ProviderHttpError("invalid_provider_response")
        bot = await self.rest.object(f"/users/{quote(slug + '[bot]', safe='')}", token=await self.tokens.token())
        if (app.get("id"), installation.get("app_id"), owner.get("id"), bot.get("id")) != (
            self.config.app_id,
            self.config.app_id,
            self.config.installation_account_id,
            self.config.bot_account_id,
        ):
            raise ProviderHttpError("bot_identity_mismatch")
        permissions = installation.get("permissions")
        enabled = (
            installation.get("suspended_at") is None
            and isinstance(permissions, dict)
            and all(permissions.get(key) == "write" for key in ("issues", "pull_requests"))
        )
        return InstallationInfo(
            app_id=str(self.config.app_id),
            organization_id=str(self.config.installation_account_id),
            organization_name=_name(owner, "login"),
            bot_id=str(self.config.bot_account_id),
            bot_name=_name(bot, "login"),
            enabled=enabled,
        )

    async def _token(self) -> str:
        if self.personal_token is not None:
            return self.personal_token
        assert self.tokens is not None
        return await self.tokens.token()

    async def conversation(self, repository_id: str) -> ConversationInfo:
        if (
            not repository_id.isascii()
            or not repository_id.isdecimal()
            or str(int(repository_id)) != repository_id
            or int(repository_id) < 1
        ):
            raise ProviderHttpError("invalid_binding")
        value = await self.rest.object(f"/repositories/{repository_id}", token=await self._token())
        if value.get("id") != int(repository_id):
            raise ProviderHttpError("invalid_binding")
        return ConversationInfo(
            id=repository_id,
            name=_name(value, "full_name"),
            audience="private" if value.get("private") is True else "public",
            is_member=True,
            is_active=value.get("archived") is False and value.get("disabled", False) is False,
            external=False,
        )

    async def conversations(self, *, limit: int, cursor: str | None) -> ConversationPage:
        try:
            page = int(cursor or "1")
            if not 1 <= page <= 10000:
                raise ValueError
        except ValueError:
            raise ProviderHttpError("invalid_cursor") from None
        response = await self.rest.request(
            "/user/repos" if self.personal_token else "/installation/repositories",
            token=await self._token(),
            params={"per_page": str(limit), "page": str(page)},
        )
        values = response.data.get("repositories") if isinstance(response.data, dict) else response.data
        if not isinstance(values, list) or len(values) > limit:
            raise ProviderHttpError("invalid_provider_response")
        items = []
        for value in values:
            if not isinstance(value, dict) or type(value.get("id")) is not int:
                raise ProviderHttpError("invalid_provider_response")
            items.append(ConversationCandidate(id=str(value["id"]), name=_name(value, "full_name")))
        return ConversationPage(
            items=tuple(items), cursor=str(page + 1) if 'rel="next"' in response.headers.get("link", "") else None
        )


async def discover_user(rest: GitHubREST, token: str) -> InstallationInfo:
    user = await rest.object("/user", token=token)
    if type(user.get("id")) is not int or user.get("type") != "User":
        raise ProviderHttpError("bot_identity_mismatch")
    # This also checks that the credential supports Notifications (classic PAT).
    response = await rest.request("/notifications", token=token, params={"per_page": "1"})
    if not isinstance(response.data, list):
        raise ProviderHttpError("invalid_provider_response")
    name, identity = _name(user, "login"), str(user["id"])
    return InstallationInfo(
        app_id=identity, organization_id=identity, organization_name=name, bot_id=identity, bot_name=name, enabled=True
    )


def _name(value: dict[str, JsonValue], key: str) -> str:
    name = value.get(key)
    if not isinstance(name, str) or not 1 <= len(name) <= 256:
        raise ProviderHttpError("invalid_provider_response")
    return name
