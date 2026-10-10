"""Harness selects mounts without placing aliases in standalone port requests."""

from datetime import UTC, datetime

import pytest
from a13n_environment.commands import PortObservation, PortTarget
from a13n_environment.models import EnvironmentAction, EnvironmentError, EnvironmentPermissionSet
from a13n_environment.operations import EnvironmentOperations
from a13n_harness.environment import EnvironmentMount
from a13n_harness.environment.advanced import create_environment_runtime
from pydantic import ValidationError

from .test_environment_core import _Binding, _instance

pytestmark = pytest.mark.anyio


class Ports:
    def __init__(self):
        self.calls = []

    async def inspect(self, target):
        self.calls.append(("inspect", target))
        return PortObservation(target=target, status="listening", observed_at=datetime.now(UTC))

    async def wait(self, target, **kwargs):
        self.calls.append(("wait", target))
        return PortObservation(target=target, status=kwargs["desired"], observed_at=datetime.now(UTC))


async def test_port_alias_stays_in_harness_routing():
    ports = {name: Ports() for name in ("first", "second")}
    actions = frozenset({EnvironmentAction.PORT_INSPECT, EnvironmentAction.PORT_WAIT})
    runtime = create_environment_runtime(
        mounts={
            name: EnvironmentMount(
                source=_Binding(
                    name,
                    families=frozenset({"ports"}),
                    operations=EnvironmentOperations(ports=facet),
                    permissions=actions,
                ),
                permission_ceiling=EnvironmentPermissionSet(operations=actions),
            )
            for name, facet in ports.items()
        },
        default_mount="first",
    )
    target = PortTarget(port=8080)
    async with runtime.bind(thread_id="thread-test", run_id="run-test", instance=_instance(), host_refs={}) as scope:
        await scope.ports.inspect(target)
        await scope.ports.inspect(target, alias="second")
        await scope.ports.wait(target, alias="second", desired="not_listening", timeout_seconds=1)
        with pytest.raises(EnvironmentError):
            await scope.ports.inspect(target, alias="absent")
    assert ports["first"].calls == [("inspect", target)]
    assert ports["second"].calls == [("inspect", target), ("wait", target)]
    assert target.model_dump() == {"address": "loopback", "port": 8080}
    with pytest.raises(ValidationError):
        PortTarget.model_validate({"port": 8080, "alias": "second"})
