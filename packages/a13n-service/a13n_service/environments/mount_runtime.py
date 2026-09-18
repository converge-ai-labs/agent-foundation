"""Worker-local installation evidence for accepted additional Run mounts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Literal

from a13n_environment import Environment, EnvironmentError
from a13n_harness import EnvironmentMount, SafeFailure
from a13n_harness.environment.providers import EnvironmentRuntime
from a13n_logging import get_logger
from anyio import fail_after

from a13n_service.interactions.attempts import AttemptAuthorityError, AttemptContext

from .mount_domain import AcceptedRunMount
from .mount_observations import RunMountObservations

logger = get_logger(__name__)


@dataclass(slots=True)
class _Application:
    mount: AcceptedRunMount
    status: Literal["ready", "failed"]
    error: SafeFailure | None = None
    retry_at: float = float("inf")
    retry_delay: float = 5
    unpublished: bool = True


class RunMountRuntime:
    """Read facts at reconciliation points; install only at the root model boundary.

    The caller serializes boundary methods. Watchers may read PG independently,
    but never call ``apply`` or infer installation from a ready observation.
    """

    def __init__(
        self,
        *,
        runtime: EnvironmentRuntime,
        has_primary: bool,
        observations: RunMountObservations,
        current_attempt: Callable[[], AttemptContext],
        prepare: Callable[[AcceptedRunMount], Awaitable[Environment]],
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._runtime = runtime
        self._has_primary = has_primary
        self._observations = observations
        self._current_attempt = current_attempt
        self._prepare = prepare
        self._clock = clock
        self._applications: dict[str, _Application] = {}

    async def reconcile(self) -> tuple[AcceptedRunMount, ...]:
        """Capture PG facts and retry observation writes without touching Harness."""
        snapshot = await self._observations.snapshot(self._current_attempt())
        accepted = {mount.name: mount for mount in snapshot}
        for name, application in self._applications.items():
            if accepted.get(name) != application.mount:
                raise ValueError("The accepted Run mount disappeared or changed")
            await self._publish(application)
        return snapshot

    async def apply(self) -> None:
        """Finish one captured acceptance batch before tools and context are assembled."""
        snapshot = await self.reconcile()
        default_name = snapshot[0].name if snapshot and not self._has_primary else None
        for mount in snapshot:
            previous = self._applications.get(mount.name)
            if previous is not None and (previous.status == "ready" or self._clock() < previous.retry_at):
                continue
            try:
                await self._observations.publish(self._current_attempt(), mount, "preparing")
            except AttemptAuthorityError:
                raise
            except Exception:
                logger.exception("Run mount preparing observation failed", extra={"mount_name": mount.name})
                continue
            try:
                await self._install(mount, make_default=mount.name == default_name)
            except AttemptAuthorityError:
                raise
            except Exception as error:
                code = error.code if isinstance(error, EnvironmentError) else "environment_preparation_failed"
                delay = min(previous.retry_delay * 2, 60) if previous is not None else 5
                application = _Application(
                    mount,
                    "failed",
                    error=SafeFailure(code=code, message="The Environment mount could not be prepared."),
                    retry_at=self._clock() + delay if code == "environment_unavailable" else float("inf"),
                    retry_delay=delay,
                )
                logger.exception("Run mount preparation failed", extra={"mount_name": mount.name, "code": code})
            else:
                application = _Application(mount, "ready")
                logger.info("Run mount installed", extra={"mount_name": mount.name, "run_id": mount.run_id})
            self._applications[mount.name] = application
            await self._publish(application)

    async def _install(self, mount: AcceptedRunMount, *, make_default: bool) -> None:
        environment = await self._prepare(mount)
        try:
            # Preparation owns all target I/O. Standard adapter entry only binds
            # the Harness mount ID, so no provider call separates this fence
            # from local publication at the serialized root boundary.
            await self._observations.validate(self._current_attempt(), mount)
            await self._runtime.mount(
                mount.name,
                EnvironmentMount(environment, mount_path=f"/environment/{mount.name}"),
                make_default=make_default,
            )
        except BaseException as error:
            try:
                with fail_after(self._current_attempt().cleanup_timeout.total_seconds(), shield=True):
                    await environment.close()
            except BaseException as cleanup_error:
                error.add_note(f"Environment candidate cleanup also failed: {cleanup_error!r}")
            raise

    async def _publish(self, application: _Application) -> None:
        if not application.unpublished:
            return
        try:
            await self._observations.publish(
                self._current_attempt(), application.mount, application.status, error=application.error
            )
        except AttemptAuthorityError:
            raise
        except Exception:
            logger.exception("Run mount application observation failed", extra={"mount_name": application.mount.name})
        else:
            application.unpublished = False
