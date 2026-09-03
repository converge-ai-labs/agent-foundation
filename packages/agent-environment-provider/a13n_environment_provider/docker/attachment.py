"""Foundation attach-only adapter for one exact existing Docker container."""

from __future__ import annotations

from collections.abc import Mapping

from ..management import Environment
from ._errors import missing_failure, target_conflict_failure
from .configuration import DockerAttachmentConnection
from .provider import (
    _LABEL_BOOTSTRAP,
    _LABEL_ENVIRONMENT,
    _LABEL_PROVIDER,
    _PROVIDER_KEY,
    _DockerEIPSession,
    _engine_call,
    _store_recover,
)
from .runtime import DockerProviderRuntime


class DockerAttachmentEnvironment(_DockerEIPSession, Environment):
    """Fresh adapter that can only attach to one accepted existing container."""

    def __init__(self, connection: DockerAttachmentConnection, runtime: DockerProviderRuntime) -> None:
        Environment.__init__(self, None)
        _DockerEIPSession.__init__(self, environment_id=connection.environment_id, runtime=runtime)
        self._connection = connection.model_copy(deep=True)

    async def _enter(
        self,
        *,
        thread_id: str,
        run_id: str,
        agent_instance_id: str,
        mount_id: str,
        host_refs: Mapping[str, str],
    ) -> None:
        del thread_id, run_id, agent_instance_id, host_refs
        await _engine_call(self._runtime.engine.validate_local_topology())
        inspection = await _engine_call(self._runtime.engine.inspect_container(self._connection.container_id))
        if inspection is None:
            raise missing_failure("Docker attachment target does not exist.")
        if inspection.container_id != self._connection.container_id:
            raise target_conflict_failure("Docker attachment resolved a different target.")
        if inspection.status != "running":
            raise target_conflict_failure("Docker attachment target is not running.")
        if (
            inspection.labels.get(_LABEL_PROVIDER) != _PROVIDER_KEY
            or inspection.labels.get(_LABEL_ENVIRONMENT) != self.environment_id
        ):
            raise target_conflict_failure("Docker attachment target identity is incompatible.")
        correlation = inspection.labels.get(_LABEL_BOOTSTRAP)
        if not _is_bootstrap_correlation(correlation):
            raise target_conflict_failure("Docker attachment target has no valid bootstrap identity.")
        allocation = await _store_recover(self._runtime, correlation)
        if allocation is None or allocation.material.environment_id != self.environment_id:
            raise missing_failure("Docker attachment bootstrap material is unavailable.")
        await self._open_eip(inspection, allocation, mount_id=mount_id)

    async def _destroy(self) -> None:
        return None


def _is_bootstrap_correlation(value: str | None) -> bool:
    if value is None or not value.startswith("bootstrap-") or len(value) != len("bootstrap-") + 24:
        return False
    return all(character in "0123456789abcdef" for character in value.removeprefix("bootstrap-"))


__all__ = ["DockerAttachmentEnvironment"]
