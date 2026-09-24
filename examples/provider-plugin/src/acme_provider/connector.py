"""A fictional CRM Connector using one bounded native JSON API."""

from contextlib import asynccontextmanager
from typing import Literal

from a13n_harness.providers.authentication import Authentication, AuthenticationCase, CredentialMode
from a13n_harness.providers.connector import ConnectorProviderDefinition
from a13n_harness.providers.connector.contracts import (
    ConnectionBinding,
    ConnectionInspection,
    ConnectorTool,
    ConnectorToolOutcome,
    ConnectorToolPage,
    DiscoveredConnector,
    SetupCompletionMethod,
    SetupStarted,
)
from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    region: Literal["us", "eu"] = "us"
    access: Literal["private", "optional", "public"] = "private"


class Authorization(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    token: SecretStr = Field(json_schema_extra={"writeOnly": True})


class Credential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    authorization: Authorization
    revision: int = Field(ge=1)
    tier: Literal["sandbox", "live"]


class Provider:
    compatibility_profile = "acme_crm_v1"
    setup_replay_safe = False

    def __init__(self, configuration, credential, http):
        self.configuration, self.credential, self.http = configuration, credential, http

    async def request(self, method, path, *, body=None, write=False):
        return await self.http.request(
            method,
            endpoint=f"https://{self.configuration.region}.crm.acme.example",
            path=path,
            api_key=self.credential.authorization.token.get_secret_value() if self.credential else None,
            json_body=body,
            write=write,
            extra_headers={"X-Revision": str(self.credential.revision), "X-Tier": self.credential.tier}
            if self.credential
            else {},
        )

    async def test(self):
        await self.request("GET", "/account")
        return ("account_read",)

    async def discover_connectors(self):
        await self.request("GET", "/connectors")
        return (await self.discover_connector("crm"),)

    async def discover_connector(self, connector_key):
        if connector_key != "crm":
            raise ValueError("Unknown Connector")
        return DiscoveredConnector(
            key="crm", name="Acme CRM", setup_schema={"type": "object"}, authentication_methods=("oauth2",)
        )

    def tool_catalog(self, connector_key, *, provider_version=None):
        if provider_version not in {None, "v1"}:
            raise ValueError("Unsupported tool version")
        if connector_key != "crm":
            raise ValueError("Unknown Connector")
        return self

    async def discover_tools(self, *, cursor=None):
        return ConnectorToolPage(
            items=(
                ConnectorTool(
                    provider_version="v1",
                    key="lookup",
                    description="Find a CRM record",
                    input_schema={"type": "object"},
                ),
            ),
            provider_version="v1",
        )

    async def start_setup(self, *, setup, context, resume_ref=None, before_shared_setup=None, credentials=None):
        value = await self.request("POST", "/connections", body={"user": context.external_user_correlation}, write=True)
        return SetupStarted(
            setup_ref=value["id"],
            external_ref=value["id"],
            completion_method=SetupCompletionMethod.polling,
            expected_metadata={},
        )

    async def inspect_setup(self, *, setup_ref, context):
        return await self.connect(
            ConnectionBinding(
                connector_key=context.connector_key,
                external_ref=setup_ref,
                external_user_correlation=context.external_user_correlation,
            )
        ).inspect()

    async def complete_setup(self, *, session_uri, context, expected_external_ref):
        return await self.inspect_setup(setup_ref=expected_external_ref, context=context)

    def connect(self, binding):
        return Connection(self, binding)


class Connection:
    def __init__(self, provider, binding):
        self.provider, self.binding = provider, binding

    async def inspect(self):
        from a13n_harness.providers.connector.validation import path_segment

        value = await self.provider.request("GET", f"/connections/{path_segment(self.binding.external_ref)}")
        inspection = ConnectionInspection.model_validate(value)
        if (inspection.external_ref, inspection.connector_key, inspection.external_user_correlation) != (
            self.binding.external_ref,
            self.binding.connector_key,
            self.binding.external_user_correlation,
        ):
            raise ValueError("External account substitution")
        return inspection

    async def discover_tools(self, *, cursor=None):
        return await self.provider.discover_tools(cursor=cursor)

    async def execute_tool(self, *, tool_key, provider_version, arguments, request_id, before_dispatch=None):
        if provider_version != "v1" or tool_key != "lookup":
            raise ValueError("Unknown tool version")
        if before_dispatch is not None:
            await before_dispatch()
        result = await self.provider.request(
            "POST",
            "/execute",
            body={"account": self.binding.external_ref, "arguments": arguments, "request_id": request_id},
            write=True,
        )
        return ConnectorToolOutcome(kind="succeeded", result=result, request_id=request_id)

    async def revoke(self, *, operation_id):
        from a13n_harness.providers.connector.validation import path_segment

        await self.provider.request("DELETE", f"/connections/{path_segment(self.binding.external_ref)}", write=True)


@asynccontextmanager
async def open_provider(configuration, credential, http):
    yield Provider(configuration, credential, http)


def validate_setup(configuration, connector_key, value):
    if connector_key != "crm" or value != {}:
        raise ValueError("Invalid setup")
    return {}


acme_connector = ConnectorProviderDefinition(
    type="acme_connector",
    display_name="Acme CRM",
    configuration_model=Configuration,
    credential_model=Credential,
    setup_validator=validate_setup,
    open_provider=open_provider,
    authentication=Authentication(
        cases=(
            AuthenticationCase(field="access", equals="optional", mode=CredentialMode.optional),
            AuthenticationCase(field="access", equals="public", mode=CredentialMode.forbidden),
        )
    ),
    setup_url="https://crm.acme.example/settings",
    setup_label="Create CRM credentials",
)
