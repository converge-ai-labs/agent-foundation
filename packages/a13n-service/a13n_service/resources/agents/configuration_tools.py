"""Nonsecret proposals for private Console actions; no credential values enter these schemas."""

from typing import Annotated, Literal

from a13n_harness.tools.client import ClientToolDefinition
from pydantic import BaseModel, ConfigDict, Field

from a13n_service.infra.ids import ObjectId
from a13n_service.resources.egress_credentials.schemas import CredentialDefinition, CredentialUpdate
from a13n_service.resources.subscriptions.schemas import SubscriptionConfiguration


class VersionedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: str = Field(min_length=1, description="The exact original id:version from the resource read.")


class WebhookCreate(SubscriptionConfiguration):
    action: Literal["create"]


class WebhookRotation(VersionedAction):
    action: Literal["rotate"]
    subscription_id: ObjectId


class WebhookConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: Annotated[WebhookCreate | WebhookRotation, Field(discriminator="action")]


class EgressCreate(CredentialDefinition):
    action: Literal["create"]


class EgressEdit(CredentialUpdate, VersionedAction):
    action: Literal["update"]
    credential_id: ObjectId


class EgressValueAction(VersionedAction):
    action: Literal["rotate", "revoke"]
    credential_id: ObjectId


class EgressSync(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["sync"]
    credential_id: ObjectId


class EgressConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: Annotated[EgressCreate | EgressEdit | EgressValueAction | EgressSync, Field(discriminator="action")]


INSTRUCTION = (
    "Propose only nonsecret fields. The current Console user reviews and executes the action with their own "
    "permission. Credentials stay in the form and its HTTP request. Existing-resource changes retain the original "
    "version; a conflict requires a new read and proposal. The result contains only status and resource identity. "
    "Cancelled means no request was sent; unknown means the outcome is uncertain. Never repeat an unknown write. "
    "After saved, reread the resource before claiming its state. Without a supported client, use the resource page."
)


def configuration_tools() -> tuple[ClientToolDefinition, ...]:
    return (
        ClientToolDefinition(
            name="configure_webhook",
            description="Ask an admin to create a Webhook or replace its signing key privately in Console.",
            parameters_json_schema=WebhookConfiguration.model_json_schema(),
            instruction=INSTRUCTION
            + " A generated signing key is displayed only to the user, never returned to the model.",
        ),
        ClientToolDefinition(
            name="configure_egress_credential",
            description="Ask a writer to create, edit, rotate, revoke or resynchronize an environment egress credential.",
            parameters_json_schema=EgressConfiguration.model_json_schema(),
            instruction=INSTRUCTION
            + " Saved is not proof of Session application. Read list_credential_sessions afterward. "
            "Revocation is final in Service and does not revoke the upstream token. App-managed credentials cannot be edited, rotated or revoked here.",
        ),
    )
