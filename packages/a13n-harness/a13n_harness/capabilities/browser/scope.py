"""Host-owned tab scopes shared by standalone and embedded browser use."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Literal

from . import BrowserAction, BrowserError, BrowserHumanControl, BrowserObservation, BrowserTab, BrowserTabAction
from .cdp import Chromium, Page


class CdpBrowserBackend:
    """One tab scope on a borrowed Chromium connection, independent of Run lifetime.

    Hosts authorize the binding before exposing it, and may supply check_access
    for live revocation. All scopes sharing a browser must use the same Chromium
    connection for exclusive membership. A scope is not profile isolation.
    """

    def __init__(
        self, browser: Chromium, page: Page | None = None, *, check_access: Callable[[], None] | None = None
    ) -> None:
        self.browser = browser
        self.check_access = check_access
        self._targets: dict[str, None] = {}
        self._download_targets: set[str] = set()
        self._selected: str | None = None
        self._lock = asyncio.Lock()
        self._retired = False
        self._paused = False
        self._handback = asyncio.Event()
        self._handback.set()
        if page is not None:
            if browser.pages.get(page.target) is not page:
                raise BrowserError("Page does not belong to this browser connection.")
            self._claim(page.target)
            self._selected = page.target
        browser.scopes.add(self)

    @property
    def targets(self) -> frozenset[str]:
        """Current process-local membership; never a durable recovery checkpoint."""
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
            self._selected = next(reversed(self._targets), None)
            self.invalidate()

    def invalidate(self) -> None:
        """Require fresh observation after human input or Host selection changes."""
        for target in self._targets:
            if page := self.browser.pages.get(target):
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

    async def attach(self, target: str, *, select: bool = True) -> None:
        """Host-only admission of an exact live target, including best-effort reattachment."""
        async with self._operation():
            infos = (await self.browser.send("Target.getTargets"))["targetInfos"]
            if not any(info["targetId"] == target and info["type"] == "page" for info in infos):
                raise BrowserError("The requested browser tab no longer exists.")
            self._check()
            self._claim(target)
            page = await self.browser.attach(target)
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
        return [info for info in pending if info["targetId"] in self._targets]

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
                        await self.browser.send("Target.closeTarget", {"targetId": target})
                    except BrowserError as exc:
                        errors.append(exc)
                    else:
                        self.browser._destroyed({"targetId": target}, None)
            if errors:
                raise BrowserError("Some scoped tabs could not be closed; the Host may retry cleanup.") from errors[0]
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

    async def observe(self) -> BrowserObservation:
        async with self._operation():
            page = await self._selected_page()
            async with page.lock:
                return await self.browser.observe(page)

    async def screenshot(self) -> bytes:
        async with self._operation():
            page = await self._selected_page()
            async with page.lock:
                return await self.browser.screenshot(page)

    async def act(self, action: BrowserAction, *, target: str = "", value: str = "", revision: str = "") -> str:
        async with self._operation():
            page = await self._selected_page()
            async with page.lock:
                return await self.browser.act(page, action, target=target, value=value, revision=revision)

    async def tabs(self, action: BrowserTabAction, *, target: str = "", url: str = "") -> tuple[BrowserTab, ...]:
        async with self._operation():
            infos = await self._refresh()
            if action in {"select", "close"} and target not in self._targets:
                raise BrowserError("Tab is not owned by this browser scope. List tabs again.")
            if action == "new":
                if self._selected is None:
                    page = await self.browser.create()
                else:
                    source = await self._selected_page()
                    async with source.lock:
                        page = await self.browser.new_tab(source)
                # Register even if close/pause began during creation: cleanup must
                # see the late target. Never navigate it after retirement.
                self._claim(page.target)
                self._selected = page.target
                self.invalidate()
                if self._retired or self._paused:
                    raise BrowserError("Tab creation was interrupted. List tabs after handback; do not replay it.")
                self._check()
                if url:
                    await self.browser.navigate(page, url)
                infos = await self._refresh()
            elif action == "select":
                self._selected = target
                await self._selected_page()
                self.invalidate()
            elif action == "close":
                await self.browser.send("Target.closeTarget", {"targetId": target})
                self.browser._destroyed({"targetId": target}, None)
                infos = await self._refresh()
            return tuple(
                BrowserTab(
                    info["targetId"], info.get("title", ""), info.get("url", ""), info["targetId"] == self._selected
                )
                for info in infos
            )

    async def scroll(self, *, x: int, y: int, target: str = "", revision: str = "") -> str:
        async with self._operation():
            page = await self._selected_page()
            async with page.lock:
                return await self.browser.scroll(page, x=x, y=y, target=target, revision=revision)

    async def dialog(self, action: Literal["inspect", "accept", "dismiss"], *, text: str = "") -> str:
        async with self._operation():
            page = await self._selected_page()
            async with page.lock:
                return await self.browser.dialog(page, action, text=text)

    async def upload(self, target: str, paths: tuple[str, ...], *, revision: str) -> str:
        async with self._operation():
            page = await self._selected_page()
            async with page.lock:
                return await self.browser.upload(page, target, paths, revision=revision)

    async def downloads(self) -> str:
        async with self._operation():
            return self.browser.downloads(self._download_targets)
