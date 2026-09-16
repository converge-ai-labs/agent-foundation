"""Read-only provider probes using the Account's exact encrypted installation."""

from __future__ import annotations

import httpx2
from pydantic import ValidationError

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.http import ConnectivityHttpError, EndpointValidator
from a13n_service.connectivity.inspection import ConversationInfo, ConversationPage, InstallationInfo
from a13n_service.connectivity.providers.lark.adapter import LarkAccountConfig, LarkAccountCredentials
from a13n_service.connectivity.providers.lark.client import LarkNativeClient
from a13n_service.connectivity.providers.lark.token import LarkTenantTokenProvider
from a13n_service.connectivity.providers.slack.adapter import SlackAccountConfig, SlackAccountCredentials
from a13n_service.connectivity.providers.slack.client import SlackNativeClient
from a13n_service.credentials import CredentialSnapshot
from a13n_service.secrets import SecretProtectionError, SecretProtector


class InstallationProbe:
    def __init__(
        self,
        http_client: httpx2.AsyncClient,
        endpoint_validator: EndpointValidator,
        *,
        provider_key: str,
        config_version: str,
        config: JsonObject,
        credentials: CredentialSnapshot,
        protector: SecretProtector,
    ) -> None:
        self._slack: tuple[SlackNativeClient, SlackAccountConfig, SlackAccountCredentials] | None = None
        self._lark: tuple[LarkNativeClient, LarkAccountConfig] | None = None
        try:
            if (provider_key, config_version) == ("slack", "slack_http_v1"):
                self._slack = (
                    SlackNativeClient(http_client),
                    SlackAccountConfig.model_validate(config),
                    SlackAccountCredentials.model_validate_json(credentials.decrypt(protector)),
                )
            elif (provider_key, config_version) == ("lark", "lark_http_v1"):
                parsed = LarkAccountConfig.model_validate(config)
                secret = LarkAccountCredentials.model_validate_json(credentials.decrypt(protector))
                tokens = LarkTenantTokenProvider(
                    http_client,
                    endpoint_validator,
                    open_api_origin=parsed.open_api_origin,
                    app_id=parsed.app_id,
                    app_secret=secret.app_secret.get_secret_value(),
                )
                self._lark = (
                    LarkNativeClient(http_client, endpoint_validator, tokens, open_api_origin=parsed.open_api_origin),
                    parsed,
                )
            else:
                raise ConnectivityHttpError("bot_provider_unsupported")
        except (ValidationError, SecretProtectionError) as error:
            raise ConnectivityHttpError("credential_unavailable") from error

    async def installation(self) -> InstallationInfo:
        if self._slack is not None:
            client, config, credentials = self._slack
            result = await client.inspect_installation(bot_token=credentials.bot_token.get_secret_value())
            matches = (
                result.app_id == config.api_app_id
                and result.organization_id == config.team_id
                and result.bot_id == config.bot_user_id
                and result.enterprise_id == config.enterprise_id
            )
        else:
            assert self._lark is not None
            client, config = self._lark
            result = await client.inspect_installation()
            matches = (
                result.app_id == config.app_id
                and result.organization_id == config.tenant_key
                and result.bot_id == config.bot_open_id
            )
        if not matches:
            raise ConnectivityHttpError("bot_identity_mismatch")
        return result

    async def conversation(self, conversation_id: str) -> ConversationInfo:
        if self._slack is not None:
            client, _config, credentials = self._slack
            return await client.inspect_conversation(
                conversation_id, bot_token=credentials.bot_token.get_secret_value()
            )
        assert self._lark is not None
        return await self._lark[0].inspect_conversation(conversation_id)

    async def conversations(self, *, limit: int, cursor: str | None) -> ConversationPage:
        if self._slack is not None:
            client, _config, credentials = self._slack
            return await client.list_conversations(
                bot_token=credentials.bot_token.get_secret_value(), limit=limit, cursor=cursor
            )
        assert self._lark is not None
        return await self._lark[0].list_conversations(limit=limit, cursor=cursor)
