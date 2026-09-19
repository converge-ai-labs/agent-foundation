"""Opt-in E2B lifecycle evidence and lifecycle barriers in the disposable test Host."""

from dataclasses import replace
from pathlib import Path

from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.providers.environment.e2b.provider import E2B, E2BEnvironment

from .lifecycle_host import native_effect_barrier


class LifecycleBarrierEnvironment(E2BEnvironment):
    fault_root: Path

    async def _open_operations(self, sandbox, mount_id):
        await native_effect_barrier(self.fault_root, self.environment_id, "prepare", sandbox.sandbox_id)
        await super()._open_operations(sandbox, mount_id)

    async def _stop(self):
        state = self.dump_state()
        await super()._stop()
        if state is not None:
            await native_effect_barrier(self.fault_root, self.environment_id, "stop", state.state["sandbox_id"])

    async def _destroy(self):
        state = self.dump_state()
        await super()._destroy()
        if state is not None:
            await native_effect_barrier(self.fault_root, self.environment_id, "delete", state.state["sandbox_id"])


def _barrier_definition(root):
    """Wrap the released definition's construction; the Provider itself stays immutable."""

    def construct(*, configuration, environment_id, state, runtime):
        # Reuse the production factory's type validation before constructing the test adapter.
        E2B.construct(configuration=configuration, environment_id=environment_id, state=state, runtime=runtime)
        environment = LifecycleBarrierEnvironment(
            configuration, environment_id=environment_id, state=state, runtime=runtime
        )
        environment.fault_root = root
        return environment

    return replace(E2B, construct=construct)


def environment_catalog(config, builtin_keys):
    root = Path(config["workspace_root"]).parent / "e2b-fault"
    return ProviderCatalog(
        (
            *select_builtin_environment_providers(tuple(key for key in builtin_keys if key != "e2b")),
            _barrier_definition(root),
        )
    )
