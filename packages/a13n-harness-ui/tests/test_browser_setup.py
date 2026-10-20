from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness_ui import browser_setup as module
from a13n_harness_ui.browser_setup import BrowserExecutable

pytestmark = pytest.mark.anyio


@pytest.fixture
def host(monkeypatch):
    monkeypatch.setattr(module, "host_platform", lambda: "linux")
    monkeypatch.setattr(module, "linux_distribution", lambda: {})
    monkeypatch.setattr(module.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(module.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(module, "system_browser", lambda platform: None)
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda name: None)


@pytest.mark.parametrize(
    ("platform", "expected"), [("linux", "linux"), ("darwin", "macos"), ("win32", "windows"), ("freebsd", "other")]
)
async def test_platform_mapping(monkeypatch, platform, expected):
    monkeypatch.setattr(module.sys, "platform", platform)
    assert module.host_platform() == expected


@pytest.mark.parametrize(
    ("platform", "path", "name"),
    [
        ("linux", "/usr/bin/chromium", "Chromium"),
        ("macos", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "Google Chrome"),
        ("windows", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", "Microsoft Edge"),
    ],
)
async def test_detects_existing_compatible_browsers(monkeypatch, platform, path, name):
    monkeypatch.setenv("PROGRAMFILES", r"C:\Program Files")
    monkeypatch.setattr(module.shutil, "which", lambda command: path if command == "chromium" else None)
    monkeypatch.setattr(module.Path, "is_file", lambda candidate: str(candidate) == path)
    browser = module.system_browser(platform)
    assert browser == BrowserExecutable(name=name, path=path, source="system")


async def test_missing_components_have_manual_commands_without_starting_a_browser(host, monkeypatch):
    monkeypatch.setattr(
        module.shutil, "which", lambda command: f"/usr/bin/{command}" if command in {"apt-get", "uv"} else None
    )
    setup = await module.inspect_browser()
    assert setup.supported and not setup.ready
    assert all(not requirement.installed for requirement in setup.requirements)
    commands = "\n".join(step.command for step in setup.steps)
    assert "uv pip install --python" in commands
    assert "install --with-deps --no-shell chromium" in commands
    assert "sudo apt-get install" in commands
    assert "tigervnc-tools" in commands


async def test_reuses_system_browser_without_download_step(host, monkeypatch):
    chrome = BrowserExecutable(name="Google Chrome", path="/opt/chrome", source="system")
    monkeypatch.setattr(module, "system_browser", lambda platform: chrome)
    setup = await module.inspect_browser()
    assert setup.browser == chrome
    assert len(setup.steps) == 1
    assert setup.steps[0].title == "Install browser automation"


@pytest.mark.parametrize("bundled", [True, False])
async def test_playwright_probe_uses_public_driver_without_launching_browser(host, monkeypatch, bundled):
    import playwright.async_api

    monkeypatch.setattr(module.importlib.util, "find_spec", lambda name: object())
    chrome = BrowserExecutable(name="Microsoft Edge", path="/opt/edge", source="system")
    monkeypatch.setattr(module, "system_browser", lambda platform: chrome)
    monkeypatch.setattr(module.Path, "is_file", lambda path: bundled)
    driver = SimpleNamespace(chromium=SimpleNamespace(executable_path="/cache/chromium", launch=AsyncMock()))
    context = AsyncMock()
    context.__aenter__.return_value = driver
    monkeypatch.setattr(playwright.async_api, "async_playwright", Mock(return_value=context))
    setup = await module.inspect_browser()
    assert setup.ready and not setup.steps
    assert setup.browser and setup.browser.source == ("playwright" if bundled else "system")
    driver.chromium.launch.assert_not_awaited()
    context.__aexit__.assert_awaited_once()


async def test_broken_driver_offers_repair(host, monkeypatch):
    import playwright.async_api

    monkeypatch.setattr(module.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(playwright.async_api, "async_playwright", Mock(side_effect=RuntimeError("broken")))
    setup = await module.inspect_browser()
    assert not setup.ready
    assert "repair" in setup.requirements[0].detail
    assert "playwright>=" in setup.steps[0].command
    assert "--reinstall-package playwright" in setup.steps[0].command


@pytest.mark.parametrize("platform", ["macos", "windows", "other"])
async def test_unsupported_desktop_does_not_hide_existing_browser_or_offer_linux_commands(host, monkeypatch, platform):
    monkeypatch.setattr(module, "host_platform", lambda: platform)
    monkeypatch.setattr(
        module, "system_browser", lambda platform: BrowserExecutable(name="Chrome", path="/chrome", source="system")
    )
    setup = await module.inspect_browser()
    assert not setup.supported and not setup.ready
    assert setup.browser
    assert not setup.steps
    assert len(setup.requirements) == 2
    assert "Linux Host" in setup.message


async def test_non_debian_linux_uses_distribution_guidance(host, monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda command: None)
    setup = await module.inspect_browser()
    assert not setup.ready
    assert "distribution's package manager" in setup.message
    assert all("apt-get" not in step.command for step in setup.steps)
    assert all("--with-deps" not in step.command for step in setup.steps)
    assert not setup.installable
    assert "apt-based" in setup.install_message


@pytest.mark.parametrize("uid", [0, 1000])
async def test_automatic_system_installation_uses_existing_privileges_without_elevation(host, monkeypatch, uid):
    monkeypatch.setattr(module.os, "geteuid", lambda: uid)
    setup = await module.inspect_browser()
    assert setup.installable == (uid == 0)
    if uid:
        assert "administrator" in setup.install_message
    else:
        assert all("sudo" not in step.command for step in setup.steps)


@pytest.mark.parametrize(
    ("identifier", "version", "supported"),
    [
        ("ubuntu", "20.04", False),
        ("ubuntu", "22.04", True),
        ("ubuntu", "24.04", True),
        ("ubuntu", "26.04", True),
        ("debian", "11", False),
        ("debian", "12", True),
        ("debian", "13", True),
    ],
)
async def test_linux_browser_runtime_baselines(host, monkeypatch, identifier, version, supported):
    monkeypatch.setattr(
        module,
        "linux_distribution",
        lambda: {"ID": identifier, "VERSION_ID": version, "PRETTY_NAME": f"{identifier} {version}"},
    )
    setup = await module.inspect_browser()
    assert setup.supported == supported
    assert setup.distribution == f"{identifier} {version}"
    if not supported:
        assert not setup.ready and not setup.installable and not setup.steps
        assert "not supported" in setup.message


@pytest.mark.parametrize("pip", [True, False])
async def test_python_automation_can_use_pip_without_uv_when_system_components_exist(host, monkeypatch, pip):
    monkeypatch.setattr(module.shutil, "which", lambda command: None if command == "uv" else f"/usr/bin/{command}")
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda name: object() if name == "pip" and pip else None)
    monkeypatch.setattr(
        module,
        "system_browser",
        lambda platform: BrowserExecutable(name="Chromium", path="/usr/bin/chromium", source="system"),
    )
    setup = await module.inspect_browser()
    assert setup.installable == pip
    assert len(setup.steps) == 1
    assert "-m pip install" in setup.steps[0].command
    if not pip:
        assert "Install uv or pip" in setup.install_message
