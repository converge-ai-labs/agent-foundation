"""Read-only discovery of Host browser and managed VNC executable bindings."""

from __future__ import annotations

import asyncio
import ntpath
import os
import re
import shutil
import sys
from pathlib import Path
from platform import freedesktop_os_release
from typing import Literal

from a13n_harness_ui.configuration.models import BrowserConfiguration
from a13n_harness_ui.surfaces import SurfaceModel

type BrowserPlatform = Literal["linux", "macos", "windows", "other"]


class BrowserExecutable(SurfaceModel):
    name: str
    path: str
    source: Literal["system", "custom"]


type VncKind = Literal["tigervnc", "x11vnc", "wayvnc"]


class VncExecutable(SurfaceModel):
    kind: VncKind = "tigervnc"
    path: str
    source: Literal["system", "custom"]
    window_manager: str | None = None
    xrandr: str | None = None
    display_server: str | None = None
    disable_resizing: bool = False


class BrowserRequirement(SurfaceModel):
    name: str
    installed: bool
    detail: str
    required: bool = True
    purpose: str = ""
    managed: bool = False


class BrowserInstallStep(SurfaceModel):
    title: str
    command: str


class BrowserSetup(SurfaceModel):
    platform: BrowserPlatform
    distribution: str | None = None
    supported: bool
    ready: bool
    browser: BrowserExecutable | None = None
    vnc: VncExecutable | None = None
    requirements: tuple[BrowserRequirement, ...] = ()
    steps: tuple[BrowserInstallStep, ...] = ()
    message: str = ""


def host_platform() -> BrowserPlatform:
    platforms: dict[str, BrowserPlatform] = {"linux": "linux", "darwin": "macos", "win32": "windows"}
    return platforms.get(sys.platform, "other")


def linux_distribution() -> dict[str, str]:
    try:
        return freedesktop_os_release()
    except OSError:
        return {}


def executable_path(path: str) -> str | None:
    candidate = Path(path).expanduser().resolve(strict=False)
    return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None


def find_program(*names: str) -> str | None:
    for name in names:
        if found := shutil.which(name):
            if path := executable_path(found):
                return path
    return None


