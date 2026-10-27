"""ScreenCaptureKit worker. Only a Host-selected window from its Chromium PID.

Run in a subprocess so native framework failures cannot stop CDP automation.
Stdout carries length-prefixed JPEG frames; pixels are never written to disk.
"""

from __future__ import annotations

# PyObjC resolves framework symbols dynamically; it ships no framework stubs.
# pyright: reportAttributeAccessIssue=false, reportMissingImports=false
import asyncio
import struct
import sys
from typing import Any

import CoreMedia
import dispatch
import Foundation
import objc
import Quartz
import ScreenCaptureKit

from a13n_harness_ui.browser_macos import session_available


def windows(pid: int) -> dict[int, dict]:
    return {
        int(window["kCGWindowNumber"]): dict(window["kCGWindowBounds"])
        for window in Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll, 0)
        if window.get("kCGWindowOwnerPID") == pid
        and window.get("kCGWindowLayer") == 0
        and window.get("kCGWindowIsOnscreen")
        and window["kCGWindowBounds"]["Width"] > 1
        and window["kCGWindowBounds"]["Height"] > 1
    }


async def completion(start) -> tuple[Any, ...]:
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def resolve(values) -> None:
        if not future.done():
            future.set_result(values)

    def finished(*values) -> None:
        loop.call_soon_threadsafe(resolve, values)

    start(finished)
    async with asyncio.timeout(10):
        return await future


class CaptureOutput(  # pyright: ignore[reportGeneralTypeIssues]
    Foundation.NSObject,
    protocols=[objc.protocolNamed("SCStreamOutput"), objc.protocolNamed("SCStreamDelegate")],  # pyright: ignore[reportCallIssue]
):
    def stream_didOutputSampleBuffer_ofType_(self, stream, sample, kind):
        try:
            attachments = CoreMedia.CMSampleBufferGetSampleAttachmentsArray(sample, False)
            if not attachments or attachments[0].get(ScreenCaptureKit.SCStreamFrameInfoStatus) != 0:
                return
            buffer = CoreMedia.CMSampleBufferGetImageBuffer(sample)
            if buffer is None:
                return
            with objc.autorelease_pool():
                image = Quartz.CIImage.imageWithCVPixelBuffer_(buffer)
                data = self.context.JPEGRepresentationOfImage_colorSpace_options_(
                    image, self.space, {Quartz.kCGImageDestinationLossyCompressionQuality: 0.8}
                )
                if data is None:
                    raise ValueError("JPEG encoding failed")
                self.loop.call_soon_threadsafe(self.deliver, bytes(data))
        except Exception:
            self.loop.call_soon_threadsafe(self.deliver, "Window preview encoding failed.")

    def stream_didStopWithError_(self, stream, error):
        self.loop.call_soon_threadsafe(
            self.deliver, "Window preview stopped. Check screen recording permission and retry."
        )

    @objc.python_method
    def deliver(self, value: bytes | str) -> None:
        if self.finished:
            return
        if isinstance(value, str):
            self.finished = True
        if self.frames.full():
            self.frames.get_nowait()
        self.frames.put_nowait(value)


def configuration(bounds: dict):
    configuration = ScreenCaptureKit.SCStreamConfiguration.alloc().init()
    width, height = bounds["Width"], bounds["Height"]
    scale = min(1, 1920 / width, 1200 / height)
    configuration.setWidth_(max(1, round(width * scale)))
    configuration.setHeight_(max(1, round(height * scale)))
    configuration.setMinimumFrameInterval_(CoreMedia.CMTimeMake(1, 15))
    configuration.setQueueDepth_(3)
    configuration.setPixelFormat_(Quartz.kCVPixelFormatType_32BGRA)
    configuration.setShowsCursor_(False)
    configuration.setCapturesAudio_(False)
    return configuration


def write_frame(data: bytes) -> None:
    sys.stdout.buffer.write(struct.pack("!I", len(data)))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


