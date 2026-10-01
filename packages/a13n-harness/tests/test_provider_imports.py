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


def test_package_import_and_provider_metadata_stay_inert() -> None:
    # One interpreter runs the checks in order; the import block stays active for all of them.
    script = (
        "import a13n_harness\n"
        "assert 'websockets' not in sys.modules\n"
        # Domain metadata needs no optional SDK.
        "from a13n_harness.providers.plugins import ProviderManifest\n"
        "from a13n_harness.providers.connector.builtins import BUILT_IN_CONNECTOR_PROVIDERS\n"
        "from a13n_harness.providers.environment.builtins import BUILT_IN_ENVIRONMENT_PROVIDERS\n"
        "from a13n_harness.providers.memory.builtins import BUILT_IN_MEMORY_PROVIDERS\n"
        "from a13n_harness.providers.model.builtins import BUILT_IN_MODEL_PROVIDERS\n"
        "from a13n_harness.providers.web.builtins import built_in_web_providers\n"
        "manifest = ProviderManifest(api_version=1, environment=BUILT_IN_ENVIRONMENT_PROVIDERS)\n"
        "assert len(manifest.environment) == 11\n"
        "assert all(item.configuration_model.model_json_schema() for item in manifest.environment)\n"
        "assert BUILT_IN_CONNECTOR_PROVIDERS and BUILT_IN_MODEL_PROVIDERS\n"
        "chatgpt = next(item for item in BUILT_IN_MODEL_PROVIDERS if item.type == 'openai_chatgpt')\n"
        "assert chatgpt.configuration_model.model_json_schema()\n"
        "assert chatgpt.configuration_model().client_id is None\n"
        "assert all(item.configuration_model.model_json_schema() for item in BUILT_IN_MEMORY_PROVIDERS)\n"
        "assert built_in_web_providers()\n"
        # Selecting one built-in needs no other Provider runtime.
        "from a13n_harness.providers.catalog import ProviderCatalog\n"
        "from a13n_harness.providers.environment.builtins import select_builtin_environment_providers\n"
        "catalog = ProviderCatalog(select_builtin_environment_providers(('direct_local',)))\n"
        "assert catalog.require('direct_local').validate_environment({'root': {'path': '/tmp'}})\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", _BLOCK + script], capture_output=True, text=True, timeout=120, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
