"""Provider metadata stays inert: describing a Provider imports no vendor SDK or Agent runtime."""

from __future__ import annotations

import subprocess
import sys

# Optional vendor SDKs, Service, and the Agent runtime that Provider metadata must never import.
_BLOCKED = (
    "docker",
    "e2b",
    "modal",
    "a13n_service",
    "pydantic_ai",
    "a13n_harness.builder",
    "a13n_harness.execution",
    "a13n_harness._output_contract",
    "a13n_harness.agent",
)
_BLOCK = f"""
import importlib.abc
import sys


class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith({_BLOCKED!r}):
            raise AssertionError(fullname)


sys.meta_path.insert(0, Block())
import httpx2

httpx2.AsyncClient = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("client created"))
"""


def _run(script: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", _BLOCK + script], capture_output=True, text=True, timeout=120, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_importing_the_package_stays_inert() -> None:
    _run("import a13n_harness\nassert 'websockets' not in sys.modules\n")


def test_domain_metadata_needs_no_optional_sdk() -> None:
    _run(
        "from a13n_harness.providers.plugins import ProviderManifest\n"
        "from a13n_harness.providers.connector.builtins import BUILT_IN_CONNECTOR_PROVIDERS\n"
        "from a13n_harness.providers.environment.builtins import BUILT_IN_ENVIRONMENT_PROVIDERS\n"
        "from a13n_harness.providers.model.builtins import BUILT_IN_MODEL_PROVIDERS\n"
        "from a13n_harness.providers.web.builtins import built_in_web_providers\n"
        "manifest = ProviderManifest(api_version=1, environment=BUILT_IN_ENVIRONMENT_PROVIDERS)\n"
        "assert len(manifest.environment) == 11\n"
        "assert all(item.configuration_model.model_json_schema() for item in manifest.environment)\n"
        "assert BUILT_IN_CONNECTOR_PROVIDERS and BUILT_IN_MODEL_PROVIDERS\n"
        "assert built_in_web_providers()\n"
    )


def test_selecting_one_builtin_needs_no_other_provider_runtime() -> None:
    _run(
        "from a13n_harness.providers.catalog import ProviderCatalog\n"
        "from a13n_harness.providers.environment.builtins import select_builtin_environment_providers\n"
        "catalog = ProviderCatalog(select_builtin_environment_providers(('direct_local',)))\n"
        "assert catalog.require('direct_local').validate_environment({'root': {'path': '/tmp'}})\n"
    )
