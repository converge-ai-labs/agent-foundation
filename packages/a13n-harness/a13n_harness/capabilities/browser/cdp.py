"""Chromium page operations over cdp-use; the embedding Host owns the process."""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

import httpx2
from a13n_logging import get_logger
from websockets.exceptions import ConnectionClosed

from a13n_harness.capabilities.browser import (
    BrowserElement,
    BrowserError,
    BrowserObservation,
    BrowserQuery,
)

if TYPE_CHECKING:
    from .scope import CdpBrowserBackend
    from .visual import View

_LOGGER = get_logger(__name__)


async def websocket_url(cdp_url: str) -> str:
    """Resolve a trusted Host endpoint; never expose it as an Agent tool input."""
    parsed = urlsplit(cdp_url)
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.hostname or parsed.fragment:
        raise BrowserError("Use an HTTP(S) CDP base URL or a WS(S) debugger URL.")
    if parsed.scheme in {"ws", "wss"}:
        return cdp_url
    try:
        async with httpx2.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.get(parsed._replace(path=parsed.path.rstrip("/") + "/json/version").geturl())
            response.raise_for_status()
            endpoint = response.json()["webSocketDebuggerUrl"]
        result = urlsplit(endpoint)
        if result.scheme not in {"ws", "wss"} or not result.hostname or result.fragment:
            raise ValueError("Invalid debugger URL")
        return endpoint
    except (httpx2.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise BrowserError("Cannot discover the browser CDP endpoint.") from exc


@dataclass(frozen=True)
class Frame:
    identity: str
    session: str
    parent: str = ""
    url: str = ""


@dataclass(frozen=True)
class PointerPoint:
    frame: Frame
    x: float
    y: float
    root_x: float
    root_y: float


@dataclass(frozen=True)
class Node:
    backend: int
    frame: str
    session: str


@dataclass
class Page:
    target: str
    session: str
    url: str = "about:blank"
    revision: str = ""
    elements: dict[str, Node] = field(default_factory=dict)
    frames: dict[str, Frame] = field(default_factory=dict)
    frame_sessions: dict[str, str] = field(default_factory=dict)
    frame_parents: dict[str, str] = field(default_factory=dict)
    dialog: dict | None = None
    dialog_opened: asyncio.Event = field(default_factory=asyncio.Event)
    epoch: int = 0
    visual_epoch: int = 0
    navigation: int = 0
    deferred_objects: list[tuple[Frame, str]] = field(default_factory=list)
    requires_observation: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    loaded: asyncio.Event = field(default_factory=asyncio.Event)
    visual: View | None = None

    def invalidate(self, frames: set[str] | None = None) -> None:
        self.epoch += 1
        if frames is None:
            self.visual_epoch += 1
            self.visual = None
            self.revision = ""
            self.elements.clear()
        else:
            self.elements = {key: node for key, node in self.elements.items() if node.frame not in frames}

    def descendants(self, frame: str) -> set[str]:
        removed = {frame}
        while children := {key for key, parent in self.frame_parents.items() if parent in removed} - removed:
            removed.update(children)
        return removed


class Chromium:
    """One browser connection, explicit page sessions and bounded protocol calls."""

    @classmethod
    async def connect(cls, endpoint: str) -> Chromium:
        from cdp_use import CDPClient

        client = CDPClient(await websocket_url(endpoint))
        try:
            await client.start()
            browser = cls(client)
            await browser.send("Target.setDiscoverTargets", {"discover": True})
            return browser
        except BaseException:
            await client.stop()
            raise

    def __init__(self, client: Any) -> None:
        self.client = client
        self.disconnected = False
        self.owners: dict[str, CdpBrowserBackend] = {}
        self.scopes: set[CdpBrowserBackend] = set()
        self.pages: dict[str, Page] = {}
        self.attach_locks: dict[str, asyncio.Lock] = {}
        self.download_directory: str | None = None
        self.download_records: dict[str, dict] = {}
        self.download_omitted: dict[str, int] = {}
        self.download_started: Callable[[dict], None] = lambda item: None
        self.tasks: set[asyncio.Task] = set()
        self.opened: Callable[[dict], None] = lambda target: None
        self.closed: Callable[[str], None] = lambda target: None
        client.register.Page.frameNavigated(self._navigated)
        client.register.Page.domContentEventFired(self._loaded)
        client.register.Page.navigatedWithinDocument(self._within_document)
        client.register.Target.targetCreated(self._created)
        client.register.Target.targetDestroyed(self._destroyed)
        client.register.Page.frameAttached(self._frame_attached)
        client.register.Page.frameDetached(self._frame_detached)
        client.register.Page.javascriptDialogOpening(self._dialog_opened)
        client.register.Page.javascriptDialogClosed(self._dialog_closed)
        client.register.Browser.downloadWillBegin(self._download_started)
        client.register.Browser.downloadProgress(self._download_progress)

    def background(self, operation) -> None:
        task = operation if isinstance(operation, asyncio.Task) else asyncio.create_task(operation)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

        def completed(done: asyncio.Task) -> None:
            if not done.cancelled() and (error := done.exception()) is not None:
                _LOGGER.warning("Background CDP operation failed: %s", error)

        task.add_done_callback(completed)

    async def send(self, method: str, params: dict | None = None, page: Page | Frame | None = None) -> dict:
        try:
            async with asyncio.timeout(15):
                return await self.client.send_raw(method, params or {}, page.session if page else None)
        except ConnectionClosed as exc:
            for future in self.client.pending_requests.values():
                if not future.done():
                    future.set_exception(ConnectionError("Browser connection closed"))
                    # send_raw may fail before it starts awaiting this future.
                    future.exception()
            self.client.pending_requests.clear()
            raise BrowserError(
                "Browser connection closed; observe before retrying an uncertain action.", dispatch="unknown"
            ) from exc
        except (TimeoutError, RuntimeError, ConnectionError) as exc:
            raise BrowserError(
                "Browser operation failed; its outcome may be uncertain. Observe before retrying.", dispatch="unknown"
            ) from exc
        finally:
            for identity, future in tuple(self.client.pending_requests.items()):
                if future.cancelled():
                    self.client.pending_requests.pop(identity, None)

    def _page(self, session: str | None) -> Page | None:
        return next(
            (
                page
                for page in self.pages.values()
                if page.session == session or session in page.frame_sessions.values()
            ),
            None,
        )

    def _created(self, event: dict, session: str | None) -> None:
        info = event["targetInfo"]
        if info["type"] == "page" and (owner := self.owners.get(info.get("openerId", ""))):
            owner._claim(info["targetId"])
        self.opened(info)

    def _destroyed(self, event: dict, session: str | None) -> None:
        target = event["targetId"]
        if owner := self.owners.get(target):
            owner._removed(target)
        self.pages.pop(target, None)
        self.attach_locks.pop(target, None)
        for page in self.pages.values():
            if target in page.frame_sessions:
                page.frame_sessions.pop(target, None)
                page.frames.pop(target, None)
                page.invalidate(page.descendants(target))
        self.closed(target)

    def _frame_attached(self, event: dict, session: str | None) -> None:
        if page := self._page(session):
            page.frame_parents[event["frameId"]] = event["parentFrameId"]
            page.invalidate({event["frameId"]})

    def _frame_detached(self, event: dict, session: str | None) -> None:
        if page := self._page(session):
            removed = page.descendants(event["frameId"])
            if event.get("reason") != "swap":
                for identity in removed:
                    page.frame_parents.pop(identity, None)
                    page.frames.pop(identity, None)
                    page.frame_sessions.pop(identity, None)
            page.invalidate(removed)

    def _dialog_opened(self, event: dict, session: str | None) -> None:
        if page := self._page(session):
            page.dialog = {**event, "session": session}
            page.dialog_opened.set()
            page.invalidate()

    def _dialog_closed(self, event: dict, session: str | None) -> None:
        if page := self._page(session):
            page.dialog = None
            page.dialog_opened.clear()
            pending, page.deferred_objects = page.deferred_objects, []
            for frame, identity in pending:
                self.background(self.send("Runtime.releaseObject", {"objectId": identity}, frame))
            page.invalidate()

    def _download_started(self, event: dict, session: str | None) -> None:
        frame = event["frameId"]
        page = next((page for page in self.pages.values() if frame in page.frames or frame in page.frame_parents), None)
        # A direct attachment popup can download and close before attachment.
        # Chromium uses its page target ID for the root frame; ownership is
        # already known from targetCreated's exact opener ancestry.
        target = page.target if page is not None else frame
        if page is None and target not in self.owners:
            return
        owner = self.owners.get(target)
        targets = owner._download_targets if owner is not None else {target}
        scoped = {key: item for key, item in self.download_records.items() if item["page"] in targets}
        if len(scoped) >= 100:
            completed = next((key for key, item in scoped.items() if item["state"] != "inProgress"), None)
            omitted_target = self.download_records.pop(completed)["page"] if completed else target
            self.download_omitted[omitted_target] = self.download_omitted.get(omitted_target, 0) + 1
            if completed is None:
                return
        self.download_records[event["guid"]] = {**event, "page": target, "state": "inProgress"}
        self.download_started(self.download_records[event["guid"]])

    def _download_progress(self, event: dict, session: str | None) -> None:
        if item := self.download_records.get(event["guid"]):
            item.update(state=event["state"], received=event.get("receivedBytes", 0))

    async def configure_downloads(self, directory: str) -> None:
        await self.send(
            "Browser.setDownloadBehavior",
            {"behavior": "allowAndName", "downloadPath": directory, "eventsEnabled": True},
        )
        self.download_directory = directory

    def downloads(self, targets: set[str]) -> str:
        if self.download_directory is None:
            return "Download tracking is not configured by this browser's Host."
        records = []
        for guid, item in self.download_records.items():
            if item["page"] in targets:
                records.append(
                    {
                        "name": item.get("suggestedFilename", ""),
                        "state": item["state"],
                        "path": self.download_directory.rstrip("/") + "/" + guid
                        if item["state"] == "completed"
                        else None,
                    }
                )
        return json.dumps(
            {"downloads": records, "omitted": sum(self.download_omitted.get(target, 0) for target in targets)},
            ensure_ascii=False,
        )

    def _loaded(self, event: dict, session: str | None) -> None:
        if (page := self._page(session)) and session == page.session:
            page.loaded.set()

    async def navigate(
        self,
        page: Page,
        url: str = "",
        *,
        wait: bool = True,
        action: Literal["goto", "back", "forward", "reload"] = "goto",
        timeout: float = 25,
    ) -> str:
        self.require_document(page)
        dispatch = "not_dispatched"
        outcome = {"verification": "unverified"}
        try:
            async with asyncio.timeout(timeout):
                method, params = "Page.navigate", {"url": url}
                navigation = page.navigation
                if action in {"back", "forward"}:
                    history = await self.send("Page.getNavigationHistory", page=page)
                    index = history["currentIndex"] + (-1 if action == "back" else 1)
                    if not 0 <= index < len(history["entries"]):
                        raise BrowserError("No history entry in that direction.")
                    entry = history["entries"][index]
                    method, params = "Page.navigateToHistoryEntry", {"entryId": entry["id"]}
                elif action == "reload":
                    method, params = "Page.reload", {}
                page.loaded.clear()
                page.invalidate()
                dispatch = "unknown"
                result = await self.send(method, params, page)
                dispatch = "dispatched"
                if result.get("errorText"):
                    outcome["error"] = "Navigation failed: " + result["errorText"]
                    outcome["verification"] = "failed"
                elif wait:
                    if action in {"back", "forward"}:
                        # History may restore from BFCache (no DOMContentLoaded event)
                        # or redirect. Require a main-frame commit, then DOM readiness.
                        while page.navigation == navigation:
                            await asyncio.sleep(0.025)
                        while await self.evaluate(page, "document.readyState") == "loading":
                            await asyncio.sleep(0.05)
                    elif action == "reload" or "loaderId" in result:
                        await page.loaded.wait()
                    page.url = await self.evaluate(page, "location.href")
                    outcome["title"] = await self.evaluate(page, "document.title")
                    outcome["verification"] = "passed"
                    outcome["load_state"] = "domcontentloaded"
        except TimeoutError:
            outcome.update(verification="timed_out", error="Navigation deadline reached. Observe before retrying.")
        except BrowserError as exc:
            outcome["error"] = str(exc)
        return json.dumps({"dispatch": dispatch, **outcome, "url": page.url}, ensure_ascii=False)

    def _navigated(self, event: dict, session: str | None) -> None:
        page = self._page(session)
        frame = event["frame"]
        if page is not None:
            page.frames[frame["id"]] = Frame(
                frame["id"],
                session or page.session,
                frame.get("parentId", ""),
                frame["url"] + frame.get("urlFragment", ""),
            )
            if session == page.session and not frame.get("parentId"):
                page.url = frame["url"] + frame.get("urlFragment", "")
                page.navigation += 1
                page.invalidate()
            else:
                page.invalidate(page.descendants(frame["id"]))

    def _within_document(self, event: dict, session: str | None) -> None:
        if page := self._page(session):
            if session == page.session and (
                not page.frames
                or event.get("frameId") in {key for key, frame in page.frames.items() if not frame.parent}
            ):
                page.url = event["url"]
                page.navigation += 1
                page.invalidate()
            else:
                page.invalidate(page.descendants(event["frameId"]))

    async def attach(self, target: str, url: str = "about:blank") -> Page:
        async with self.attach_locks.setdefault(target, asyncio.Lock()):
            if target in self.pages:
                return self.pages[target]
            attached = await self.send("Target.attachToTarget", {"targetId": target, "flatten": True})
            page = Page(target, attached["sessionId"], url)
            self.pages[target] = page
            try:
                await self.send("Page.enable", page=page)
                await self.send("Runtime.enable", page=page)
                return page
            except BaseException:
                self.pages.pop(target, None)
                try:
                    await self.send("Target.detachFromTarget", {"sessionId": page.session})
                except BrowserError:
                    pass
                raise

    async def create(self, *, new_window: bool = False) -> Page:
        target = await self.send("Target.createTarget", {"url": "about:blank", "newWindow": new_window})
        try:
            return await self.attach(target["targetId"])
        except BaseException:
            try:
                await self.send("Target.closeTarget", {"targetId": target["targetId"]})
            except BrowserError:
                pass
            raise

    async def window(self, page: Page) -> dict:
        return await self.send("Browser.getWindowForTarget", {"targetId": page.target})

    async def window_bounds(self, identity: int, bounds: dict) -> None:
        await self.send("Browser.setWindowBounds", {"windowId": identity, "bounds": bounds})

    async def activate(self, page: Page) -> None:
        await self.send("Page.bringToFront", page=page)

    async def resize_window(self, page: Page, width: int, height: int) -> None:
        identity = (await self.window(page))["windowId"]
        await self.window_bounds(identity, {"windowState": "normal"})
        # Native window chrome stays in the VNC desktop. Size the content area
        # without emulating an unrelated viewport or changing browser focus.
        content = await self.evaluate(page, "({width:innerWidth,height:innerHeight})")
        bounds = (await self.window(page))["bounds"]
        await self.window_bounds(
            identity,
            {
                "left": 0,
                "top": 0,
                "width": max(200, width + bounds["width"] - content["width"]),
                "height": max(200, height + bounds["height"] - content["height"]),
            },
        )

    async def evaluate(self, page: Page, expression: str) -> Any:
        result = await self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
            page,
        )
        if "exceptionDetails" in result:
            raise BrowserError("The page script failed. Observe before retrying.")
        return result.get("result", {}).get("value")

    async def documents(self, page: Page) -> tuple[Frame, ...]:
        self.require_document(page)
        tree = (await self.send("Page.getFrameTree", page=page))["frameTree"]
        infos = (await self.send("Target.getTargets"))["targetInfos"]
        iframe_targets = {info["targetId"] for info in infos if info["type"] == "iframe"}
        target_parents = {
            info["targetId"]: info["parentFrameId"]
            for info in infos
            if info["type"] == "iframe" and info.get("parentFrameId")
        }
        frames: dict[str, Frame] = {}

        async def visit(node: dict, inherited_session: str, parent: str = "") -> None:
            if len(frames) >= 32:
                return
            info = node["frame"]
            identity = info["id"]
            session = inherited_session
            if identity in iframe_targets:
                if identity not in page.frame_sessions:
                    attached = await self.send("Target.attachToTarget", {"targetId": identity, "flatten": True})
                    page.frame_sessions[identity] = attached["sessionId"]
                    child = Frame(
                        identity, attached["sessionId"], parent, info.get("url", "") + info.get("urlFragment", "")
                    )
                    await self.send("Page.enable", page=child)
                    await self.send("Runtime.enable", page=child)
                session = page.frame_sessions[identity]
            frame = Frame(identity, session, parent, info.get("url", "") + info.get("urlFragment", ""))
            if parent:
                page.frame_parents[identity] = parent
            frames[identity] = frame
            for child in node.get("childFrames", []):
                await visit(child, session, identity)

        await visit(tree, page.session)
        # Chromium omits out-of-process children from the parent's frame tree.
        # Target ancestry also covers frames loaded before their parent session attached.
        # Only follow exact parent IDs already reached from this page's frame tree.
        for _ in range(32):
            pending = [
                (key, parent)
                for key, parent in (page.frame_parents | target_parents).items()
                if key not in frames and parent in frames and key in iframe_targets
            ]
            if not pending or len(frames) >= 32:
                break
            for identity, parent in pending[: max(0, 32 - len(frames))]:
                if identity not in page.frame_sessions:
                    attached = await self.send("Target.attachToTarget", {"targetId": identity, "flatten": True})
                    page.frame_sessions[identity] = attached["sessionId"]
                    frame = Frame(identity, attached["sessionId"], parent)
                    await self.send("Page.enable", page=frame)
                    await self.send("Runtime.enable", page=frame)
                frame = Frame(identity, page.frame_sessions[identity], parent)
                child = (await self.send("Page.getFrameTree", page=frame))["frameTree"]
                await visit(child, frame.session, parent)
        page.frames = frames
        return tuple(frames.values())

    @staticmethod
    def require_document(page: Page) -> None:
        if page.dialog is not None:
            raise BrowserError(
                "A JavaScript dialog is pending. Use browser_dialog or human takeover before continuing."
            )

    async def semantic(self, page: Page, query: BrowserQuery):
        from .snapshot import Document, documents, rows

        root = self.reference(page, query.target, query.revision)[0] if query.target else None
        epoch = page.epoch
        frames = await self.documents(page)
        if root:
            frames = tuple(frame for frame in frames if frame.identity == root.frame)
        snapshots = {}
        entries = []
        for frame in frames:
            if frame.session not in snapshots:
                snapshots[frame.session] = documents(
                    await self.send(
                        "DOMSnapshot.captureSnapshot",
                        {"computedStyles": ["display", "visibility"]},
                        frame,
                    )
                )
            ax = await self.send("Accessibility.getFullAXTree", {"frameId": frame.identity}, frame)
            dom = snapshots[frame.session].get(frame.identity, Document())
            entries.extend(
                (frame, row) for row in rows(ax["nodes"], dom, root.backend if root else 0) if row.matches(query)
            )
        if page.epoch != epoch:
            raise BrowserError("Page or frame changed during observation. Observe again.")
        return frames, entries

    async def observe(self, page: Page, query: BrowserQuery | None = None) -> BrowserObservation:
        query = query or BrowserQuery()
        if not 1 <= query.limit <= 1000 or query.offset < 0:
            raise BrowserError("Observation requires offset >= 0 and limit between 1 and 1000.")
        if page.dialog:
            page.requires_observation = False
            page.revision = secrets.token_hex(12)
            return BrowserObservation(await self.dialog(page, "inspect"), page.revision, ())
        frames, entries = await self.semantic(page, query)
        if not page.revision:
            page.revision = secrets.token_hex(12)
        previous = {node: key for key, node in page.elements.items()}
        current = {query.target: page.elements[query.target]} if query.target else {}
        lines, elements = [f"URL: {page.url}"], []
        size, emitted, last_frame = len(lines[0]), 0, ""
        for frame, row in entries[query.offset : query.offset + query.limit]:
            line = row.text()
            if size + len(line) > 24000:
                break
            if frame.identity != last_frame:
                lines.append(f"Frame: {frame.url}")
                last_frame = frame.identity
            if row.backend:
                node = Node(row.backend, frame.identity, frame.session)
                key = previous.get(node) or secrets.token_hex(8)
                current[key] = node
                elements.append(BrowserElement(key, row.name[:240], len(lines)))
            lines.append(line)
            size += len(line) + 1
            emitted += 1
        next_offset = query.offset + emitted
        lines.append(
            f"Rows: {emitted} of {len(entries)}; offset={query.offset}."
            + (
                f" More: browser_observe with offset={next_offset}, or narrow role/name/text/ref."
                if next_offset < len(entries)
                else ""
            )
        )
        if len(frames) >= 32:
            lines.append("Frame coverage is limited to 32 documents.")
        page.elements = current
        page.requires_observation = False
        return BrowserObservation("\n".join(lines), page.revision, tuple(elements))

    async def read(
        self,
        page: Page,
        kind: Literal["text", "links", "tables", "forms"],
        *,
        target: str = "",
        revision: str = "",
        frame_index: int = 0,
        offset: int = 0,
        limit: int = 100,
    ) -> str:
        from .snapshot import READ_DOCUMENT

        self.require_document(page)
        if kind not in {"text", "links", "tables", "forms"} or offset < 0 or not 1 <= limit <= 500:
            raise BrowserError("Invalid read kind, offset or limit.")
        epoch = page.epoch
        frames = await self.documents(page)
        arguments = [{"value": value} for value in (kind, offset, limit)]
        if target:
            async with self.node(page, target, revision) as (_, frame, identity):
                response = await self.send(
                    "Runtime.callFunctionOn",
                    {
                        "objectId": identity,
                        "functionDeclaration": READ_DOCUMENT,
                        "arguments": arguments,
                        "returnByValue": True,
                    },
                    frame,
                )
            frame_index = next(index for index, item in enumerate(frames) if item.identity == frame.identity)
            next_frame = None
        else:
            if not 0 <= frame_index < len(frames):
                raise BrowserError("Frame index is unavailable. Observe again.")
            frame = frames[frame_index]
            world = await self.send(
                "Page.createIsolatedWorld",
                {
                    "frameId": frame.identity,
                    "worldName": "a13n-read",
                },
                frame,
            )
            response = await self.send(
                "Runtime.evaluate",
                {
                    "expression": f"({READ_DOCUMENT}).call(document,{json.dumps(kind)},{offset},{limit})",
                    "contextId": world["executionContextId"],
                    "returnByValue": True,
                },
                frame,
            )
            next_frame = frame_index + 1 if frame_index + 1 < len(frames) else None
        if page.epoch != epoch:
            raise BrowserError("Page changed during read. Read again.")
        result = response.get("result", {}).get("value")
        if not isinstance(result, dict):
            raise BrowserError("The rendered document could not be read. Observe again.")
        return json.dumps(
            {
                "url": frame.url,
                "frame_index": frame_index,
                "kind": kind,
                **result,
                "next_frame": next_frame,
                "frame_limit_reached": len(frames) >= 32,
            },
            ensure_ascii=False,
        )

    def reference(self, page: Page, target: str, revision: str) -> tuple[Node, Frame]:
        self.require_document(page)
        node = page.elements.get(target)
        if not revision or revision != page.revision or node is None:
            raise BrowserError("Element reference expired. Observe again.")
        frame = page.frames.get(node.frame)
        if frame is None or frame.session != node.session:
            raise BrowserError("Frame changed. Observe again.")
        return node, frame

    @asynccontextmanager
    async def node(self, page: Page, target: str, revision: str):
        node, frame = self.reference(page, target, revision)
        result = await self.send("DOM.resolveNode", {"backendNodeId": node.backend}, frame)
        identity = result["object"]["objectId"]
        try:
            self.reference(page, target, revision)
            yield node, frame, identity
        finally:
            if page.dialog:
                # Runtime commands can wait behind a modal JavaScript dialog.
                # Release this handle after the dialog closes, without delaying its tool.
                page.deferred_objects.append((frame, identity))
            else:
                await self.send("Runtime.releaseObject", {"objectId": identity}, frame)

    async def settle_rendering(self, frame: Frame) -> None:
        # Input hit testing uses compositor data, which can trail layout after a
        # scroll. Background tabs can suspend animation frames, so never wait
        # indefinitely for their renderer or turn this into a focus operation.
        await self.send(
            "Runtime.evaluate",
            {
                "expression": """document.hidden ? undefined : new Promise(resolve=>{
                    let frame; const finish=()=>{cancelAnimationFrame(frame);clearTimeout(timer);resolve();};
                    const timer=setTimeout(finish,100);
                    frame=requestAnimationFrame(()=>{frame=requestAnimationFrame(finish);});
                })""",
                "awaitPromise": True,
                "returnByValue": True,
            },
            frame,
        )

    async def point(self, page: Page, frame: Frame, x: float, y: float, *, scroll: bool = False) -> PointerPoint:
        seen = set()
        input_point: tuple[Frame, float, float] | None = None
        while frame.parent:
            if frame.identity in seen or frame.parent not in page.frames:
                raise BrowserError("Frame ancestry changed. Observe again.")
            seen.add(frame.identity)
            parent = page.frames[frame.parent]
            if input_point is None and parent.session != frame.session:
                # Input coordinates belong to this renderer's root viewport.
                # Continue through every ancestor to verify clipping and coverage.
                input_point = frame, x, y
            owner = await self.send("DOM.getFrameOwner", {"frameId": frame.identity}, parent)
            backend = owner["backendNodeId"]
            resolved = await self.send("DOM.resolveNode", {"backendNodeId": backend}, parent)
            identity = resolved["object"]["objectId"]
            try:
                if scroll:
                    await self.send(
                        "Runtime.callFunctionOn",
                        {
                            "objectId": identity,
                            "functionDeclaration": "function(){this.scrollIntoView({block:'nearest',inline:'nearest',behavior:'instant'});}",
                        },
                        parent,
                    )
                    # Poll from the Host: renderer timers are throttled in background tabs.
                    previous, stable = None, 0
                    deadline = asyncio.get_running_loop().time() + 0.5
                    while asyncio.get_running_loop().time() < deadline:
                        await asyncio.sleep(0.02)
                        measured = await self.send(
                            "Runtime.callFunctionOn",
                            {
                                "objectId": identity,
                                "returnByValue": True,
                                "functionDeclaration": "function(){const r=this.getBoundingClientRect();return [r.x,r.y,r.width,r.height];}",
                            },
                            parent,
                        )
                        current = measured.get("result", {}).get("value")
                        stable = stable + 1 if current is not None and current == previous else 0
                        previous = current
                        if stable >= 2:
                            break
                    else:
                        raise BrowserError("Iframe geometry did not settle. Observe again; no click sent.")
                result = await self.send(
                    "Runtime.callFunctionOn",
                    {
                        "objectId": identity,
                        "returnByValue": True,
                        "functionDeclaration": """function(x,y){const r=this.getBoundingClientRect();
                    for(let a=this;a;a=a.parentElement){const m=getComputedStyle(a).transform;
                    if(m!=='none'){const t=new DOMMatrix(m);if(t.b||t.c||!t.is2D)return null;}}
                    const px=r.left+(this.clientLeft+x)*r.width/this.offsetWidth;
                    const py=r.top+(this.clientTop+y)*r.height/this.offsetHeight;
                    let hit=this.ownerDocument.elementFromPoint(px,py);
                    while(hit?.shadowRoot){const inner=hit.shadowRoot.elementFromPoint(px,py);if(!inner||inner===hit)break;hit=inner;}
                    for(let a=hit;a;a=a.parentElement||a.getRootNode()?.host){if(a===this)return {x:px,y:py};}
                    return null;}""",
                        "arguments": [{"value": x}, {"value": y}],
                    },
                    parent,
                )
                bounds = result.get("result", {}).get("value")
                if not bounds:
                    raise BrowserError(
                        "Iframe is covered, clipped, rotated or unavailable. Observe again or take over."
                    )
                x, y = bounds["x"], bounds["y"]
            finally:
                await self.send("Runtime.releaseObject", {"objectId": identity}, parent)
            frame = parent
        input_frame, input_x, input_y = input_point or (frame, x, y)
        return PointerPoint(input_frame, input_x, input_y, x, y)

    async def dispatch_input(self, page: Page, method: str, params: dict, *, frame: Frame | None = None) -> bool:
        """A modal may pause the command reply after input has already been delivered."""
        operation = asyncio.create_task(self.send(method, params, frame or page))
        dialog = asyncio.create_task(page.dialog_opened.wait())
        retained = False
        try:
            done, _ = await asyncio.wait((operation, dialog), return_when=asyncio.FIRST_COMPLETED)
            if operation in done:
                await operation
            else:
                self.background(operation)
                retained = True
            return page.dialog is not None
        finally:
            dialog.cancel()
            if not retained and not operation.done():
                operation.cancel()
            await asyncio.gather(dialog, *(() if retained else (operation,)), return_exceptions=True)

    async def element(
        self,
        page: Page,
        target: str,
        revision: str,
        *,
        fill: str | None = None,
        hover: bool = False,
        button: str = "left",
        count: int = 1,
    ) -> None:
        async with self.node(page, target, revision) as (_, frame, identity):
            await self.send(
                "Runtime.callFunctionOn",
                {
                    "objectId": identity,
                    "functionDeclaration": "function(){this.scrollIntoView({block:'nearest',inline:'nearest',behavior:'instant'});}",
                },
                frame,
            )
            if fill is None:
                await self.settle_rendering(frame)
            checked = await self.send(
                "Runtime.callFunctionOn",
                {
                    "objectId": identity,
                    "returnByValue": True,
                    "functionDeclaration": """function(){if(!this.isConnected||this.matches(':disabled'))return null;
                for(let n=this;n;n=n.parentElement||n.getRootNode()?.host){if(n.inert)return null;}
                const doc=this.ownerDocument, win=doc.defaultView;
                if(getComputedStyle(this).visibility!=='visible')return null;
                for(const r of this.getClientRects()){
                    const left=Math.max(0,r.left),right=Math.min(win.innerWidth,r.right);
                    const top=Math.max(0,r.top),bottom=Math.min(win.innerHeight,r.bottom);
                    if(right<=left||bottom<=top)continue;
                    for(const [fx,fy] of [[.5,.5],[.2,.5],[.8,.5],[.5,.2],[.5,.8]]){
                        const x=left+(right-left)*fx,y=top+(bottom-top)*fy;
                        let hit=doc.elementFromPoint(x,y);
                        while(hit?.shadowRoot){const inner=hit.shadowRoot.elementFromPoint(x,y);if(!inner||inner===hit)break;hit=inner;}
                        for(let n=hit;n;n=n.parentElement||n.getRootNode()?.host){if(n===this)return {x,y,type:this.type};}
                    }
                }return null;}""",
                },
                frame,
            )
            point = checked.get("result", {}).get("value")
            if not point:
                raise BrowserError("Element is detached, hidden, disabled or covered. Observe again.")
            self.reference(page, target, revision)
            if fill is not None:
                if point.get("type") == "file":
                    raise BrowserError("Use browser_files(action='upload') to select files.")
                focused = await self.send(
                    "Runtime.callFunctionOn",
                    {
                        "objectId": identity,
                        "returnByValue": True,
                        "functionDeclaration": """function(){
                            if(!this.isConnected||this.matches(':disabled')||this.readOnly)return false;
                            for(let n=this;n;n=n.parentElement||n.getRootNode()?.host){if(n.inert)return false;}
                            if(!this.isContentEditable && this.tagName!=='TEXTAREA' &&
                                !(this.tagName==='INPUT'&&['text','search','tel','url','email','password','number'].includes(this.type)))return false;
                            this.focus({preventScroll:true});
                            let active=this.ownerDocument.activeElement;
                            while(active?.shadowRoot?.activeElement)active=active.shadowRoot.activeElement;
                            if(active!==this)return false;
                            if(this.isContentEditable){
                                const range=this.ownerDocument.createRange();
                                range.selectNodeContents(this);
                                const selection=this.ownerDocument.getSelection();
                                selection.removeAllRanges();
                                selection.addRange(range);
                            }else{this.select();}
                            return true;
                        }""",
                    },
                    frame,
                )
                self.reference(page, target, revision)
                if focused.get("result", {}).get("value") is not True:
                    raise BrowserError("Fill requires an enabled, writable text field with focus. No text inserted.")
                await self.send("Input.insertText", {"text": fill}, page)
            else:
                position = await self.point(page, frame, point["x"], point["y"], scroll=True)
                self.reference(page, target, revision)
                await self.dispatch_input(
                    page,
                    "Input.dispatchMouseEvent",
                    {"type": "mouseMoved", "x": position.x, "y": position.y, "button": "none"},
                    frame=position.frame,
                )
                if hover:
                    return
                self.reference(page, target, revision)
                # Hover handlers can replace or move a node without navigating.
                checked = await self.send(
                    "Runtime.callFunctionOn",
                    {
                        "objectId": identity,
                        "returnByValue": True,
                        "functionDeclaration": """function(x,y){
                        if(!this.isConnected||this.matches(':disabled'))return false;
                        for(let n=this;n;n=n.parentElement||n.getRootNode()?.host){if(n.inert)return false;}
                        let hit=this.ownerDocument.elementFromPoint(x,y);
                        while(hit?.shadowRoot){const inner=hit.shadowRoot.elementFromPoint(x,y);if(!inner||inner===hit)break;hit=inner;}
                        for(let n=hit;n;n=n.parentElement||n.getRootNode()?.host){if(n===this)return true;}
                        return false;
                     }""",
                        "arguments": [{"value": point["x"]}, {"value": point["y"]}],
                    },
                    frame,
                )
                self.reference(page, target, revision)
                if checked.get("result", {}).get("value") is not True:
                    raise BrowserError(
                        "Target moved, detached or became covered after hover. Observe again; no click sent."
                    )
                current = await self.point(page, frame, point["x"], point["y"])
                self.reference(page, target, revision)
                if (
                    current.frame.session != position.frame.session
                    or abs(current.root_x - position.root_x) > 1
                    or abs(current.root_y - position.root_y) > 1
                    or abs(current.x - position.x) > 1
                    or abs(current.y - position.y) > 1
                ):
                    raise BrowserError("Iframe moved after hover. Observe again; no click sent.")
                params = {"x": position.x, "y": position.y, "button": button, "clickCount": count}
                try:
                    await self.dispatch_input(
                        page, "Input.dispatchMouseEvent", {"type": "mousePressed", **params}, frame=position.frame
                    )
                finally:
                    # Finish even when mousedown navigates, opens a modal, fails or is cancelled.
                    await self.dispatch_input(
                        page, "Input.dispatchMouseEvent", {"type": "mouseReleased", **params}, frame=position.frame
                    )

    async def scroll(self, page: Page, *, x: int, y: int, target: str = "", revision: str = "") -> str:
        self.require_document(page)
        if page.requires_observation:
            raise BrowserError("Observe after handback before continuing.")
        if abs(x) > 10000 or abs(y) > 10000:
            raise BrowserError("Scroll distance exceeds 10000 CSS pixels.")
        if target:
            async with self.node(page, target, revision) as (_, frame, identity):
                result = await self.send(
                    "Runtime.callFunctionOn",
                    {
                        "objectId": identity,
                        "returnByValue": True,
                        "functionDeclaration": "function(x,y){if(!this.isConnected)return null;const before=[this.scrollLeft,this.scrollTop];this.scrollBy({left:x,top:y,behavior:'instant'});return {before,after:[this.scrollLeft,this.scrollTop]};}",
                        "arguments": [{"value": x}, {"value": y}],
                    },
                    frame,
                )
                outcome = result.get("result", {}).get("value")
                if outcome is None:
                    raise BrowserError("Scroll target is detached. Observe again.")
        else:
            outcome = await self.evaluate(
                page,
                "(()=>{const before=[scrollX,scrollY];scrollBy({left:"
                + str(x)
                + ",top:"
                + str(y)
                + ",behavior:'instant'});return {before,after:[scrollX,scrollY]}})()",
            )
        return "Scroll positions: " + json.dumps(outcome)

    async def dialog(self, page: Page, action: Literal["inspect", "accept", "dismiss"], *, text: str = "") -> str:
        if action != "inspect" and page.requires_observation:
            raise BrowserError("Observe after handback before answering a dialog.")
        current = page.dialog
        if current is None:
            return "No JavaScript dialog is pending."
        if action == "inspect":
            return "JavaScript dialog: " + json.dumps(
                {key: current.get(key, "") for key in ("type", "message", "defaultPrompt")}, ensure_ascii=False
            )
        session = Frame("", current["session"] or page.session)
        await self.send("Page.handleJavaScriptDialog", {"accept": action == "accept", "promptText": text}, session)
        page.dialog = None
        page.invalidate()
        return "Dialog answered. Observe before continuing."

    async def upload(self, page: Page, target: str, paths: tuple[str, ...], *, revision: str) -> str:
        if page.requires_observation:
            raise BrowserError("Observe after handback before continuing.")
        if not 1 <= len(paths) <= 16 or any(
            "\x00" in path
            or len(path) > 4096
            or not (PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute())
            for path in paths
        ):
            raise BrowserError("Supply 1-16 absolute file paths on the browser Host.")
        async with self.node(page, target, revision) as (node, frame, identity):
            checked = await self.send(
                "Runtime.callFunctionOn",
                {
                    "objectId": identity,
                    "returnByValue": True,
                    "functionDeclaration": "function(){return this.isConnected&&this.type==='file'&&!this.matches(':disabled')&&!this.closest('[inert]')&&!this.webkitdirectory?{multiple:this.multiple}:null;}",
                },
                frame,
            )
            field = checked.get("result", {}).get("value")
            if field is None or (len(paths) > 1 and not field["multiple"]):
                raise BrowserError(
                    "Select an enabled file input supporting this number of files. Directory uploads require takeover."
                )
            self.reference(page, target, revision)
            await self.send("DOM.setFileInputFiles", {"backendNodeId": node.backend, "files": list(paths)}, frame)
        return "Host files selected. Observe the form before submitting."

    async def new_tab(self, source: Page) -> Page:
        self.require_document(source)
        frames = await self.documents(source)
        root = next(frame for frame in frames if not frame.parent)
        world = await self.send("Page.createIsolatedWorld", {"frameId": root.identity, "worldName": "a13n-tab"}, root)
        name = "a13n-" + secrets.token_hex(12)
        before = {info["targetId"] for info in (await self.send("Target.getTargets"))["targetInfos"]}
        await self.send(
            "Runtime.evaluate",
            {
                "expression": f"!!window.open('about:blank',{json.dumps(name)})",
                "contextId": world["executionContextId"],
                "returnByValue": True,
                "userGesture": True,
            },
            root,
        )
        try:
            async with asyncio.timeout(5):
                while True:
                    infos = (await self.send("Target.getTargets"))["targetInfos"]
                    for info in infos:
                        if (
                            info["type"] == "page"
                            and info["targetId"] not in before
                            and info.get("openerId") == source.target
                        ):
                            page = await self.attach(info["targetId"])
                            if await self.evaluate(page, "window.name") == name:
                                if (await self.window(page))["windowId"] != (await self.window(source))["windowId"]:
                                    await self.close_page(page)
                                    raise BrowserError("The browser opened a different window instead of an owned tab.")
                                await self.evaluate(page, "window.name=''")
                                return page
                    await asyncio.sleep(0.025)
        except TimeoutError as exc:
            raise BrowserError(
                "Tab creation did not settle. List tabs before retrying; the tab may already be open.",
                dispatch="unknown",
            ) from exc

    async def close_target(self, target: str) -> None:
        await self.send("Target.closeTarget", {"targetId": target})
        try:
            async with asyncio.timeout(5):
                while any(item["targetId"] == target for item in (await self.send("Target.getTargets"))["targetInfos"]):
                    await asyncio.sleep(0.025)
        except TimeoutError as exc:
            raise BrowserError("Tab close has not settled. List tabs before retrying.", dispatch="unknown") from exc
        self._destroyed({"targetId": target}, None)

    async def close_page(self, page: Page) -> None:
        await self.close_target(page.target)

    async def close_browser(self) -> None:
        """Explicit process-owner operation; disconnecting never calls this."""
        await self.send("Browser.close")

    async def release_keys(self, page: Page) -> None:
        for key, code, number in (
            ("Shift", "ShiftLeft", 16),
            ("Control", "ControlLeft", 17),
            ("Alt", "AltLeft", 18),
            ("Meta", "MetaLeft", 91),
        ):
            await self.send(
                "Input.dispatchKeyEvent",
                {"type": "keyUp", "key": key, "code": code, "windowsVirtualKeyCode": number, "modifiers": 0},
                page,
            )

    async def close(self) -> None:
        """Disconnect this client; tabs and the browser process remain Host-owned."""
        self.disconnected = True
        for scope in self.scopes:
            scope.retire()
        self.scopes.clear()
        self.owners.clear()
        for page in self.pages.values():
            page.invalidate()
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        self.pages.clear()
        self.attach_locks.clear()
        await self.client.stop()
