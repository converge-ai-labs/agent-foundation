"""Environment metadata remains usable outside Harness and without vendor SDKs."""

import subprocess
import sys
import textwrap


def test_public_contracts_and_builtins_do_not_import_harness_or_vendor_sdks() -> None:
    script = textwrap.dedent("""
        import importlib.abc
        import sys

        forbidden = {"a13n_harness", "a13n_service", "a13n_harness_ui", "pydantic_ai", "docker", "e2b", "modal"}
        class BlockImports(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.split(".")[0] in forbidden:
                    raise AssertionError(f"Environment metadata imported {fullname}")
        sys.meta_path.insert(0, BlockImports())
        from a13n_environment import EnvironmentConnector, EnvironmentExecution, EnvironmentProvider
        from a13n_environment.builtins import BUILT_IN_ENVIRONMENT_PROVIDERS
        assert len(BUILT_IN_ENVIRONMENT_PROVIDERS) == 11
        for definition in BUILT_IN_ENVIRONMENT_PROVIDERS:
            definition.configuration_model.model_json_schema()
            definition.environment_model.model_json_schema()
            if definition.credential_model:
                definition.credential_model.model_json_schema()
        assert not forbidden.intersection(name.split(".")[0] for name in sys.modules)
    """)
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
