import subprocess
import sys

from a13n_harness.providers.plugins import load_provider_plugins


def test_installed_entry_point_and_direct_import_share_definition() -> None:
    from acme_provider.environment import acme_environment

    plugin = load_provider_plugins(("acme",))[0]
    assert plugin.distribution_name == "a13n-provider-acme-example"
    assert plugin.manifest.environment == (acme_environment,)
    assert plugin.manifest.environment[0] is acme_environment


def test_installed_loading_does_not_import_agent_runtime_or_optional_sdks() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib.abc
import sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("pydantic_ai", "a13n_harness.execution", "a13n_harness.agent")):
            raise AssertionError(fullname)
sys.meta_path.insert(0, Block())
from a13n_harness.providers.plugins import load_provider_plugins
manifest = load_provider_plugins(("acme",))[0].manifest
assert manifest.environment[0].type == "acme_workspace"
assert not {"docker", "e2b", "modal"} & {name.split(".")[0] for name in sys.modules}
""",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