def system_browsers(platform: BrowserPlatform) -> list[BrowserExecutable]:
    candidates: list[tuple[str, str]] = []
    if platform == "linux":
        for name, command in (
            ("Google Chrome", "google-chrome"),
            ("Google Chrome", "google-chrome-stable"),
            ("Chromium", "chromium"),
            ("Chromium", "chromium-browser"),
            ("Microsoft Edge", "microsoft-edge"),
            ("Microsoft Edge", "microsoft-edge-stable"),
        ):
            if path := shutil.which(command):
                candidates.append((name, path))
    elif platform == "macos":
        for root in (Path("/Applications"), Path.home() / "Applications"):
            for name, path in (
                ("Google Chrome", "Google Chrome.app/Contents/MacOS/Google Chrome"),
                ("Chromium", "Chromium.app/Contents/MacOS/Chromium"),
                ("Microsoft Edge", "Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            ):
                candidates.append((name, str(root / path)))
    elif platform == "windows":
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            if root := os.environ.get(variable):
                for name, path in (
                    ("Google Chrome", r"Google\Chrome\Application\chrome.exe"),
                    ("Chromium", r"Chromium\Application\chrome.exe"),
                    ("Microsoft Edge", r"Microsoft\Edge\Application\msedge.exe"),
                ):
                    candidates.append((name, ntpath.join(root, path)))
    found: dict[str, BrowserExecutable] = {}
    for name, candidate in candidates:
        if path := executable_path(candidate):
            found.setdefault(path, BrowserExecutable(name=name, path=path, source="system"))
    return list(found.values())


def supported_browser(version: str) -> bool:
    return any(name in version.lower() for name in ("chromium", "chrome", "edge", "brave", "vivaldi"))


def supported_tigervnc(help_text: str) -> bool:
    required = ("rfbunixpath", "rfbport", "passwordfile", "securitytypes", "displayfd")
    return "tigervnc" in help_text.lower() and all(option in help_text.lower() for option in required)


async def inspect_vnc(path: str, source: Literal["system", "custom"]) -> VncExecutable | None:
    help_text = await probe(path, "-help")
    if supported_tigervnc(help_text):
        return VncExecutable(path=path, source=source)
    # x11vnc's full help exceeds the probe's output bound. Its option inventory
    # is small and includes the private transport and native auth capabilities.
    options = await probe(path, "-opts")
    if all(option in options for option in ("-unixsock", "-passwdfile", "-display", "-shared")):
        return VncExecutable(kind="x11vnc", path=path, source=source)
    help_text = await probe(path, "--help")
    if all(option in help_text for option in ("--unix-socket", "--disable-input", "--config", "--socket")):
        disable_resizing = "--disable-resizing" in help_text
        # Older WayVNC does not implement desktop resizing at all. Reject
        # unknown versions unless resizing can explicitly be disabled.
        version = await probe(path, "--version")
        legacy = re.search(r"wayvnc[^\n]*?\b0\.([0-7])\.", version, re.IGNORECASE)
        if disable_resizing or legacy:
            return VncExecutable(kind="wayvnc", path=path, source=source, disable_resizing=disable_resizing)
    return None


class BrowserScan(SurfaceModel):
    browsers: tuple[BrowserExecutable, ...] = ()
    vncs: tuple[VncExecutable, ...] = ()


async def scan_browsers() -> BrowserScan:
    """Enumerate supported installed executables only when explicitly requested."""
    platform = host_platform()
    candidates = await asyncio.to_thread(system_browsers, platform)
    browsers = []
    for candidate in candidates:
        if supported_browser(await probe(candidate.path, "--version")):
            browsers.append(candidate)
    vncs: dict[str, VncExecutable] = {}
    if platform == "linux":
        for name in ("Xtigervnc", "Xvnc", "x11vnc", "wayvnc"):
            path = await asyncio.to_thread(find_program, name)
            if path and path not in vncs:
                if candidate := await inspect_vnc(path, "system"):
                    vncs[path] = candidate
    return BrowserScan(browsers=tuple(browsers), vncs=tuple(vncs.values()))


async def probe(path: str, argument: str) -> str:
    """Bound help/version probes without launching a desktop or executing a shell."""
    process = None
    try:
        async with asyncio.timeout(5):
            process = await asyncio.create_subprocess_exec(
                path, argument, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
            )
            assert process.stdout is not None
            output = bytearray()
            while chunk := await process.stdout.read(min(8192, 65537 - len(output))):
                output.extend(chunk)
                if len(output) > 65536:
                    return ""
            await process.wait()
            return output.decode(errors="replace")
    except (OSError, TimeoutError):
        return ""
    finally:
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


async def inspect_browser(
    configuration: BrowserConfiguration | None = None, *, no_sandbox: bool = False
) -> BrowserSetup:
    configuration = configuration or BrowserConfiguration()
    platform = host_platform()
    supported = platform == "linux"
    distribution = await asyncio.to_thread(linux_distribution) if supported else {}
    browser = None
    browser_detail = "Select a browser executable in General settings, save and restart Harness UI"
    if configuration.executable:
        path = await asyncio.to_thread(executable_path, configuration.executable)
        if path:
            browser = BrowserExecutable(name="Configured browser", path=path, source="custom")
        browser_detail = "Configured browser executable is missing or not executable"
    if browser:
        version = await probe(browser.path, "--version")
        if not supported_browser(version):
            browser_detail = "Browser version probe failed or the executable is not a supported Chromium browser"
            browser = None
        else:
            browser_detail = f"{browser.path} ({browser.source})"

    resizing = await asyncio.to_thread(find_program, "xrandr") if supported else None
    manager = await asyncio.to_thread(find_program, "openbox", "fluxbox", "icewm", "twm") if supported else None
    vnc = None
    vnc_detail = "Select a VNC executable in General settings, save and restart Harness UI"
    if supported and configuration.vnc_executable:
        path = await asyncio.to_thread(executable_path, configuration.vnc_executable)
        if not path:
            vnc_detail = "Configured VNC executable is missing or not executable"
        elif candidate := await inspect_vnc(path, "custom"):
            display_server = None
            if candidate.kind == "x11vnc":
                display_server = await asyncio.to_thread(find_program, "Xvfb")
                resizing = None
            elif candidate.kind == "wayvnc":
                display_server = await asyncio.to_thread(find_program, "sway")
                manager = resizing = None
            vnc = candidate.model_copy(
                update={
                    "window_manager": manager,
                    "xrandr": resizing,
                    "display_server": display_server,
                }
            )
            vnc_detail = f"{path} ({vnc.kind})"
        else:
            vnc_detail = "VNC executable is not a compatible TigerVNC Xvnc, x11vnc or WayVNC server"
    requirements = [
        BrowserRequirement(
            name="Browser",
            installed=browser is not None,
            detail=browser_detail,
            purpose="Chrome, Chromium or Edge renders websites and stores the shared profile.",
        ),
        BrowserRequirement(
            name="VNC server",
            installed=vnc is not None,
            detail=vnc_detail,
            purpose="Streams the private browser desktop and accepts human keyboard and pointer input.",
        ),
    ]
    sandbox_blocked = supported and os.geteuid() == 0 and not no_sandbox
    sandbox_guidance = (
        "Chromium cannot run as root with its sandbox enabled. Restart Harness UI as a non-root user, "
        "or explicitly opt out with webui --browser-no-sandbox "
        "(A13N_HARNESS_UI_BROWSER_NO_SANDBOX=1)."
    )
    if sandbox_blocked:
        requirements.append(
            BrowserRequirement(
                name="Chromium sandbox",
                installed=False,
                purpose="Chromium's sandbox requires a non-root Host account.",
                detail=sandbox_guidance,
            )
        )
    if vnc:
        requirements.append(
            BrowserRequirement(
                name="Display server",
                managed=vnc.kind == "tigervnc",
                installed=vnc.kind == "tigervnc" or vnc.display_server is not None,
                purpose={
                    "tigervnc": "TigerVNC includes the virtual X display; no separate Xvfb is needed.",
                    "x11vnc": "Xvfb provides the private virtual X display captured by x11vnc.",
                    "wayvnc": "Sway provides the private headless Wayland desktop captured by WayVNC and manages its windows.",
                }[vnc.kind],
                detail="Built into the selected TigerVNC executable."
                if vnc.kind == "tigervnc"
                else vnc.display_server
                or (
                    "Install Xvfb and make it available on PATH."
                    if vnc.kind == "x11vnc"
                    else "Install Sway and make it available on PATH."
                ),
            )
        )
    if vnc and vnc.kind == "wayvnc":
        requirements.append(
            BrowserRequirement(
                name="Host account",
                installed=os.geteuid() != 0,
                purpose="Headless Sway requires Harness UI to run as a non-root user.",
                detail="Running as a non-root user." if os.geteuid() != 0 else "Restart Harness UI as a non-root user.",
            )
        )
    if supported and (not vnc or vnc.kind == "tigervnc"):
        requirements.append(
            BrowserRequirement(
                name="Desktop resizing",
                installed=resizing is not None,
                required=False,
                purpose="xrandr enables Viewport size switching. Without it, browsing and local Zoom still work.",
                detail=resizing or "Install xrandr and make it available on PATH to enable resizing.",
            )
        )
    if supported and (not vnc or vnc.kind != "wayvnc"):
        requirements.append(
            BrowserRequirement(
                name="Window management",
                installed=manager is not None,
                required=False,
                purpose="Openbox, Fluxbox, IceWM or twm adds window borders, moving and window controls.",
                detail=manager or "No supported window manager found on PATH; basic browsing remains available.",
            )
        )
    ready = supported and all(requirement.installed for requirement in requirements if requirement.required)
    steps = []
    if supported and vnc is None and shutil.which("apt-get"):
        steps.append(
            BrowserInstallStep(
                title="Install a compatible VNC server (if needed)",
                command="sudo apt-get update\nsudo apt-get install --no-install-recommends tigervnc-standalone-server",
            )
        )
    if supported and vnc and vnc.kind != "tigervnc" and not vnc.display_server and shutil.which("apt-get"):
        package = "xvfb" if vnc.kind == "x11vnc" else "sway"
        steps.append(
            BrowserInstallStep(
                title="Install the selected VNC display component",
                command=f"sudo apt-get install --no-install-recommends {package}",
            )
        )
    if supported and vnc and shutil.which("apt-get"):
        if vnc.kind == "tigervnc" and not resizing:
            steps.append(
                BrowserInstallStep(
                    title="Optional: enable viewport resizing",
                    command="sudo apt-get install --no-install-recommends x11-xserver-utils",
                )
            )
        if vnc.kind != "wayvnc" and not manager:
            steps.append(
                BrowserInstallStep(
                    title="Optional: add window management",
                    command="sudo apt-get install --no-install-recommends openbox",
                )
            )
    if not supported:
        message = "The remote desktop currently requires a Linux Host. Browser detection is available on this platform."
    elif sandbox_blocked:
        message = sandbox_guidance
    elif ready:
        message = "Browser and VNC executables are ready. Harness manages their startup and cleanup."
    else:
        message = "Select browser and VNC executables in General settings, save and restart Harness UI. Prepare any missing components on this Host."
        if not shutil.which("apt-get"):
            message += " Use your distribution's package manager for Chromium and a compatible VNC server."
    return BrowserSetup(
        platform=platform,
        distribution=distribution.get("PRETTY_NAME"),
        supported=supported,
        ready=ready,
        browser=browser,
        vnc=vnc,
        requirements=tuple(requirements),
        steps=tuple(steps),
        message=message,
    )
