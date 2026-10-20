"""Read-only browser preparation checks and platform-specific installation guidance."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import ntpath
import os
import shlex
import shutil
import sys
from pathlib import Path
from platform import freedesktop_os_release
from typing import Literal

from pydantic import Field

from a13n_harness_ui.surfaces import SurfaceModel

type BrowserPlatform = Literal["linux", "macos", "windows", "other"]


class BrowserExecutable(SurfaceModel):
    name: str
    path: str
    source: Literal["system", "playwright"]


class BrowserRequirement(SurfaceModel):
    name: str
    installed: bool
    detail: str


class BrowserInstallStep(SurfaceModel):
    title: str
    command: str


class BrowserInstallState(SurfaceModel):
    status: Literal["idle", "installing", "succeeded", "failed"] = "idle"
    step: str = ""
    output: str = ""
    error: str | None = None


class BrowserSetup(SurfaceModel):
    platform: BrowserPlatform
    distribution: str | None = None
    supported: bool
    ready: bool
    browser: BrowserExecutable | None = None
    requirements: tuple[BrowserRequirement, ...] = ()
    steps: tuple[BrowserInstallStep, ...] = ()
    message: str = ""
    installable: bool = False
    install_message: str = ""
    installation: BrowserInstallState = Field(default_factory=BrowserInstallState)


def host_platform() -> BrowserPlatform:
    platforms: dict[str, BrowserPlatform] = {"linux": "linux", "darwin": "macos", "win32": "windows"}
    return platforms.get(sys.platform, "other")


def linux_distribution() -> dict[str, str]:
    try:
        return freedesktop_os_release()
    except OSError:
        return {}


def system_browser(platform: BrowserPlatform) -> BrowserExecutable | None:
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
            if executable := shutil.which(command):
                candidates.append((name, executable))
    elif platform == "macos":
        for root in (Path("/Applications"), Path.home() / "Applications"):
            for name, executable in (
                ("Google Chrome", "Google Chrome.app/Contents/MacOS/Google Chrome"),
                ("Chromium", "Chromium.app/Contents/MacOS/Chromium"),
                ("Microsoft Edge", "Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            ):
                candidates.append((name, str(root / executable)))
    elif platform == "windows":
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            if root := os.environ.get(variable):
                for name, executable in (
                    ("Google Chrome", r"Google\Chrome\Application\chrome.exe"),
                    ("Chromium", r"Chromium\Application\chrome.exe"),
                    ("Microsoft Edge", r"Microsoft\Edge\Application\msedge.exe"),
                ):
                    candidates.append((name, ntpath.join(root, executable)))
    for name, executable in candidates:
        if Path(executable).is_file():
            return BrowserExecutable(name=name, path=executable, source="system")
    return None


async def inspect_browser() -> BrowserSetup:
    importlib.invalidate_caches()
    platform = host_platform()
    supported = platform == "linux"
    distribution = await asyncio.to_thread(linux_distribution) if supported else {}
    minimum = {"ubuntu": 22, "debian": 12}.get(distribution.get("ID", ""))
    major = distribution.get("VERSION_ID", "").split(".")[0]
    unsupported_version = minimum is not None and major.isdecimal() and int(major) < minimum
    if unsupported_version:
        supported = False
    browser = await asyncio.to_thread(system_browser, platform)
    automation = importlib.util.find_spec("playwright") is not None
    repair_automation = False
    automation_detail = (
        "Installed in this Python environment" if automation else "Install the optional Playwright package"
    )
    if automation:
        try:
            from playwright.async_api import async_playwright

            async with asyncio.timeout(10), async_playwright() as driver:
                executable = driver.chromium.executable_path
                if await asyncio.to_thread(Path(executable).is_file):
                    browser = BrowserExecutable(name="Chromium", path=executable, source="playwright")
        except Exception:
            automation = False
            repair_automation = True
            automation_detail = "Playwright could not initialize; repair its installation in this Python environment"
    groups = (
        ("Remote desktop", ("Xtigervnc", "tigervncpasswd")),
        ("Window manager", ("openbox",)),
        ("Display tools", ("xrandr", "xauth")),
    )
    requirements = [
        BrowserRequirement(name="Browser automation", installed=automation, detail=automation_detail),
        BrowserRequirement(
            name="Compatible browser",
            installed=browser is not None,
            detail=f"{browser.name} ({browser.source})" if browser else "Chrome, Chromium or Edge was not found",
        ),
    ]
    missing_tools: list[str] = []
    if platform == "linux":
        for name, commands in groups:
            missing = [command for command in commands if shutil.which(command) is None]
            missing_tools.extend(missing)
            requirements.append(
                BrowserRequirement(
                    name=name,
                    installed=not missing,
                    detail="Missing: " + ", ".join(missing) if missing else ", ".join(commands),
                )
            )
    ready = supported and all(item.installed for item in requirements)
    steps = []
    python = shlex.quote(sys.executable)
    apt_available = supported and shutil.which("apt-get") is not None
    package_installer = shutil.which("uv")
    pip_available = importlib.util.find_spec("pip") is not None
    if supported and not ready:
        if not automation:
            if package_installer:
                installer = f"{shlex.quote(package_installer)} pip install --python {python}"
                repair = " --reinstall-package playwright" if repair_automation else ""
            else:
                installer = f"{python} -m pip install"
                repair = " --force-reinstall" if repair_automation else ""
            steps.append(
                BrowserInstallStep(
                    title="Install browser automation",
                    command=f"{installer}{repair} 'playwright>=1.58,<2'",
                )
            )
        if browser is None:
            dependencies = " --with-deps" if apt_available else ""
            steps.append(
                BrowserInstallStep(
                    title="Install Chromium and its system libraries" if apt_available else "Install Chromium",
                    command=f"{python} -m playwright install{dependencies} --no-shell chromium",
                )
            )
        if missing_tools and apt_available:
            prefix = "" if os.geteuid() == 0 else "sudo "
            steps.append(
                BrowserInstallStep(
                    title="Install the Linux desktop components",
                    command=f"{prefix}apt-get update\n{prefix}apt-get install -y --no-install-recommends "
                    "tigervnc-standalone-server tigervnc-tools x11-xserver-utils xauth openbox",
                )
            )
    message = (
        "Browser support is ready on this Host."
        if ready
        else "Install the missing components on the Host running Harness UI."
    )
    if not supported:
        message = (
            f"{distribution.get('PRETTY_NAME', 'This Linux release')} is not supported by the current browser runtime. Use Ubuntu 22.04 or newer, or Debian 12 or newer."
            if unsupported_version
            else "The remote desktop currently requires a Linux Host. Browser detection is available for this platform, but desktop support is not implemented yet."
        )
    elif not apt_available and (missing_tools or browser is None):
        message += " Install any missing browser shared libraries, TigerVNC, its password tool, Openbox, xrandr and xauth using your distribution's package manager."
    installable = supported and not ready and bool(steps)
    install_message = "Installs the missing components on this Host using its current account."
    if not supported:
        installable, install_message = False, message
    elif not apt_available and (missing_tools or browser is None):
        installable = False
        install_message = "Automatic desktop installation currently supports apt-based Hosts. Use your distribution's package manager, then check again."
    elif missing_tools and os.geteuid() != 0:
        installable = False
        install_message = (
            "System packages require an administrator. Run the commands below on the Host, then check again."
        )
    elif browser is None and os.geteuid() != 0:
        installable = False
        install_message = (
            "Browser system libraries require an administrator. Prepare them on the Host, then check again."
        )
    elif not automation and not package_installer and not pip_available:
        installable = False
        install_message = "Install uv or pip in this Host environment before installing browser automation."
    return BrowserSetup(
        platform=platform,
        distribution=distribution.get("PRETTY_NAME"),
        supported=supported,
        ready=ready,
        browser=browser,
        requirements=tuple(requirements),
        steps=tuple(steps),
        message=message,
        installable=installable,
        install_message=install_message,
    )
