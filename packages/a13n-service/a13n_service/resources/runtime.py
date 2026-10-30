"""The assembled runtime as resource operations use it, extending tenancy's dependencies."""

from typing import Protocol

from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from a13n_harness.providers.endpoint_policy import EndpointPolicy

from a13n_service.providers.metrics import Queries
from a13n_service.providers.registry import Registry
from a13n_service.resources.builtin_skills import BuiltinPackage, BuiltinSkillName
from a13n_service.tenancy import runtime


class Runtime(runtime.Runtime, Protocol):
    @property
    def builtin_skills(self) -> dict[BuiltinSkillName, BuiltinPackage]: ...

    @property
    def metrics_queries(self) -> Queries: ...

    @property
    def registry(self) -> Registry: ...

    @property
    def plugins(self) -> HarnessPluginFactoryCatalog: ...

    @property
    def endpoint_policy(self) -> EndpointPolicy: ...
