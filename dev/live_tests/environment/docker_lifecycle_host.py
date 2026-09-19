"""Hold real Docker effects before the disposable Worker's Service publication."""

from dataclasses import replace
from pathlib import Path

from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.providers.environment.catalog import EnvironmentProviderCatalog
from a13n_harness.providers.environment.docker.provider import DOCKER

from .lifecycle_host import native_effect_barrier


def _barrier_definition(root):
    """Wrap the released definition's construction; the Provider itself stays immutable."""

    def construct(**arguments):
        environment = DOCKER.construct(**arguments)
        prepare, stop, destroy = environment._prepare, environment._stop, environment._destroy

        async def barrier(action):
            state = environment.dump_state()
            if state is not None:
                await native_effect_barrier(root, environment.environment_id, action, state.state["container_id"])

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

    return replace(DOCKER, construct=construct)


def environment_catalog(config, builtin_keys):
    root = Path(config["workspace_root"]).parent / "docker-fault"
    return EnvironmentProviderCatalog(
        (
            *select_builtin_environment_providers(tuple(key for key in builtin_keys if key != "docker")),
            _barrier_definition(root),
        )
    )
