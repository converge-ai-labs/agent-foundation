"""A connection's encrypted values: the credential it presents, the tokens of an OAuth credential, the secret of its
OAuth client and its pending browser authorization.

Each value is encrypted for its exact organization, row and column, so none can be copied to another
connection or read as another. Plaintext exists only in memory while a request or a run uses it.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Literal

from a13n_harness.providers.connector.contracts import SetupCompletionMethod
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from a13n_service.infra.crypto import Envelope, KeyRing, SecretLocation, secret_hash
from a13n_service.providers.tools.oauth import OAuthClient
from a13n_service.resources.connections.tables import ConnectionRow

type SecretColumn = Literal["credential", "tokens", "client_secret", "authorization"]


class HeadersSecret(BaseModel):
    """What `bearer` and `headers` connections present; a bearer token is its `authorization` header."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    headers: dict[str, str] = Field(repr=False)


class OAuthTokens(BaseModel):
    """The tokens an `oauth` credential's client was granted; renewal replaces them."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    access_token: str = Field(repr=False)
    refresh_token: str | None = Field(default=None, repr=False)


class OAuthClientSecret(BaseModel):
    """What an OAuth client registered in advance authenticates to the token endpoint with."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str = Field(repr=False)


class AccountSecret(BaseModel):
    """The connector provider's account; the provider keeps and refreshes the external tokens."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    account_ref: str = Field(repr=False)
    # The provider-side user the account belongs to; the connection's own ID.
    correlation: str


class _Flow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Who started it: the callback completes it with this principal's current authority.
    principal_id: str
    return_url: str | None
    # The hash of the secret the initiating browser holds in its flow cookie.
    browser: str


class OAuthFlow(_Flow):
    kind: Literal["oauth"] = "oauth"
    client: OAuthClient
    verifier: str = Field(repr=False)
    iss_required: bool


class AccountFlow(_Flow):
    kind: Literal["account"] = "account"
    account: AccountSecret
    completion: SetupCompletionMethod
    # None decodes old pending flows only; completion requires a fresh setup snapshot.
    expected_metadata: dict[str, str] | None = None


_FLOW: TypeAdapter[OAuthFlow | AccountFlow] = TypeAdapter(
    Annotated[OAuthFlow | AccountFlow, Field(discriminator="kind")]
)


@dataclass(frozen=True, slots=True)
class FlowStart:
    """What a browser authorization starts from: who starts it, the callback and where the browser returns."""

    principal_id: str
    return_url: str | None
    # The hash of the secret the initiating browser holds in its flow cookie.
    browser: str
    # The one-use state the callback brings back.
    state: str = field(repr=False)
    callback_url: str


def store_flow(keys: KeyRing, row: ConnectionRow, start: FlowStart, flow: BaseModel, expires_at: datetime) -> None:
    """Record the pending browser authorization, found by the hash of its one-use callback state."""
    row.authorization = protect(keys, row.organization_id, row.id, "authorization", flow)
    row.oauth_state_hash = secret_hash(start.state)
    row.authorization_expires_at = expires_at


def protect(
    keys: KeyRing, organization_id: str, connection_id: str, column: SecretColumn, value: BaseModel
) -> dict[str, JsonValue]:
    location = SecretLocation(organization_id, "connections", column, connection_id)
    return keys.protect(value.model_dump_json().encode(), location).model_dump(mode="json")


def reveal[M: BaseModel](
    keys: KeyRing, organization_id: str, connection_id: str, column: SecretColumn, envelope: dict, model: type[M]
) -> M:
    location = SecretLocation(organization_id, "connections", column, connection_id)
    return model.model_validate_json(keys.reveal(Envelope.model_validate(envelope), location))


def reveal_flow(keys: KeyRing, organization_id: str, connection_id: str, envelope: dict) -> OAuthFlow | AccountFlow:
    location = SecretLocation(organization_id, "connections", "authorization", connection_id)
    return _FLOW.validate_json(keys.reveal(Envelope.model_validate(envelope), location))
