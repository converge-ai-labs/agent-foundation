"""Hold real Docker effects before the disposable Worker's Service publication."""

from pathlib import Path

from a13n_environment import DockerEnvironmentProvider, build_environment_provider_catalog

from .lifecycle_host import native_effect_barrier


class LifecycleBarrierProvider(DockerEnvironmentProvider):
    def __init__(self, root):
        self.root = root

    def create_environment(self, **arguments):
        environment = super().create_environment(**arguments)
        prepare, stop, destroy = environment._prepare, environment._stop, environment._destroy

        async def barrier(action):
            state = environment.dump_state()
            if state is not None:
                await native_effect_barrier(self.root, environment.environment_id, action, state.state["container_id"])

        async def open_after_create(*args, **kwargs):
            await prepare(*args, **kwargs)
            await barrier("prepare")

        async def stop_before_publication():
            await stop()
            await barrier("stop")

        async def destroy_before_publication():
            await destroy()
            await barrier("delete")

        environment._prepare = open_after_create
        environment._stop = stop_before_publication
        environment._destroy = destroy_before_publication
        return environment


def environment_catalog(config, builtin_keys):
    root = Path(config["workspace_root"]).parent / "docker-fault"
    return build_environment_provider_catalog(
        builtin_keys=tuple(key for key in builtin_keys if key != "docker"),
        explicit_providers=(LifecycleBarrierProvider(root),),
    )
