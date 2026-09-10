"""Opt-in E2B lifecycle evidence and lifecycle barriers in the disposable test Host."""

from pathlib import Path

from a13n_environment import E2BEnvironment, E2BEnvironmentProvider, build_environment_provider_catalog

from .lifecycle_host import native_effect_barrier


class LifecycleBarrierEnvironment(E2BEnvironment):
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


class LifecycleBarrierProvider(E2BEnvironmentProvider):
    def __init__(self, root):
        self.root = root

    def create_environment(self, *, configuration, environment_id, state, runtime=None):
        # Reuse the production factory's type validation before constructing the test adapter.
        super().create_environment(
            configuration=configuration, environment_id=environment_id, state=state, runtime=runtime
        )
        environment = LifecycleBarrierEnvironment(
            configuration, environment_id=environment_id, state=state, runtime=runtime
        )
        environment.fault_root = self.root
        return environment


def environment_catalog(config, builtin_keys):
    root = Path(config["workspace_root"]).parent / "e2b-fault"
    return build_environment_provider_catalog(
        builtin_keys=tuple(key for key in builtin_keys if key != "a13n.e2b"),
        explicit_providers=(LifecycleBarrierProvider(root),),
    )
