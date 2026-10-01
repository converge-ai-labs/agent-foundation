"""Browser-bound hosted SIWC completion, reusing the Model Provider's one-shot grant.

The authenticated cookie carries only a bounded locator, state and initiating
principal. PKCE and token material remain in the encrypted durable provider row.
"""

import base64
import secrets
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

from a13n_harness.providers.endpoint_policy import EndpointPolicy
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from a13n_service.infra.crypto import Envelope, KeyRing, SecretLocation, secret_hash
from a13n_service.infra.db import Storage, short_session
from a13n_service.infra.errors import ServiceError, invalid
from a13n_service.resources.providers import oauth
from a13n_service.settings import Providers
from a13n_service.tenancy.access import Access, principal_for, require_login_session
from a13n_service.tenancy.authorize import Principal

CALLBACK_PATH = "/api/v1/model-providers/oauth/callback"


class BrowserFlow(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    principal_id: str
    workspace_id: str
    provider_id: str
    attempt_id: str
    state: str = Field(repr=False)
    redirect_uri: str
    expires_at: datetime


def cookie_name(state: str) -> str:
    return f"__Secure-a13n_chatgpt_{secret_hash(state)}"


def cookie_path(redirect_uri: str) -> str:
    return urlsplit(redirect_uri).path


def _location(state: str) -> SecretLocation:
    # A browser flow locator is not itself a tenant credential. Its use case
    # rechecks the principal and the named workspace/provider before consumption.
    return SecretLocation(None, "model_provider_oauth", "browser_callback", state)


def browser_cookie(
    start: oauth.AuthorizationStart, actor: Principal, workspace_id: str, provider_id: str, *, keys: KeyRing
) -> tuple[str, str]:
    require_login_session(actor)
    params = parse_qs(urlsplit(start.authorization_url).query)
    state = params["state"][0]
    flow = BrowserFlow(
        principal_id=actor.id,
        workspace_id=workspace_id,
        provider_id=provider_id,
        attempt_id=start.attempt_id,
        state=state,
        redirect_uri=params["redirect_uri"][0],
        expires_at=start.expires_at,
    )
    envelope = keys.protect(flow.model_dump_json().encode(), _location(state))
    return cookie_name(state), base64.urlsafe_b64encode(envelope.model_dump_json().encode()).decode()


async def complete(
    storage: Storage,
    access: Access,
    *,
    query: str,
    cookie: str | None,
    keys: KeyRing,
    settings: Providers,
    policy: EndpointPolicy,
    public_origin: str,
) -> tuple[oauth.AuthorizationStatus, str]:
    if len(query) > 16384 or cookie is None or len(cookie) > 4096:
        raise invalid("callback", "no matching browser authorization")
    try:
        params = parse_qs(query, keep_blank_values=True, strict_parsing=True)
        state = params["state"][0]
        if len(params["state"]) != 1:
            raise ValueError("duplicate state")
        envelope = Envelope.model_validate_json(base64.b64decode(cookie, altchars=b"-_", validate=True))
        flow = BrowserFlow.model_validate_json(keys.reveal(envelope, _location(state)))
        target = urlsplit(flow.redirect_uri)
        if (
            target.scheme != "https"
            or f"{target.scheme}://{target.netloc.lower().removesuffix(':443')}" != public_origin
            or not secrets.compare_digest(flow.state, state)
            or flow.expires_at.tzinfo is None
            or datetime.now(UTC) >= flow.expires_at
        ):
            raise ValueError("browser authorization mismatch")
    except (KeyError, ValueError, ServiceError):
        raise invalid("callback", "no matching browser authorization") from None
    # The login cookie is SameSite=Strict, so an issuer's cross-site redirect
    # authenticates with this Lax flow cookie instead. Reload the initiator's
    # current status/grants; the normal completion use case rechecks provider write.
    async with short_session(storage) as session:
        actor = await principal_for(session, access, flow.principal_id)
    require_login_session(actor)
    # The frozen registered URI, never Host/Forwarded headers, is the exact target.
    status = await oauth.complete(
        storage,
        actor,
        flow.workspace_id,
        flow.provider_id,
        oauth.AuthorizationCallback(
            attempt_id=flow.attempt_id,
            callback_url=SecretStr(flow.redirect_uri + "?" + query),
        ),
        keys=keys,
        policy=policy,
        settings=settings,
    )
    return status, flow.redirect_uri
