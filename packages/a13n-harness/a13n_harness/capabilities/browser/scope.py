"""Host-owned tab scopes shared by standalone and embedded browser use."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Literal

from . import (
    BrowserError,
    BrowserHumanControl,
    BrowserObservation,
    BrowserQuery,
    BrowserScreenshot,
    BrowserTab,
    BrowserTabAction,
)
from .actions import Action, Clip, Condition
from .cdp import Chromium, Page


class CdpBrowserBackend:
    """Tab access and owned cleanup on a borrowed Chromium connection.

    Hosts authorize the binding and may supply check_access for live revocation.
    tab_access defaults to owned targets; global access includes all live pages
    without claiming them. Bindings share one Chromium runtime and profile.
    """

    def __init__(
        self,
        browser: Chromium,
        page: Page | None = None,
        *,
        check_access: Callable[[], None] | None = None,
        new_window: bool = False,
        tab_access: Literal["owned", "global"] = "owned",
        fallback_selection: bool = True,
    ) -> None:
        if tab_access not in {"owned", "global"}:
            raise ValueError("tab_access must be 'owned' or 'global'.")
        self.browser = browser
        self.tab_access = tab_access
        self.fallback_selection = fallback_selection
        self.new_window = new_window
        self.check_access = check_access
        self._targets: dict[str, None] = {}
        self._download_targets: set[str] = set()
        self._selected: str | None = None
        self._lock = asyncio.Lock()
        self._reference_owner = object()
        self._retired = False
        self._paused = False
        self._handback = asyncio.Event()
        self._handback.set()
        if page is not None:
            if browser.pages.get(page.target) is not page:
                raise BrowserError("Page does not belong to this browser connection.")
            if tab_access == "owned":
                self._claim(page.target)
            self._selected = page.target
        browser.scopes.add(self)

    @property
    def targets(self) -> frozenset[str]:
        """Owned cleanup targets, independent of global visibility; process-local only."""
        return frozenset(self._targets)

    @property
    def page(self) -> Page:
        """The selected attached page, for trusted Host presentation operations."""
        page = self.browser.pages.get(self._selected or "")
        if page is None:
            raise BrowserError("No selected page. List tabs, then select or open a tab.")
        return page

    def _claim(self, target: str) -> None:
        owner = self.browser.owners.get(target)
        if owner is not None and owner is not self:
            raise BrowserError("Tab belongs to another browser scope.")
        self.browser.owners[target] = self
        self._targets[target] = None
        self._download_targets.add(target)

    def _removed(self, target: str) -> None:
        self._targets.pop(target, None)
        if self.browser.owners.get(target) is self:
            self.browser.owners.pop(target)
        if self._selected == target:
            self._selected = next(reversed(self._targets), None) if self.fallback_selection else None
            self.invalidate()

    def clear_selection(self) -> None:
        """Let a Host clear a lost durable selection without choosing another owned tab."""
        self.invalidate()
        self._selected = None

    def invalidate(self) -> None:
        """Require fresh observation after human input or Host selection changes."""
        for target in self._targets.keys() | {self._selected}:
            if page := self.browser.pages.get(target or ""):
                page.invalidate()
                page.requires_observation = True

    def _check(self) -> None:
        if self._retired or self.browser.disconnected:
            raise BrowserError("Browser scope is closed or disconnected. Ask the Host for a new binding.")
        if self.check_access is not None:
            self.check_access()
        if self._paused:
            raise BrowserHumanControl("Browser scope is paused for human control.")

    @asynccontextmanager
    async def _operation(self) -> AsyncIterator[None]:
        self._check()
        async with self._lock:
            self._check()
            yield

    async def _selected_page(self) -> Page:
        if self._selected is None:
            raise BrowserError("No tabs remain. Open a tab with browser_tabs.")
        if self._selected not in self.browser.pages:
            page = await self.browser.attach(self._selected)
            page.requires_observation = True
        return self.page

    @asynccontextmanager
    async def _page_operation(self, page: Page) -> AsyncIterator[None]:
        async with page.lock:
            self._check()
            if self.browser.pages.get(page.target) is not page:
                raise BrowserError("The requested browser tab no longer exists. List tabs again.")
            if page.reference_owner is not self._reference_owner:
                if page.reference_owner is not None:
                    page.invalidate()
                    page.requires_observation = True
                page.reference_owner = self._reference_owner
            yield

    async def _close_target(self, target: str, *, cleanup: bool = False) -> None:
        # Cleanup also drains operations from other bindings using this page.
        if page := self.browser.pages.get(target):
            async with page.lock:
                if not cleanup:
                    self._check()
                await self.browser.close_target(target)
        else:
            if not cleanup:
                self._check()
            await self.browser.close_target(target)

    async def attach(self, target: str, *, select: bool = True) -> None:
        """Host-only admission of an exact live target, including best-effort reattachment."""
        async with self._operation():
            infos = (await self.browser.send("Target.getTargets"))["targetInfos"]
            if not any(info["targetId"] == target and info["type"] == "page" for info in infos):
                raise BrowserError("The requested browser tab no longer exists.")
            self._check()
            if self.tab_access == "owned":
                self._claim(target)
            page = await self.browser.attach(target)
            async with self._page_operation(page):
                page.invalidate()
                page.requires_observation = True
                if select:
                    self._selected = target

    async def _refresh(self) -> list[dict]:
        infos = (await self.browser.send("Target.getTargets"))["targetInfos"]
        # Events normally claim popups synchronously. Reconcile missed events by
        # exact opener ancestry only, never by title, URL or foreground window.
        pending = [info for info in infos if info["type"] == "page"]
        while True:
            added = False
            for info in pending:
                if info["targetId"] not in self.browser.owners and info.get("openerId") in self._targets:
                    self._claim(info["targetId"])
                    added = True
            if not added:
                break
        live = {info["targetId"] for info in pending}
        for target in tuple(self._targets):
            if target not in live:
                self._removed(target)
        if self._selected is not None and self._selected not in live:
            self._removed(self._selected)
        return [info for info in pending if self.tab_access == "global" or info["targetId"] in self._targets]

    async def pause(self) -> None:
        """Block admission immediately and drain this scope before human input."""
        if self._retired or self.browser.disconnected:
            raise BrowserError("Browser scope is closed or disconnected.")
        self._paused = True
        self._handback.clear()
        async with self._lock:
            self.invalidate()

    def resume(self) -> None:
        """Release a drained scope; the Host owns viewer authority and revocation."""
        if self._lock.locked():
            raise BrowserError("Wait for pause to drain before resuming browser tools.")
        self.invalidate()
        self._paused = False
        self._handback.set()

    async def wait_for_handback(self) -> None:
        await self._handback.wait()
        self._check()

    def retire(self) -> None:
        """Revoke this binding without closing targets or disconnecting the client."""
        self._retired = True
        self.invalidate()
        self._handback.set()

    async def close(self) -> None:
        """Retire, drain, then close only owned tabs. Failed cleanup can be retried."""
        self.retire()
        async with self._lock:
            await self._refresh()
            attempted: set[str] = set()
            errors = []
            while remaining := self.targets - attempted:
                for target in remaining:
                    attempted.add(target)
                    try:
                        await self._close_target(target, cleanup=True)
                    except BrowserError as exc:
                        errors.append(exc)
            if errors:
                raise BrowserError("Some scoped tabs could not be closed; the Host may retry cleanup.") from errors[0]
            for guid, item in tuple(self.browser.download_records.items()):
                if item["page"] in self._download_targets:
                    self.browser.download_records.pop(guid)
            for target in self._download_targets:
                self.browser.download_omitted.pop(target, None)
            self._download_targets.clear()
            self.browser.scopes.discard(self)

    @classmethod
    @asynccontextmanager
    async def connect(cls, cdp_url: str) -> AsyncIterator[CdpBrowserBackend]:
        """Standalone convenience: own a tab scope and client, never the browser process."""
        browser = await Chromium.connect(cdp_url)
        backend = None
        try:
            backend = cls(browser, await browser.create())
            yield backend
        finally:
            try:
                if backend is not None:
                    await backend.close()
            finally:
                await browser.close()

    async def observe(self, query: BrowserQuery | None = None) -> BrowserObservation:
        async with self._operation():
            page = await self._selected_page()
            async with self._page_operation(page):
                return await self.browser.observe(page, query)

    async def read(
        self,
        kind: Literal["text", "links", "tables", "forms"],
        *,
        target: str = "",
        revision: str = "",
        frame_index: int = 0,
        offset: int = 0,
        limit: int = 100,
    ) -> str:
        async with self._operation():
            page = await self._selected_page()
            async with self._page_operation(page):
                return await self.browser.read(
                    page, kind, target=target, revision=revision, frame_index=frame_index, offset=offset, limit=limit
                )

    async def screenshot(self, *, full_page: bool = False, clip: Clip | None = None) -> BrowserScreenshot:
        from .visual import capture

        async with self._operation():
            page = await self._selected_page()
            async with self._page_operation(page):
                return await capture(self.browser, page, full_page=full_page, clip=clip)

    async def navigate(
        self, url: str = "", *, action: Literal["goto", "back", "forward", "reload"] = "goto", timeout: float = 25
    ) -> str:
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        try:
            async with asyncio.timeout_at(end) as admission:
                async with self._operation():
                    page = await self._selected_page()
                    async with self._page_operation(page):
                        if page.requires_observation:
                            raise BrowserError("Observe after handback before continuing.")
                        # The page operation owns dispatch-aware timeout reporting.
                        admission.reschedule(None)
                        return await self.browser.navigate(page, url, action=action, timeout=end - loop.time())
        except TimeoutError:
            return '{"verification":"timed_out","dispatch":"not_dispatched"}'

    async def wait(self, condition: Condition, *, revision: str = "", timeout: float = 10) -> str:
        from .interaction import wait

        try:
            async with asyncio.timeout(timeout):
                async with self._operation():
                    page = await self._selected_page()
                    async with self._page_operation(page):
                        return await wait(self.browser, page, condition, revision=revision, check_access=self._check)
        except TimeoutError:
            return '{"verification":"timed_out","dispatch":"not_dispatched"}'

    async def interact(self, action: Action, *, revision: str = "") -> str:
        from .interaction import interact

        async with self._operation():
            page = await self._selected_page()
            async with self._page_operation(page):
                return await interact(self.browser, page, action, revision=revision)

    async def tabs(self, action: BrowserTabAction, *, target: str = "", url: str = "") -> tuple[BrowserTab, ...]:
        async with self._operation():
            infos = await self._refresh()
            if action in {"select", "close"} and target not in {info["targetId"] for info in infos}:
                raise BrowserError("Tab is not owned or accessible in this browser scope. List tabs again.")
            if action == "new":
                if self._selected is None or self.browser.owners.get(self._selected) is not self:
                    # Do not use a borrowed opener: its popups belong to its owner.
                    # Recreate a lost owned window when this scope had one.
                    page = await self.browser.create(new_window=self.new_window or bool(self._download_targets))
                else:
                    source = await self._selected_page()
                    async with self._page_operation(source):
                        page = await self.browser.new_tab(source)
                # Register even if close/pause began during creation: cleanup must
                # see the late target. Never navigate it after retirement.
                self._claim(page.target)
                self._selected = page.target
                if self._retired or self._paused:
                    raise BrowserError("Tab creation was interrupted. List tabs after handback; do not replay it.")
                async with self._page_operation(page):
                    page.invalidate()
                    page.requires_observation = True
                    if url:
                        result = json.loads(await self.browser.navigate(page, url))
                        if result["verification"] != "passed":
                            raise BrowserError(
                                "Tab opened, but navigation did not finish: "
                                + result.get("error", "unknown outcome")
                                + " List tabs before continuing; do not repeat tab creation.",
                                dispatch="unknown",
                            )
                infos = await self._refresh()
            elif action == "select":
                page = await self.browser.attach(target)
                async with self._page_operation(page):
                    page.invalidate()
                    page.requires_observation = True
                    self._selected = target
            elif action == "close":
                await self._close_target(target)
                infos = await self._refresh()
            return tuple(
                BrowserTab(
                    info["targetId"], info.get("title", ""), info.get("url", ""), info["targetId"] == self._selected
                )
                for info in infos
            )

    async def dialog(self, action: Literal["inspect", "accept", "dismiss"], *, text: str = "") -> str:
        async with self._operation():
            page = await self._selected_page()
            async with self._page_operation(page):
                return await self.browser.dialog(page, action, text=text)

    async def upload(self, target: str, paths: tuple[str, ...], *, revision: str) -> str:
        async with self._operation():
            page = await self._selected_page()
            async with self._page_operation(page):
                return await self.browser.upload(page, target, paths, revision=revision)

    async def downloads(self) -> str:
        async with self._operation():
            targets = self._download_targets
            if self.tab_access == "global":
                targets = targets | {item["page"] for item in self.browser.download_records.values()}
                targets |= self.browser.download_omitted.keys()
            return self.browser.downloads(targets)
