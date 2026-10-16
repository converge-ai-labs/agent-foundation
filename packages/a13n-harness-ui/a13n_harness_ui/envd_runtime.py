"""App-owned local envd installation, subprocesses and readiness observations."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import AsyncExitStack
from functools import partial
from pathlib import Path
from typing import Literal

from a13n_envd_client import EIPDeviceConnection
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.providers.environment.local_envd.runtime import LocalEnvdProviderRuntime, resolve_a13n_envd_executable
from anyio import CancelScope, Lock, fail_after, to_thread
from pydantic import BaseModel, ConfigDict

from a13n_harness_ui.composition import ResolvedEnvironmentProfile
from a13n_harness_ui.configuration.models import EnvdConfiguration, canonical_digest
from a13n_harness_ui.environment_monitoring import MonitoringSource
from a13n_harness_ui.environment_profiles import LocalProfileId, built_in_environment_profile
from a13n_harness_ui.errors import EnvironmentLifecycleError, HarnessUiError
from a13n_harness_ui.extensions.environment_adapters import LocalEnvdProfileConfiguration, LocalEnvdProjectConfiguration
from a13n_harness_ui.managed_runtime import EnvdRuntimeStatus, ManagedEnvdRuntime
from a13n_harness_ui.sandbox import create_sandbox_runtime, probe_local_envd_runtime
from a13n_harness_ui.settings import EnvdRuntimeSettings
from a13n_harness_ui.setup import EnvironmentReadiness


class EnvdInstanceStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    device_id: str
    sandbox: bool
    computer_use: bool
    running: bool
    roots: tuple[str, ...]


class EnvdStatus(EnvdRuntimeStatus):
    computer_use: bool = False
    configured_executable: str | None = None
    restart_required: bool = False
    preparing: bool = False
    instances: tuple[EnvdInstanceStatus, ...] = ()
    readiness: tuple[EnvironmentReadiness, ...] = ()


class EnvdRuntimeManager:
    """One Host owner; incompatible immutable boundaries use separate daemons."""

    def __init__(
        self,
        *,
        settings: EnvdRuntimeSettings,
        runtime_parent: Path | None,
        protected_roots: tuple[Path, ...] = (),
        configuration: EnvdConfiguration | None = None,
    ) -> None:
        self.configuration = configuration
        self.settings = (
            settings
            if configuration is None
            else settings.model_copy(
                update={
                    "executable": None
                    if configuration.executable is None
                    else Path(configuration.executable).expanduser()
                }
            )
        )
        self.computer_use = configuration is not None and configuration.computer_use
        self._parent = runtime_parent
        self._protected_roots = protected_roots
        self._runtimes: dict[tuple[tuple[Path, ...], str], LocalEnvdProviderRuntime] = {}
        self._lock = Lock()
        self._actions = Lock()
        self._preparing = False
        self._readiness: dict[LocalProfileId, EnvironmentReadiness] = {}
        self._managed = (
            None
            if runtime_parent is None
            else ManagedEnvdRuntime(
                cache_root=runtime_parent / "managed", staging_root=runtime_parent / "staging", settings=self.settings
            )
        )

    def require_computer_use(self) -> None:
        if not self.computer_use:
            raise EnvironmentLifecycleError(
                "Enable Computer Use in Runtime settings and restart Harness UI before using the Host desktop.",
                code="envd_computer_disabled",
            )

    async def resolve_executable(self) -> Path:
        if self.settings.executable is not None:
            return await to_thread.run_sync(partial(resolve_a13n_envd_executable, self.settings.executable))
        if self._managed is None:
            raise EnvironmentLifecycleError(
                "Local envd runtime cache is unavailable.", code="local_envd_runtime_unavailable"
            )
        return await self._managed.resolve()

    async def runtime(
        self,
        roots: tuple[Path, ...],
        *,
        thread_files_root: Path | None = None,
        profile: ResolvedEnvironmentProfile | None = None,
    ) -> LocalEnvdProviderRuntime:
        selected = LocalEnvdProfileConfiguration.model_validate(
            {} if profile is None else profile.provider_configuration
        )
        adapter = LocalEnvdProjectConfiguration.model_validate({} if profile is None else profile.adapter_configuration)
        if selected.launch.computer_use:
            self.require_computer_use()
        roots = tuple(root.resolve(strict=True) for root in roots)
        # Unrestricted native execution shares a stable Host-owned daemon. Session cwd
        # still comes from each connector; restricted grants never merge across roots.
        if selected.launch.full_control and selected.launch.sandbox.mode == "disabled" and self._parent is not None:
            shared = self._parent / "host"
            await to_thread.run_sync(partial(shared.mkdir, parents=True, exist_ok=True))
            roots = (shared.resolve(),)
        key = (
            roots,
            canonical_digest(
                {"launch": selected.launch.model_dump(mode="json"), "adapter": adapter.model_dump(mode="json")}
            ),
        )
        async with self._lock:
            if key not in self._runtimes:
                executable = await self.resolve_executable()
                self._runtimes[key] = await to_thread.run_sync(
                    partial(
                        create_sandbox_runtime,
                        executable,
                        roots=roots,
                        runtime_parent=self._parent,
                        protected_roots=self._protected_roots,
                        thread_files_root=thread_files_root,
                        launch=selected.launch,
                        project_access=adapter.project_access,
                    )
                )
            return self._runtimes[key]

    def record_readiness(self, result: EnvironmentReadiness) -> None:
        self._readiness[result.profile_id] = result

    async def start(
        self,
        profile_id: Literal["environment-envd", "environment-desktop"],
        probe_root: Path,
        *,
        restart: bool = False,
        reinstall: bool = False,
    ) -> EnvironmentReadiness:
        async with self._actions:
            self._preparing = True
            self._readiness.pop(profile_id, None)
            try:
                with fail_after(300):
                    if reinstall and (self.settings.executable is not None or self._managed is None):
                        raise EnvironmentLifecycleError(
                            "Custom executables must be updated on the Host, then restarted here.",
                            code="envd_custom_executable",
                        )
                    if restart or reinstall:
                        async with self._lock:
                            await self._stop_owned()
                            if reinstall:
                                assert self._managed is not None
                                await self._managed.resolve(reinstall=True)
                    builtin = built_in_environment_profile(profile_id)
                    assert builtin is not None
                    profile = ResolvedEnvironmentProfile(
                        profile_id=profile_id,
                        behavior_digest=canonical_digest(builtin.provider_configuration),
                        provider_key=builtin.provider_key,
                        adapter_key=builtin.adapter_key,
                        provider_configuration=builtin.provider_configuration,
                    )
                    runtime = await self.runtime((probe_root,), profile=profile)
                    # Explicit start can replace a disconnected owner. Acquiring first
                    # distinguishes a never-launched lazy runtime from a dead carrier.
                    if (await runtime.acquire_device()).is_closed:
                        async with self._lock:
                            with CancelScope(shield=True):
                                await runtime.close()
                                for key, owner in tuple(self._runtimes.items()):
                                    if owner is runtime:
                                        del self._runtimes[key]
                        runtime = await self.runtime((probe_root,), profile=profile)
                    desktop = await probe_local_envd_runtime(
                        runtime, probe_root, computer_use=profile_id == "environment-desktop"
                    )
                ready = desktop is None or (desktop.observe_ready and desktop.input_ready)
                result = EnvironmentReadiness(
                    profile_id=profile_id,
                    ready=ready,
                    code="envd_ready" if ready else "computer_not_ready",
                    message=(
                        "Compatible release installed and runtime started."
                        if reinstall
                        else "Runtime restarted. Previous Sessions are closed."
                        if restart
                        else "Runtime is ready."
                    )
                    if ready
                    else "Runtime started, but Host screen observation or input is unavailable.",
                    observe_ready=None if desktop is None else desktop.observe_ready,
                    input_ready=None if desktop is None else desktop.input_ready,
                )
            except (HarnessUiError, EnvironmentError, EnvironmentProviderError, OSError, TimeoutError) as exc:
                result = EnvironmentReadiness(
                    profile_id=profile_id,
                    ready=False,
                    code="envd_not_ready",
                    message=str(exc) or "Runtime did not become ready.",
                )
            finally:
                self._preparing = False
            self.record_readiness(result)
            return result

    async def status(self) -> EnvdStatus:
        if self._managed is None:
            raise EnvironmentLifecycleError(
                "Local envd runtime cache is unavailable.", code="local_envd_runtime_unavailable"
            )
        installation = await self._managed.status()
        instances = tuple(
            EnvdInstanceStatus(
                device_id=runtime.device_id,
                sandbox=runtime.configuration.sandbox.mode == "restricted",
                computer_use=runtime.configuration.computer_use,
                running=runtime.running_device() is not None,
                roots=tuple(str(root) for root in roots) if runtime.configuration.sandbox.mode == "restricted" else (),
            )
            for (roots, _), runtime in self._runtimes.items()
        )
        return EnvdStatus(
            **{**installation.model_dump(), "running_devices": sum(item.running for item in instances)},
            computer_use=self.computer_use,
            configured_executable=None if self.settings.executable is None else str(self.settings.executable),
            preparing=self._preparing,
            instances=instances,
            readiness=tuple(self._readiness.values()),
        )

    def monitoring_sources(self) -> Iterable[MonitoringSource]:
        for (roots, revision), runtime in self._runtimes.items():
            yield MonitoringSource(
                id="local-" + runtime.device_id,
                name="Local envd",
                transport="stdio",
                revision=revision,
                roots=tuple(str(root) for root in roots[:16]),
                acquire=partial(_running_device, runtime),
            )

    async def stop(self) -> None:
        """Explicitly stop all owned daemons, including their active Sessions."""
        async with self._actions, self._lock:
            await self._stop_owned()

    async def _stop_owned(self) -> None:
        with CancelScope(shield=True):
            try:
                async with AsyncExitStack() as stack:
                    for runtime in self._runtimes.values():
                        stack.push_async_callback(runtime.close)
            finally:
                self._runtimes.clear()
                self._readiness.clear()

    async def close(self) -> None:
        await self.stop()


async def _running_device(runtime: LocalEnvdProviderRuntime) -> EIPDeviceConnection | None:
    return runtime.running_device()
