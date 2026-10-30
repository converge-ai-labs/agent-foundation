"""Borrow Harness CDP execution; Service owns admission and exact durable tab identities."""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Literal

import anyio
from a13n_harness import RunError
from a13n_harness.capabilities.browser import (
    BrowserError,
    BrowserHumanControl,
    BrowserObservation,
    BrowserQuery,
    BrowserReadKind,
    BrowserScreenshot,
    BrowserTab,
    BrowserTabAction,
)
from a13n_harness.capabilities.browser.actions import Action, Clip, Condition
from a13n_harness.capabilities.browser.scope import CdpBrowserBackend
from a13n_harness.tools import current_invocation_scope
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from a13n_service.infra.db import transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.runs.attempts import Lease
from a13n_service.runs.browsers import admission, lifecycle
from a13n_service.runs.browsers.schemas import BrowserBinding
from a13n_service.runs.browsers.tables import BrowserSelectionRow, BrowserTabRow
from a13n_service.runs.calls import CallCheck, Refused
from a13n_service.runs.runtime import Runtime


class Backend:
    def __init__(
        self,
        runtime: Runtime,
        lease: Lease,
        binding: BrowserBinding,
        check: CallCheck,
        stack: AsyncExitStack,
        enabled: frozenset[str],
    ):
        self.runtime, self.lease, self.binding, self.check, self.stack = runtime, lease, binding, check, stack
        self.enabled = enabled
        self.native: CdpBrowserBackend | None = None
        self.lock = asyncio.Lock()
        self.epoch: int | None = None
        self.selected: str | None = None
        self.connection = AsyncExitStack()
        self.stack.push_async_callback(self._disconnect)

    async def _disconnect(self) -> None:
        if self.native is not None:
            self.native.retire()
        await self.connection.aclose()
        self.native = None
        self.epoch = None

    async def _connect(self, permit: admission.Permit) -> CdpBrowserBackend:
        selected = await lifecycle.ready_target(
            self.runtime, self.lease.workspace_id, self.binding.browser_id, configuration=permit.configuration
        )
        if self.native is not None and not self.native.browser.connected:
            await self._disconnect()
        if self.native is None:
            assert selected.state is not None
            async with AsyncExitStack() as connection:
                with anyio.fail_after(self.runtime.settings.providers.operation_seconds):
                    if selected.account is None:
                        browser = await lifecycle.connect_external(
                            self.runtime, selected.state, self.runtime.endpoint_policy.for_run(permit.configuration)
                        )
                    else:
                        provider = await connection.enter_async_context(
                            lifecycle.open_provider(self.runtime, selected.account)
                        )
                        browser = await provider.connect(selected.state)
                connection.push_async_callback(browser.close)
                self.native = CdpBrowserBackend(browser, fallback_selection=False)
                self.connection = connection.pop_all()
        return self.native

    async def _sync(self, native: CdpBrowserBackend, permit: admission.Permit) -> None:
        infos = (await native.browser.send("Target.getTargets"))["targetInfos"]
        pages = {info["targetId"]: info for info in infos if info["type"] == "page"}
        async with transaction(self.runtime.storage) as session:
            owners = {
                row.target_id: row.thread_id
                for row in await session.scalars(
                    select(BrowserTabRow).where(
                        BrowserTabRow.browser_id == self.binding.browser_id,
                        BrowserTabRow.generation == permit.generation,
                    )
                )
            }
            discovered: dict[str, str] = {}
            while True:
                added = {
                    target: owners[info["openerId"]]
                    for target, info in pages.items()
                    if target not in owners and info.get("openerId") in owners
                }
                if not added:
                    break
                discovered.update(added)
                owners.update(added)
            # Ownership is append-only within a generation. Concurrent discovery may insert the same
            # exact popup, but must never change its opener's owner or invert Thread -> Browser locks.
            await lifecycle.lock_owners(session, discovered.values())
            row = await lifecycle.find(session, self.lease.workspace_id, self.binding.browser_id, lock=True)
            if row.generation != permit.generation:
                raise BrowserError("Browser generation changed. Ask for a new binding.")
            for target, owner in discovered.items():
                await session.execute(
                    insert(BrowserTabRow)
                    .values(
                        browser_id=row.id,
                        workspace_id=row.workspace_id,
                        target_id=target,
                        thread_id=owner,
                        generation=permit.generation,
                    )
                    .on_conflict_do_nothing()
                )
            selected_id = permit.selected_id
            if self.binding.tab_access == "thread" and (
                selected_id is None or owners.get(selected_id) != self.lease.thread_id
            ):
                selected_id = None
        if self.epoch != permit.epoch - 1:
            native.invalidate()
        # Restore cleanup ownership before widening visibility; observing a global target never claims it.
        native.tab_access = "owned"
        for target, owner in owners.items():
            if owner == self.lease.thread_id and target in pages and target not in native.targets:
                await native.attach(target, select=False)
        native.tab_access = "global" if self.binding.tab_access == "global" else "owned"
        try:
            local_selected = native.page.target
        except BrowserError:
            local_selected = None
        if selected_id is not None and selected_id in pages:
            if selected_id != local_selected:
                await native.attach(selected_id)
            self.selected = selected_id
        else:
            native.clear_selection()
            self.selected = None
        # No identity inference or implicit replacement after a selected page disappears.

    async def _save(self, native: CdpBrowserBackend, permit: admission.Permit) -> None:
        try:
            selected = native.page.target
        except BrowserError:
            selected = None
        async with transaction(self.runtime.storage) as session:
            await lifecycle.lock_owners(session, (self.lease.thread_id,))
            row = await lifecycle.find(session, self.lease.workspace_id, self.binding.browser_id, lock=True)
            if row.generation != permit.generation:
                return
            for target in native.targets:
                await session.execute(
                    insert(BrowserTabRow)
                    .values(
                        browser_id=row.id,
                        workspace_id=row.workspace_id,
                        target_id=target,
                        thread_id=self.lease.thread_id,
                        generation=row.generation,
                    )
                    .on_conflict_do_nothing()
                )
            await session.execute(
                insert(BrowserSelectionRow)
                .values(
                    browser_id=row.id,
                    workspace_id=row.workspace_id,
                    thread_id=self.lease.thread_id,
                    generation=row.generation,
                    target_id=selected,
                )
                .on_conflict_do_update(
                    index_elements=["browser_id", "thread_id"],
                    set_={
                        "generation": row.generation,
                        "target_id": selected,
                    },
                )
            )
        self.selected, self.epoch = selected, permit.epoch

    async def _call[T](
        self, tool: str, call: Callable[[CdpBrowserBackend], Awaitable[T]], *, inventory: bool = False
    ) -> T:
        if tool not in self.enabled:
            raise BrowserError("This browser tool is disabled for the Agent.")
        async with self.lock:
            try:
                permit = await admission.admit(self.runtime, self.lease, self.binding, inventory=inventory)
            except ServiceError as error:
                raise BrowserError(f"Browser access refused ({error.code}).") from None
            known = True
            native = None
            synced = False
            try:
                try:
                    invocation = current_invocation_scope().invocation
                except RuntimeError:
                    raise RunError("A browser call has no tool call identity", code="call_unidentified") from None
                try:
                    await self.check.tool_call(
                        call_id=invocation.tool_call_id,
                        source="browser",
                        tool_name=invocation.tool_name,
                        provider_id=permit.provider_id,
                    )
                except Refused as error:
                    raise RunError(str(error), code="call_refused") from error
                native = await self._connect(permit)
                await self._sync(native, permit)
                synced = True
                if not inventory:

                    def exact_selection() -> None:
                        if native.page.target != permit.selected_id:
                            raise BrowserError("The selected page disappeared. List tabs and select explicitly.")

                    native.check_access = exact_selection
                    exact_selection()
                known = False
                result = await call(native)
                known = not (
                    tool in {"act", "navigate", "wait"}
                    and isinstance(result, str)
                    and result.startswith("{")
                    and json.loads(result).get("dispatch") == "unknown"
                )
                return result
            except BrowserError as error:
                known = known or error.dispatch != "unknown"
                raise
            except ServiceError as error:
                raise BrowserError(f"Browser unavailable ({error.code}).") from None
            except Exception as error:
                if isinstance(error, RunError):
                    raise
                raise BrowserError(
                    "Browser operation failed; observe before deciding what to do next.",
                    dispatch="not_dispatched" if known else "unknown",
                ) from None
            finally:
                # Persist newly created tabs even when navigation failed. A failed save keeps the permit
                # unsettled: neither cancellation nor worker loss is proof of completed bookkeeping.
                with anyio.CancelScope(shield=True):
                    if native is not None:
                        native.check_access = None
                        if synced:
                            await self._save(native, permit)
                    if known:
                        await admission.settle(self.runtime, permit)

    async def observe(self, query: BrowserQuery | None = None) -> BrowserObservation:
        return await self._call("observe", lambda native: native.observe(query))

    async def interact(self, action: Action, *, revision: str = "") -> str:
        return await self._call("act", lambda native: native.interact(action, revision=revision))

    async def wait(self, condition: Condition, *, revision: str = "", timeout: float = 10) -> str:
        return await self._call("wait", lambda native: native.wait(condition, revision=revision, timeout=timeout))

    async def navigate(
        self, url: str = "", *, action: Literal["goto", "back", "forward", "reload"] = "goto", timeout: float = 25
    ) -> str:
        return await self._call("navigate", lambda native: native.navigate(url, action=action, timeout=timeout))

    async def read(
        self,
        kind: BrowserReadKind,
        *,
        target: str = "",
        revision: str = "",
        frame_index: int = 0,
        offset: int = 0,
        limit: int = 100,
    ) -> str:
        return await self._call(
            "read",
            lambda native: native.read(
                kind, target=target, revision=revision, frame_index=frame_index, offset=offset, limit=limit
            ),
        )

    async def screenshot(self, *, full_page: bool = False, clip: Clip | None = None) -> BrowserScreenshot:
        return await self._call("screenshot", lambda native: native.screenshot(full_page=full_page, clip=clip))

    async def tabs(self, action: BrowserTabAction, *, target: str = "", url: str = "") -> tuple[BrowserTab, ...]:
        return await self._call("tabs", lambda native: native.tabs(action, target=target, url=url), inventory=True)

    async def dialog(self, action: Literal["inspect", "accept", "dismiss"], *, text: str = "") -> str:
        return await self._call("dialog", lambda native: native.dialog(action, text=text))

    async def upload(self, target: str, paths: tuple[str, ...], *, revision: str) -> str:
        async def unsupported(native: CdpBrowserBackend) -> str:
            raise BrowserError("This Browser provider does not expose authorized file transfer.")

        return await self._call("files", unsupported)

    async def downloads(self) -> str:
        return await self._call("files", lambda native: native.downloads())

    async def wait_for_handback(self) -> None:
        raise BrowserError(admission.GUIDANCE)


@asynccontextmanager
async def open_backend(
    runtime: Runtime, lease: Lease, binding: BrowserBinding, check: CallCheck, enabled: frozenset[str]
) -> AsyncIterator[Backend]:
    async with AsyncExitStack() as stack:
        backend = Backend(runtime, lease, binding, check, stack, enabled)
        if binding.preparation == "run_start":
            permit = None
            try:
                permit = await admission.admit(runtime, lease, binding, register_blocked=False)
                await check.tool_call(
                    call_id=new_object_id("bprep"),
                    source="browser.prepare",
                    tool_name="browser.prepare",
                    provider_id=permit.provider_id,
                )
                await lifecycle.ready_target(
                    runtime, lease.workspace_id, binding.browser_id, configuration=permit.configuration
                )
            except (BrowserHumanControl, BrowserError, ServiceError):
                # Readiness is independent of the model loop. The next actual browser call reports it.
                pass
            finally:
                if permit is not None:
                    with anyio.CancelScope(shield=True):
                        await admission.settle(runtime, permit)
        try:
            yield backend
        finally:
            # Disconnect only. Neither scope.close() nor supplier.delete() belongs to Run exit.
            if backend.native is not None:
                backend.native.retire()
