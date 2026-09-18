"""Inert native Connector definitions."""

from contextlib import asynccontextmanager

from .composio.configuration import ComposioConfiguration, validate_setup
from .configuration import ApiKeyCredentials
from .definition import ConnectorProviderDefinition
from .http import ConnectorHttpClient


@asynccontextmanager
async def _open(configuration: ComposioConfiguration, credential: ApiKeyCredentials | None, http: ConnectorHttpClient):
    from .composio.runtime import ComposioProvider

    assert credential is not None
    yield ComposioProvider(http, credential)


COMPOSIO = ConnectorProviderDefinition(
    type="composio",
    display_name="Composio",
    configuration_model=ComposioConfiguration,
    credential_model=ApiKeyCredentials,
    setup_validator=validate_setup,
    open_provider=_open,
    setup_url="https://platform.composio.dev/",
    setup_label="Composio dashboard",
)
BUILT_IN_CONNECTOR_PROVIDERS = (COMPOSIO,)