async def capture(pid: int, identity: int) -> None:
    if not session_available():
        raise RuntimeError("Unlock the Mac desktop and reconnect to preview the browser window.")
    if not Quartz.CGPreflightScreenCaptureAccess():
        raise RuntimeError("Allow Screen Recording for the app running Harness UI in System Settings, then retry.")
    available = windows(pid)
    if identity not in available:
        raise RuntimeError("The browser window closed. Reset the browser to open a new preview window.")
    bounds = available[identity]
    content, error = await completion(
        lambda done: (
            ScreenCaptureKit.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
                True, False, done
            )
        )
    )
    if error:
        raise RuntimeError("Screen recording permission is unavailable. Check System Settings and retry.")
    window = next(
        (
            candidate
            for candidate in content.windows()
            if candidate.windowID() == identity
            and candidate.owningApplication()
            and candidate.owningApplication().processID() == pid
        ),
        None,
    )
    if window is None:
        raise RuntimeError("The browser window is no longer available for preview.")
    output = CaptureOutput.alloc().init()
    output.loop = asyncio.get_running_loop()
    output.frames = asyncio.Queue(maxsize=1)
    output.finished = False
    output.context = Quartz.CIContext.contextWithOptions_(None)
    output.space = Quartz.CGColorSpaceCreateDeviceRGB()
    # Never use display- or application-wide filters, even on capture failure.
    content_filter = ScreenCaptureKit.SCContentFilter.alloc().initWithDesktopIndependentWindow_(window)
    stream = ScreenCaptureKit.SCStream.alloc().initWithFilter_configuration_delegate_(
        content_filter, configuration(bounds), output
    )
    sample_queue = dispatch.dispatch_queue_create(b"a13n.browser-preview", None)
    added, error = stream.addStreamOutput_type_sampleHandlerQueue_error_(
        output, ScreenCaptureKit.SCStreamOutputTypeScreen, sample_queue, None
    )
    if not added or error:
        raise RuntimeError("Could not initialize the browser window preview.")
    (error,) = await completion(stream.startCaptureWithCompletionHandler_)
    if error:
        raise RuntimeError("Could not start window capture. Check Screen Recording permission and retry.")

    async def monitor() -> None:
        nonlocal bounds
        while True:
            await asyncio.sleep(0.5)
            current = windows(pid).get(identity)
            if current is None or not session_available() or not Quartz.CGPreflightScreenCaptureAccess():
                output.deliver("Window preview stopped: the window closed or screen recording permission was revoked.")
                return
            if (current["Width"], current["Height"]) != (bounds["Width"], bounds["Height"]):
                (error,) = await completion(
                    lambda done, current=current: stream.updateConfiguration_completionHandler_(
                        configuration(current), done
                    )
                )
                if error:
                    output.deliver("Window preview stopped while resizing. Reconnect to retry.")
                    return
                bounds = current

    async def watch() -> None:
        try:
            await monitor()
        except Exception:
            output.deliver("Window preview monitoring failed. Reconnect to retry.")

    monitor_task = asyncio.create_task(watch())
    try:
        # Static windows may only emit idle stream frames. Seed the viewer with
        # a native snapshot using the same exact-window filter (macOS 14+).
        image, error = await completion(
            lambda done: ScreenCaptureKit.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(
                content_filter, configuration(bounds), done
            )
        )
        if error or image is None:
            raise RuntimeError("Could not capture the browser window.")
        initial = output.context.JPEGRepresentationOfImage_colorSpace_options_(
            Quartz.CIImage.imageWithCGImage_(image),
            output.space,
            {Quartz.kCGImageDestinationLossyCompressionQuality: 0.8},
        )
        if initial is None:
            raise RuntimeError("Could not encode the browser window.")
        await asyncio.to_thread(write_frame, bytes(initial))
        while True:
            frame = await output.frames.get()
            if isinstance(frame, str):
                raise RuntimeError(frame)
            await asyncio.to_thread(write_frame, frame)
    finally:
        output.finished = True
        monitor_task.cancel()
        await asyncio.gather(monitor_task, return_exceptions=True)
        await completion(stream.stopCaptureWithCompletionHandler_)


if __name__ == "__main__":
    try:
        asyncio.run(capture(int(sys.argv[1]), int(sys.argv[2])))
    except (RuntimeError, TimeoutError) as exc:
        print(str(exc) or "Window preview timed out. Reconnect to retry.", file=sys.stderr)
        sys.exit(1)
