from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness_ui import browser_setup as module
from a13n_harness_ui.browser_setup import BrowserExecutable
from a13n_harness_ui.configuration.models import BrowserConfiguration

pytestmark = pytest.mark.anyio
VNC_HELP = "TigerVNC Xvnc rfbunixpath rfbport PasswordFile SecurityTypes displayfd"


@pytest.fixture
def host(monkeypatch):
    monkeypatch.setattr(module, "host_platform", lambda: "linux")
    monkeypatch.setattr(module.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(module, "linux_distribution", lambda: {})
    monkeypatch.setattr(module.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(module, "executable_path", lambda path: path)
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(
        module,
        "probe",
        AsyncMock(side_effect=lambda path, argument: VNC_HELP if argument == "-help" else "Chromium 156.0"),
    )

    return BrowserConfiguration(executable="/usr/bin/chromium", vnc_executable="/usr/bin/Xvnc")


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
async def test_detects_existing_browsers(monkeypatch, platform, path, name):
    monkeypatch.setenv("PROGRAMFILES", r"C:\Program Files")
    monkeypatch.setattr(module.shutil, "which", lambda name: path if name == "chromium" else None)
    monkeypatch.setattr(module, "executable_path", lambda candidate: path if candidate == path else None)
    assert module.system_browsers(platform) == [BrowserExecutable(name=name, path=path, source="system")]


async def test_resolves_both_vnc_names_and_binds_actual_executable_paths(host, monkeypatch):
    available = {"Xvnc", "vncpasswd", "openbox", "xrandr", "xauth"}
    monkeypatch.setattr(module.shutil, "which", lambda name: f"/opt/bin/{name}" if name in available else None)
    setup = await module.inspect_browser(host.model_copy(update={"vnc_executable": "/opt/bin/Xvnc"}))
    assert setup.ready and setup.vnc
    assert setup.vnc.path == "/opt/bin/Xvnc"
    assert setup.vnc.window_manager == "/opt/bin/openbox"
    assert setup.vnc.xrandr == "/opt/bin/xrandr"
    assert not setup.steps
    assert all(call.args[1] in {"--version", "-help"} for call in module.probe.await_args_list)
    assert "installation" not in setup.model_dump() and "installable" not in setup.model_dump()


async def test_explicit_executable_bindings_override_detection(host, monkeypatch):
    automatic = Mock(side_effect=AssertionError("Must not fall back"))
    monkeypatch.setattr(module, "system_browsers", automatic)
    setup = await module.inspect_browser(
        BrowserConfiguration(executable="/custom/chrome", vnc_executable="/custom/server")
    )
    assert setup.ready and setup.browser and setup.vnc
    assert setup.browser.path == "/custom/chrome" and setup.browser.source == "custom"
    assert setup.vnc.path == "/custom/server" and setup.vnc.source == "custom"
    automatic.assert_not_called()


@pytest.mark.parametrize("field", ["executable", "vnc_executable"])
async def test_invalid_custom_path_does_not_silently_fall_back(host, monkeypatch, field):
    monkeypatch.setattr(module, "executable_path", lambda path: None if path == "/missing" else path)
    setup = await module.inspect_browser(host.model_copy(update={field: "/missing"}))
    assert not setup.ready
    assert getattr(setup, "browser" if field == "executable" else "vnc") is None
    assert any("Configured" in requirement.detail for requirement in setup.requirements)


@pytest.mark.parametrize(
    "help_text", ["TightVNC rfbunixpath rfbport PasswordFile SecurityTypes displayfd", "TigerVNC rfbport", ""]
)
async def test_rejects_incompatible_vnc_instead_of_treating_presence_as_readiness(host, monkeypatch, help_text):
    monkeypatch.setattr(
        module,
        "probe",
        AsyncMock(side_effect=lambda path, argument: help_text if argument == "-help" else "Chromium 156"),
    )
    setup = await module.inspect_browser(host)
    assert not setup.ready and setup.vnc is None
    assert "compatible TigerVNC" in setup.requirements[1].detail


async def test_authentication_utilities_are_not_required(host, monkeypatch):
    monkeypatch.setattr(
        module.shutil,
        "which",
        lambda name: None if name in {"tigervncpasswd", "vncpasswd", "xauth"} else f"/usr/bin/{name}",
    )
    setup = await module.inspect_browser(host)
    assert setup.ready and setup.vnc
    assert not setup.steps


async def test_missing_components_only_produce_manual_guidance(host, monkeypatch):
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/apt-get" if name == "apt-get" else None)
    setup = await module.inspect_browser()
    assert setup.supported and not setup.ready
    assert not any(requirement.installed for requirement in setup.requirements)
    assert any("a13n-harness-ui[browser]" in step.command for step in setup.steps)
    module.probe.assert_not_awaited()


@pytest.mark.parametrize("platform", ["macos", "windows", "other"])
async def test_other_platforms_report_browser_but_never_offer_linux_runtime(host, monkeypatch, platform):
    monkeypatch.setattr(module, "host_platform", lambda: platform)
    setup = await module.inspect_browser(host)
    assert setup.browser and not setup.supported and not setup.ready
    assert setup.vnc is None and not setup.steps
    assert "Linux Host" in setup.message


@pytest.mark.parametrize(("identifier", "version"), [("ubuntu", "20.04"), ("debian", "11")])
async def test_distribution_age_does_not_override_detected_runtime_capabilities(host, monkeypatch, identifier, version):
    monkeypatch.setattr(module, "linux_distribution", lambda: {"ID": identifier, "VERSION_ID": version})
    assert (await module.inspect_browser(host)).ready


async def test_version_probe_rejects_non_chromium_browser(host, monkeypatch):
    monkeypatch.setattr(
        module,
        "probe",
        AsyncMock(side_effect=lambda path, argument: VNC_HELP if argument == "-help" else "Firefox 140"),
    )
    setup = await module.inspect_browser(host)
    assert not setup.ready and setup.browser is None


async def test_probe_handles_paths_with_spaces_and_bounds_output(tmp_path):
    executable = tmp_path / "fake executable"
    executable.write_text("#!/bin/sh\nprintf 'Chromium version test'\n")
    executable.chmod(0o700)
    assert await module.probe(str(executable), "--version") == "Chromium version test"
    executable.write_text("#!/bin/sh\nhead -c 70000 /dev/zero\n")
    assert await module.probe(str(executable), "-help") == ""


async def test_probe_timeout_cleans_up_child(tmp_path, monkeypatch):
    executable = tmp_path / "slow"
    executable.write_text("#!/bin/sh\nexec sleep 10\n")
    executable.chmod(0o700)
    timeout = module.asyncio.timeout
    monkeypatch.setattr(module.asyncio, "timeout", lambda seconds: timeout(0.02))
    assert await module.probe(str(executable), "-help") == ""


async def test_missing_vnc_does_not_misreport_installed_desktop_helpers(host, monkeypatch):
    monkeypatch.setattr(
        module.shutil, "which", lambda name: None if name in {"Xtigervnc", "Xvnc"} else f"/usr/bin/{name}"
    )
    monkeypatch.setattr(module, "executable_path", lambda path: None if path == host.vnc_executable else path)
    setup = await module.inspect_browser(host)
    assert not setup.ready and setup.vnc is None
    assert setup.requirements[-1].installed


async def test_scan_lists_supported_candidates_once_without_launching_or_installing(host, monkeypatch):
    paths = {
        "google-chrome": "/opt/chrome",
        "google-chrome-stable": "/opt/chrome",
        "chromium": "/opt/chromium",
        "microsoft-edge": "/opt/broken",
        "Xtigervnc": "/opt/Xvnc",
        "Xvnc": "/opt/Xvnc",
    }
    monkeypatch.setattr(module.shutil, "which", paths.get)
    monkeypatch.setattr(module, "playwright_browser", AsyncMock(return_value=None))
    monkeypatch.setattr(
        module,
        "probe",
        AsyncMock(
            side_effect=lambda path, argument: (
                "" if path == "/opt/broken" else VNC_HELP if argument == "-help" else "Chromium 156"
            )
        ),
    )
    scan = await module.scan_browsers()
    assert [item.path for item in scan.browsers] == ["/opt/chrome", "/opt/chromium"]
    assert [item.path for item in scan.vncs] == ["/opt/Xvnc"]
    assert module.probe.await_count == 4


async def test_scan_includes_cached_playwright_browser_and_filters_incompatible_vnc(host, monkeypatch):
    monkeypatch.setattr(module, "system_browsers", lambda platform: [])
    monkeypatch.setattr(
        module,
        "playwright_browser",
        AsyncMock(return_value=BrowserExecutable(name="Chromium", path="/cached/chrome", source="playwright")),
    )
    monkeypatch.setattr(module, "probe", AsyncMock(return_value="Chromium 156"))
    scan = await module.scan_browsers()
    assert [item.path for item in scan.browsers] == ["/cached/chrome"]
    assert not scan.vncs


@pytest.mark.parametrize("missing", ["executable", "vnc_executable", "both"])
async def test_missing_binding_never_selects_an_installed_candidate(host, monkeypatch, missing):
    system = Mock(side_effect=AssertionError("Readiness must not choose a browser"))
    bundled = AsyncMock(side_effect=AssertionError("Readiness must not choose bundled Chromium"))
    monkeypatch.setattr(module, "system_browsers", system)
    monkeypatch.setattr(module, "playwright_browser", bundled)
    configuration = host.model_copy(
        update={field: None for field in ("executable", "vnc_executable") if field == missing or missing == "both"}
    )
    setup = await module.inspect_browser(configuration)
    assert not setup.ready
    if missing in {"executable", "both"}:
        assert setup.browser is None and "Select a browser" in setup.requirements[0].detail
    if missing in {"vnc_executable", "both"}:
        assert setup.vnc is None and "Select a VNC" in setup.requirements[1].detail
    system.assert_not_called()
    bundled.assert_not_awaited()


@pytest.mark.parametrize("manager", ["openbox", "fluxbox", "icewm", "twm", None])
async def test_window_manager_is_optional_and_not_tied_to_openbox(host, monkeypatch, manager):
    monkeypatch.setattr(
        module.shutil, "which", lambda name: f"/opt/{name}" if name == manager or name == "xrandr" else None
    )
    setup = await module.inspect_browser(host)
    assert setup.ready and setup.vnc
    assert setup.vnc.window_manager == (f"/opt/{manager}" if manager else None)
    requirement = next(item for item in setup.requirements if item.name == "Window management")
    assert not requirement.required and requirement.installed == (manager is not None)


async def test_missing_desktop_utilities_keep_basic_browsing_available(host, monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    setup = await module.inspect_browser(host)
    assert setup.ready and setup.vnc
    assert setup.vnc.window_manager is None and setup.vnc.xrandr is None
    assert all(item.installed for item in setup.requirements if item.required)
    assert not any(item.installed for item in setup.requirements if not item.required)
    assert not setup.steps


@pytest.mark.parametrize(("kind", "helper"), [("x11vnc", "Xvfb"), ("wayvnc", "sway")])
async def test_alternative_vnc_requires_its_managed_display_component(host, monkeypatch, kind, helper):
    options = "-unixsock -passwdfile -display -shared"
    wayland_help = "--unix-socket --disable-input --config --socket --disable-resizing"

    async def probe(path, argument):
        if path == host.executable:
            return "Chromium 156"
        if argument == "-opts" and kind == "x11vnc":
            return options
        if argument == "--help" and kind == "wayvnc":
            return wayland_help
        return "wayvnc: 0.9.1" if argument == "--version" else ""

    monkeypatch.setattr(module, "probe", probe)
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    setup = await module.inspect_browser(host)
    assert setup.vnc and setup.vnc.kind == kind and not setup.ready
    assert any(item.name == "Display server" and item.required and not item.installed for item in setup.requirements)
    monkeypatch.setattr(module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == helper else None)
    ready = await module.inspect_browser(host)
    assert ready.ready and ready.vnc and ready.vnc.display_server == f"/usr/bin/{helper}"
    assert ready.vnc.xrandr is None


@pytest.mark.parametrize(
    ("version", "resize_flag", "accepted"),
    [("0.7.2", False, True), ("0.8.0", False, False), ("0.9.1", True, True), ("unknown", False, False)],
)
async def test_wayvnc_cannot_enable_uncontrolled_client_resizing(monkeypatch, version, resize_flag, accepted):
    help_text = "--unix-socket --disable-input --config --socket" + (" --disable-resizing" if resize_flag else "")
    monkeypatch.setattr(
        module,
        "probe",
        AsyncMock(
            side_effect=lambda path, argument: (
                help_text if argument == "--help" else f"wayvnc: {version}" if argument == "--version" else ""
            )
        ),
    )
    detected = await module.inspect_vnc("/custom/wayvnc", "custom")
    assert (detected is not None) == accepted
    if detected:
        assert detected.disable_resizing == resize_flag
