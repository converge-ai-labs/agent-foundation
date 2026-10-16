"""Built-in implementations keep lifecycle authority out of execution objects."""

import inspect

import pytest
from a13n_harness.providers.environment._backend import ExecutionBackend
from a13n_harness.providers.environment.daytona.execution import DaytonaExecution
from a13n_harness.providers.environment.direct_local.execution import DirectLocalExecution
from a13n_harness.providers.environment.docker.execution import DockerExecution
from a13n_harness.providers.environment.e2b.execution import E2BExecution
from a13n_harness.providers.environment.local_envd.provider import LocalEnvdExecution
from a13n_harness.providers.environment.modal.execution import ModalExecution
from a13n_harness.providers.environment.remote_envd.environment import RemoteEnvdExecution
from a13n_harness.providers.environment.runloop.execution import RunloopExecution
from a13n_harness.providers.environment.sprites.execution import SpritesExecution
from a13n_harness.providers.environment.vercel.execution import VercelExecution
from a13n_service.providers.environment._backend import ManagementBackend
from a13n_service.providers.environments.daytona.management import DaytonaManagement
from a13n_service.providers.environments.docker.management import DockerManagement
from a13n_service.providers.environments.e2b.management import E2BManagement
from a13n_service.providers.environments.modal.management import ModalManagement
from a13n_service.providers.environments.runloop.management import RunloopManagement
from a13n_service.providers.environments.sprites.management import SpritesManagement
from a13n_service.providers.environments.vercel.management import VercelManagement


@pytest.mark.parametrize(
    "implementation",
    [
        DaytonaExecution,
        DirectLocalExecution,
        DockerExecution,
        E2BExecution,
        LocalEnvdExecution,
        ModalExecution,
        RemoteEnvdExecution,
        RunloopExecution,
        SpritesExecution,
        VercelExecution,
    ],
)
def test_execution_implementation_has_no_management_authority(implementation):
    assert issubclass(implementation, ExecutionBackend)
    assert not issubclass(implementation, ManagementBackend)
    assert not inspect.isabstract(implementation)
    for name in ("create", "start", "inspect", "stop", "destroy", "keepalive", "desktop_access", "_cache_state"):
        assert not hasattr(implementation, name), (implementation, name)
    assert "operation_id" not in inspect.signature(implementation).parameters


@pytest.mark.parametrize(
    "implementation",
    [
        DaytonaManagement,
        DockerManagement,
        E2BManagement,
        ModalManagement,
        RunloopManagement,
        SpritesManagement,
        VercelManagement,
    ],
)
def test_management_implementation_has_no_execution_scope(implementation):
    assert issubclass(implementation, ManagementBackend)
    assert not issubclass(implementation, ExecutionBackend)
    assert not inspect.isabstract(implementation)
    for name in ("open", "check_ready", "operations", "availability", "execute"):
        assert not hasattr(implementation, name), (implementation, name)
