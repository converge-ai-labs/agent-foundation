"""Host-selected memory backend factories, separate from behavior Capabilities."""

from __future__ import annotations

import importlib.metadata
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Mapping
from contextlib import AbstractAsyncContextManager
from types import MappingProxyType
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from a13n_harness._urls import require_http_url
from a13n_harness.memory import MemoryBackend

MEMORY_BACKEND_ENTRY_POINT_GROUP = "a13n_harness.memory_backends"


class MemoryBackendPlugin[Configuration: BaseModel, Credential: BaseModel](ABC):
    """Inert factory with pure configuration and independent credential validation.

    Schema validation must not perform I/O. ``open`` returns a host-owned lifetime;
    no backend, credential, or client belongs in AgentSpec or HarnessState.
    """

    key: ClassVar[str]
    display_name: ClassVar[str]
    configuration_model: type[Configuration]
    credential_model: type[Credential]
    supports_documents: bool = False

    @abstractmethod
    def open(
        self, configuration: Configuration, credential: Credential
    ) -> AbstractAsyncContextManager[MemoryBackend]: ...


class Mem0OSSConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str = Field(min_length=1, max_length=2048)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parsed = require_http_url(value)
        if parsed.query or parsed.fragment or value != value.strip():
            raise ValueError("Memory base URLs must not contain queries, fragments, or surrounding whitespace")
        return value.rstrip("/")


class Mem0PlatformConfiguration(Mem0OSSConfiguration):
    base_url: str = "https://api.mem0.ai"


class Mem0Credential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    api_key: SecretStr = Field(min_length=1)

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("The API key cannot be blank")
        return value


class Mem0OSSBackendPlugin(MemoryBackendPlugin[Mem0OSSConfiguration, Mem0Credential]):
    key = "a13n.mem0-oss"
    display_name = "Mem0 OSS"
    supports_documents = True
    configuration_model = Mem0OSSConfiguration
    credential_model = Mem0Credential

    def open(
        self, configuration: Mem0OSSConfiguration, credential: Mem0Credential
    ) -> AbstractAsyncContextManager[MemoryBackend]:
        from a13n_harness.capabilities.mem0_backends import open_mem0_oss

        return open_mem0_oss(base_url=configuration.base_url, api_key=credential.api_key.get_secret_value())


class Mem0PlatformBackendPlugin(MemoryBackendPlugin[Mem0PlatformConfiguration, Mem0Credential]):
    key = "a13n.mem0-platform"
    display_name = "Mem0 Platform"
    supports_documents = True
    configuration_model = Mem0PlatformConfiguration
    credential_model = Mem0Credential

    def open(
        self, configuration: Mem0PlatformConfiguration, credential: Mem0Credential
    ) -> AbstractAsyncContextManager[MemoryBackend]:
        from a13n_harness.capabilities.mem0_backends import open_mem0_platform

        return open_mem0_platform(base_url=configuration.base_url, api_key=credential.api_key.get_secret_value())


class MemoryBackendCatalog(Mapping[str, MemoryBackendPlugin[Any, Any]]):
    """Immutable, caller-owned snapshot; installation alone never enables a plugin."""

    __slots__ = ("_plugins",)

    def __init__(self, plugins: Iterable[MemoryBackendPlugin[Any, Any]] = ()) -> None:
        selected: dict[str, MemoryBackendPlugin[Any, Any]] = {}
        for plugin in plugins:
            if not isinstance(plugin, MemoryBackendPlugin):
                raise TypeError("Memory backend entries must implement MemoryBackendPlugin")
            key = _key(plugin.key)
            if key in selected:
                raise ValueError(f"Duplicate memory backend key: {key}")
            if type(plugin.supports_documents) is not bool:
                raise TypeError("Memory document support must be a boolean")
            if not plugin.display_name.strip():
                raise ValueError("Memory backend display name cannot be empty")
            if not issubclass(plugin.configuration_model, BaseModel) or not issubclass(
                plugin.credential_model, BaseModel
            ):
                raise TypeError("Memory backend schemas must be Pydantic models")
            selected[key] = plugin
        self._plugins = MappingProxyType(selected)

    def __getitem__(self, key: str) -> MemoryBackendPlugin[Any, Any]:
        return self._plugins[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._plugins)

    def __len__(self) -> int:
        return len(self._plugins)


def _key(value: str) -> str:
    if not isinstance(value, str) or len(value) > 128 or not re.fullmatch(r"[a-z0-9]+(?:[._-][a-z0-9]+)+", value):
        raise ValueError("Invalid memory backend key")
    return value


def build_memory_backend_catalog(
    *,
    builtin_keys: Iterable[str] = (),
    extension_keys: Iterable[str] = (),
    explicit_plugins: Iterable[MemoryBackendPlugin[Any, Any]] = (),
) -> MemoryBackendCatalog:
    """Load only explicitly selected entry points, rejecting every key collision."""
    builtins = {
        Mem0OSSBackendPlugin.key: Mem0OSSBackendPlugin,
        Mem0PlatformBackendPlugin.key: Mem0PlatformBackendPlugin,
    }
    builtin_keys = tuple(_key(key) for key in builtin_keys)
    extension_keys = tuple(_key(key) for key in extension_keys)
    explicit = MemoryBackendCatalog(explicit_plugins)
    selected_keys = (*builtin_keys, *extension_keys, *explicit)
    if len(set(selected_keys)) != len(selected_keys):
        raise ValueError("Duplicate memory backend keys")
    if any(key not in builtins for key in builtin_keys):
        raise ValueError("Unknown built-in memory backend")
    entries = importlib.metadata.entry_points(group=MEMORY_BACKEND_ENTRY_POINT_GROUP) if extension_keys else ()
    selected_entries = []
    for key in extension_keys:
        matches = [entry for entry in entries if entry.name == key]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one installed memory backend for {key}")
        selected_entries.append((key, matches[0]))
    plugins: list[MemoryBackendPlugin[Any, Any]] = [builtins[key]() for key in builtin_keys]
    for key, entry in selected_entries:
        target = entry.load()
        if not isinstance(target, type) or not issubclass(target, MemoryBackendPlugin):
            raise TypeError("Memory backend entry points must export a MemoryBackendPlugin class")
        plugin = target()
        if plugin.key != key:
            raise ValueError("Memory backend entry point and implementation keys differ")
        plugins.append(plugin)
    return MemoryBackendCatalog((*plugins, *explicit.values()))
