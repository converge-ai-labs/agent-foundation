"""Optional, machine-private resources for the local Service Workspace."""

from __future__ import annotations

import os
import stat
import tomllib
from pathlib import Path

from a13n_harness.providers.authentication import CredentialMode
from a13n_service.connectivity.connectors.domain import CreateConnectorProviderRequest
from a13n_service.credentials import credential_payload
from a13n_service.environments.domain import CreateProviderRequest, CreateTemplateRequest
from a13n_service.models.domain import CreateModelProviderRequest, CreateModelRequest
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.models.service_common import ModelError
from a13n_service.web.domain import CreateWebProviderRequest
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

DEFAULT_PATH = Path.home() / ".a13n/dev-resources.toml"
MAX_BYTES = 65_536


class DevelopmentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    name: str
    upstream_model: str
    model_api: str | None = None
    enabled: bool = True


class DevelopmentProvider(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    name: str
    credential: dict[str, object] | None = Field(default=None, repr=False)
    configuration: dict[str, object] = Field(default_factory=dict)
    models: tuple[DevelopmentModel, ...]


class DevelopmentWebProvider(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    name: str
    credential: dict[str, SecretStr]
    configuration: dict[str, object] = Field(default_factory=dict)


class DevelopmentEnvironmentProvider(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    name: str
    configuration: dict[str, object] = Field(default_factory=dict)
    credential: dict[str, SecretStr] = Field(default_factory=dict)


class DevelopmentEnvironmentTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    provider: str
    configuration: dict[str, object]
    access: str = "full"
    preparation: str = "on_run"
    stop_after: int | None = None
    delete_after: int | None = None


class DevelopmentConnectorProvider(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    name: str
    configuration: dict[str, object] = Field(default_factory=dict)
    credentials: dict[str, SecretStr]


class DevelopmentResources(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    model_providers: tuple[DevelopmentProvider, ...] = ()
    web_providers: tuple[DevelopmentWebProvider, ...] = ()
    environment_providers: tuple[DevelopmentEnvironmentProvider, ...] = ()
    environment_templates: tuple[DevelopmentEnvironmentTemplate, ...] = ()
    connector_providers: tuple[DevelopmentConnectorProvider, ...] = ()


def _revealed(values: dict[str, SecretStr]) -> dict[str, str]:
    return {key: value.get_secret_value() for key, value in values.items()}


def _filled(values: dict[str, SecretStr]) -> bool:
    return bool(values) and all(value.get_secret_value().strip() for value in values.values())


def _model_credential(provider: DevelopmentProvider) -> dict[str, object] | None:
    value = provider.credential
    if not value or any(isinstance(item, str) and not item.strip() for item in value.values()):
        return None
    definition = built_in_provider_registry().integration(provider.type)
    return credential_payload(definition.credential_model.model_validate(value))


def _model_requires_credential(provider: DevelopmentProvider) -> bool:
    definition = built_in_provider_registry().integration(provider.type)
    configuration = definition.configuration_model.model_validate(provider.configuration)
    return definition.authentication.resolve(configuration) is CredentialMode.required


def _active(resources: DevelopmentResources) -> bool:
    unavailable = {
        provider.name.casefold()
        for provider in resources.environment_providers
        if provider.credential and not _filled(provider.credential)
    }
    return bool(
        any(
            _model_credential(provider) or not _model_requires_credential(provider)
            for provider in resources.model_providers
        )
        or any(_filled(provider.credential) for provider in resources.web_providers)
        or any(provider.name.casefold() not in unavailable for provider in resources.environment_providers)
        or any(template.provider.casefold() not in unavailable for template in resources.environment_templates)
        or any(_filled(provider.credentials) for provider in resources.connector_providers)
    )


def load_resources(path: Path = DEFAULT_PATH) -> DevelopmentResources | None:
    """Read only an owned private regular file; never echo its contents in errors."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ValueError(f"Cannot open private development resources file: {path}") from error
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
            raise ValueError(f"Development resources file must be regular and mode 0600: {path}")
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise ValueError(f"Development resources file must be owned by this user: {path}")
        if metadata.st_size > MAX_BYTES:
            raise ValueError(f"Development resources file is too large: {path}")
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            content = stream.read(MAX_BYTES + 1)
    finally:
        if fd >= 0:
            os.close(fd)
    if len(content) > MAX_BYTES:
        raise ValueError(f"Development resources file is too large: {path}")
    try:
        resources = DevelopmentResources.model_validate(tomllib.loads(content.decode("utf-8")))
    except (UnicodeError, tomllib.TOMLDecodeError, ValidationError):
        raise ValueError(f"Invalid development resources file: {path}") from None
    if resources.version != 1:
        raise ValueError(f"Unsupported development resources version: {path}")
    registry = built_in_provider_registry()
    names: set[str] = set()
    keys: set[str] = set()
    for provider in resources.model_providers:
        name = provider.name.casefold()
        if name in names:
            raise ValueError(f"Duplicate development Model Provider name: {path}")
        names.add(name)
        try:
            definition = registry.definition(provider.type)
            CreateModelProviderRequest.model_validate(
                {
                    "type": provider.type,
                    "name": provider.name,
                    "credential": _model_credential(provider),
                    "configuration": provider.configuration,
                }
            )
            for model in provider.models:
                api = model.model_api or definition.default_model_api
                registry.validate_model_api(provider.type, api)
                CreateModelRequest.model_validate(
                    {
                        "key": model.key,
                        "name": model.name,
                        "provider_id": "mprov_00000000000000000000",
                        "upstream_model": model.upstream_model,
                        "model_api": api,
                    }
                )
                key = model.key.casefold()
                if key in keys:
                    raise ValueError("duplicate key")
                keys.add(key)
        except (ModelError, ValueError, ValidationError):
            raise ValueError(f"Invalid development Model Provider or Model: {path}") from None
    for section in (
        resources.web_providers,
        resources.environment_providers,
        resources.environment_templates,
        resources.connector_providers,
    ):
        seen: set[str] = set()
        for item in section:
            name = item.name.casefold()
            if name in seen:
                raise ValueError(f"Duplicate development resource name: {path}")
            seen.add(name)
    try:
        for provider in resources.web_providers:
            CreateWebProviderRequest.model_validate(
                {
                    "type": provider.type,
                    "name": provider.name,
                    "configuration": provider.configuration,
                    "credential": _revealed(provider.credential),
                }
            )
        for provider in resources.environment_providers:
            CreateProviderRequest.model_validate(
                {
                    "type": provider.type,
                    "name": provider.name,
                    "configuration": provider.configuration,
                    "credential": _revealed(provider.credential) or None,
                }
            )
        for template in resources.environment_templates:
            CreateTemplateRequest.model_validate(
                {
                    "name": template.name,
                    "provider_id": "eprov_00000000000000000000",
                    "configuration": template.configuration,
                    "access": template.access,
                    "preparation": template.preparation,
                    "retention": {"idle": {"stop_after": template.stop_after, "delete_after": template.delete_after}},
                }
            )
        for provider in resources.connector_providers:
            if _filled(provider.credentials):
                CreateConnectorProviderRequest.model_validate(
                    {
                        "type": provider.type,
                        "name": provider.name,
                        "configuration": provider.configuration,
                        "credentials": _revealed(provider.credentials),
                    }
                )
    except (ValueError, ValidationError):
        raise ValueError(f"Invalid development resource: {path}") from None
    return resources
