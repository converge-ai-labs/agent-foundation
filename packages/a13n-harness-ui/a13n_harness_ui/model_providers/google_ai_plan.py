"""Package-owned Google AI Plan integration through the Host account store."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from a13n_harness.providers.model.google_ai_plan import MODEL_SUGGESTIONS
from a13n_harness.providers.model.oauth.google_ai_plan import (
    CALLBACK_PATH,
    LOGIN_TIMEOUT,
    SCHEME,
    GoogleAIPlanClient,
    GoogleAIPlanCredentials,
    GoogleAIPlanFlow,
    refresh_google_ai_plan,
)
from a13n_harness.providers.model.oauth.models import ModelAuthenticationError

from .sdk import (
    AuthorizedAccount,
    BuildModelContext,
    CallbackChallenge,
    LoopbackCallbackSpec,
    ModelProviderDeclaration,
    ModelProviderPlugin,
    OAuthClientUnconfigured,
    OAuthContext,
    PluginModelSuggestion,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic_ai.models import Model


class GoogleAIPlanPlugin(ModelProviderPlugin):
    def __init__(self, registration: GoogleAIPlanClient | None = None) -> None:
        self._registration = registration

    def declaration(self) -> ModelProviderDeclaration:
        return ModelProviderDeclaration(
            provider_key=SCHEME,
            display_name="Google AI Plan",
            login_methods=("browser", "manual_callback"),
            loopback_callback=LoopbackCallbackSpec(path=CALLBACK_PATH),
            allowed_origins=(
                "https://accounts.google.com",
                "https://oauth2.googleapis.com",
                "https://www.googleapis.com",
                "https://generativelanguage.googleapis.com",
            ),
            models=tuple(PluginModelSuggestion(model_id=key, label=label) for key, label in MODEL_SUGGESTIONS),
            default_model=MODEL_SUGGESTIONS[0][0],
        )

    def _client(self) -> GoogleAIPlanClient:
        if self._registration is not None:
            return self._registration
        try:
            return GoogleAIPlanClient.configured(
                os.environ.get("A13N_GOOGLE_AI_PLAN_CLIENT_ID"),
                os.environ.get("A13N_GOOGLE_AI_PLAN_CLIENT_SECRET"),
            )
        except ModelAuthenticationError:
            raise OAuthClientUnconfigured from None

    def _flow(self, ctx: OAuthContext) -> GoogleAIPlanFlow:
        if ctx.redirect_uri is None or ctx.pkce is None:
            raise ValueError("Google AI Plan requires a Host callback and PKCE context.")
        return GoogleAIPlanFlow(
            self._client(),
            ctx.http_client,
            redirect_uri=ctx.redirect_uri,
            state=ctx.pkce.state,
            code_verifier=ctx.pkce.verifier,
            nonce=ctx.pkce.nonce,
        )

    async def begin_callback_login(self, ctx: OAuthContext) -> CallbackChallenge:
        flow = self._flow(ctx)
        return CallbackChallenge(
            authorization_url=flow.authorization_url(), expires_in=LOGIN_TIMEOUT, state=flow.registration.client_id
        )

    async def complete_callback_login(
        self,
        ctx: OAuthContext,
        challenge: CallbackChallenge,
        callback_params: Mapping[str, str],
    ) -> AuthorizedAccount:
        flow = self._flow(ctx)
        if challenge.state != flow.registration.client_id:
            raise ValueError("Google AI Plan registration changed during sign-in.")
        return _account(await flow.exchange_code(callback_params["code"]))

    async def refresh_credentials(self, ctx: OAuthContext, current: AuthorizedAccount) -> AuthorizedAccount:
        credentials = GoogleAIPlanCredentials.model_validate(current.credentials)
        if credentials.account_id != current.upstream_account_id:
            raise ValueError("Google AI Plan account binding does not match the credentials.")
        return _account(await refresh_google_ai_plan(self._client(), ctx.http_client, credentials))

    def build_model(self, ctx: BuildModelContext) -> Model:
        from a13n_harness.models.google_ai_plan import GoogleAIPlanModel, GoogleAIPlanProvider

        self._client()
        return GoogleAIPlanModel(ctx.model_id, provider=GoogleAIPlanProvider(ctx.http_client, auth=ctx.bearer_auth()))


def _account(credentials: GoogleAIPlanCredentials) -> AuthorizedAccount:
    return AuthorizedAccount(
        upstream_account_id=credentials.account_id,
        credentials=credentials.model_dump(mode="json"),
        expires_at=credentials.expires_at,
    )
