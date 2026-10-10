"""Built-in implementations keep lifecycle authority out of execution objects."""

import inspect

import pytest
from a13n_environment._backend import ExecutionBackend, ManagementBackend
from a13n_environment.daytona.execution import DaytonaExecution
from a13n_environment.daytona.management import DaytonaManagement
from a13n_environment.direct_local.execution import DirectLocalExecution
from a13n_environment.direct_local.management import DirectLocalManagement
from a13n_environment.docker.execution import DockerExecution
from a13n_environment.docker.management import DockerManagement
from a13n_environment.e2b.execution import E2BExecution
from a13n_environment.e2b.management import E2BManagement
from a13n_environment.local_envd.provider import LocalEnvdExecution
from a13n_environment.modal.execution import ModalExecution
from a13n_environment.modal.management import ModalManagement
from a13n_environment.remote_envd.environment import RemoteEnvdExecution
from a13n_environment.runloop.execution import RunloopExecution
from a13n_environment.runloop.management import RunloopManagement
from a13n_environment.sprites.execution import SpritesExecution
from a13n_environment.sprites.management import SpritesManagement
from a13n_environment.vercel.execution import VercelExecution
from a13n_environment.vercel.management import VercelManagement


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
    for name in ("create", "start", "inspect", "stop", "destroy", "keepalive", "_cache_state"):
        assert not hasattr(implementation, name), (implementation, name)
    assert "operation_id" not in inspect.signature(implementation).parameters


@pytest.mark.parametrize(
    "implementation",
    [
        DaytonaManagement,
        DirectLocalManagement,
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
