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
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/apt-get" if name == "apt-get" else None)
    setup = await module.inspect_browser()
    assert setup.supported and not setup.ready
    assert not any(requirement.installed for requirement in setup.requirements)
    assert setup.steps and all("apt-get" in step.command for step in setup.steps)
    module.probe.assert_not_awaited()


@pytest.mark.parametrize("sharing", [False, True])
@pytest.mark.parametrize("password", ["", "testpass"])
async def test_macos_requires_native_sharing_and_host_only_password(host, monkeypatch, sharing, password):
    monkeypatch.setattr(module, "host_platform", lambda: "macos")
    monkeypatch.setattr(module, "screen_sharing_available", AsyncMock(return_value=sharing))
    monkeypatch.setenv(module.PASSWORD_ENV, password)
    setup = await module.inspect_browser(host)
    assert setup.supported and setup.ready == (sharing and bool(password))
    assert setup.vnc is None and not setup.steps
    assert [item.name for item in setup.requirements] == ["Browser", "Screen Sharing", "VNC password"]
    assert not password or password not in setup.model_dump_json()
    module.probe.assert_awaited_once_with(host.executable, "--version")


@pytest.mark.parametrize("explicit", [False, True])
async def test_macos_selects_browser_without_discovering_linux_components(host, monkeypatch, explicit):
    monkeypatch.setattr(module, "host_platform", lambda: "macos")
    monkeypatch.setattr(module, "screen_sharing_available", AsyncMock(return_value=True))
    monkeypatch.setenv(module.PASSWORD_ENV, "testpass")
    browser = BrowserExecutable(name="Chrome", path="/Applications/Chrome", source="system")
    discovered = Mock(return_value=[browser])
    monkeypatch.setattr(module, "system_browsers", discovered)
    monkeypatch.setattr(module, "linux_distribution", Mock(side_effect=AssertionError("Linux only")))
    monkeypatch.setattr(module, "find_program", Mock(side_effect=AssertionError("Linux only")))
    configuration = BrowserConfiguration(executable=browser.path if explicit else None)
    setup = await module.inspect_browser(configuration)
    assert setup.ready and setup.browser and setup.browser.path == browser.path
    assert setup.browser.source == ("custom" if explicit else "system")
    assert setup.vnc is None
    assert configuration.vnc_executable is None
    if explicit:
        discovered.assert_not_called()
    else:
        discovered.assert_called_once_with("macos")


@pytest.mark.parametrize("platform", ["windows", "other"])
async def test_other_platforms_report_platform_limit_without_probing_or_missing_components(host, monkeypatch, platform):
    monkeypatch.setattr(module, "host_platform", lambda: platform)
    setup = await module.inspect_browser(host)
    assert not setup.supported and not setup.ready
    assert setup.browser is None and setup.vnc is None
    assert not setup.steps and not setup.requirements
    assert "Linux or macOS Host" in setup.message
    module.probe.assert_not_awaited()


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


@pytest.mark.parametrize("missing", ["executable", "vnc_executable", "both"])
async def test_missing_bindings_automatically_select_installed_candidates_without_changing_configuration(host, missing):
    configuration = host.model_copy(
        update={field: None for field in ("executable", "vnc_executable") if field == missing or missing == "both"}
    )
    original = configuration.model_dump()
    setup = await module.inspect_browser(configuration)
    assert setup.ready and setup.browser and setup.vnc
    assert setup.browser.source == ("system" if missing in {"executable", "both"} else "custom")
    assert setup.vnc.source == ("system" if missing in {"vnc_executable", "both"} else "custom")
    assert configuration.model_dump() == original


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


@pytest.mark.parametrize("kind", ["tigervnc", "x11vnc", "wayvnc"])
async def test_requirement_purposes_and_optional_components_follow_selected_adapter(host, monkeypatch, kind):
    monkeypatch.setattr(
        module, "inspect_vnc", AsyncMock(return_value=module.VncExecutable(kind=kind, path="/server", source="custom"))
    )
    setup = await module.inspect_browser(host)
    checks = {item.name: item for item in setup.requirements}
    assert all(item.purpose for item in setup.requirements)
    display = checks["Display server"]
    assert display.installed and display.required
    if kind == "tigervnc":
        assert "no separate Xvfb" in display.purpose
        assert not checks["Desktop resizing"].required
    else:
        assert "Desktop resizing" not in checks
        assert ("Xvfb" if kind == "x11vnc" else "Sway") in display.purpose
    if kind == "wayvnc":
        assert "Window management" not in checks and checks["Host account"].required
    else:
        assert not checks["Window management"].required


async def test_wayvnc_reports_root_account_as_missing_requirement(host, monkeypatch):
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        module,
        "inspect_vnc",
        AsyncMock(return_value=module.VncExecutable(kind="wayvnc", path="/server", source="custom")),
    )
    setup = await module.inspect_browser(host)
    account = next(item for item in setup.requirements if item.name == "Host account")
    assert not setup.ready and not account.installed and account.required


async def test_settings_check_uses_draft_paths_without_publishing_or_launching(tmp_path, monkeypatch):
    from a13n_harness_ui import app as app_module

    from .test_sharing_webui import shared_server

    checked = AsyncMock(return_value=module.BrowserSetup(platform="linux", supported=True, ready=False))
    monkeypatch.setattr(app_module, "inspect_browser", checked)
    async with shared_server(tmp_path, share_computer=True) as (_, _, client):
        configuration = tmp_path / "a13n-harness-ui.yaml"
        saved = configuration.read_text()
        body = {"executable": "/draft/chrome", "vnc_executable": "/draft/x11vnc"}
        denied = await client.post("/api/browser/check", json=body)
        assert denied.status_code == 401
        checked.assert_not_awaited()
        # The check passes a detached candidate to discovery and never publishes it.
        response = await client.post(
            "/api/browser/check", json=body, headers={"Authorization": "Bearer owner-test-key"}
        )
        assert response.status_code == 200 and response.json()["ready"] is False
        checked.assert_awaited_once_with(BrowserConfiguration(**body), no_sandbox=False)
        assert configuration.read_text() == saved
        checked.reset_mock()
        invalid = await client.post(
            "/api/browser/check", json={"executable": "relative"}, headers={"Authorization": "Bearer owner-test-key"}
        )
        assert invalid.status_code == 400
        checked.assert_not_awaited()


@pytest.mark.parametrize("no_sandbox", [False, True])
async def test_root_preparation_and_start_share_explicit_sandbox_policy(host, monkeypatch, tmp_path, no_sandbox):
    from a13n_harness_ui.browser import Binding, HostBrowsers
    from a13n_harness_ui.errors import HarnessUiError

    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    setup = await module.inspect_browser(host, no_sandbox=no_sandbox)
    assert setup.ready is no_sandbox
    if not no_sandbox:
        assert "non-root" in setup.message and "--browser-no-sandbox" in setup.message
        browsers = HostBrowsers(root=tmp_path, available=True, authorize=AsyncMock(), configuration=host)
        binding = browsers.bindings["a"] = Binding()
        with pytest.raises(HarnessUiError, match=r"non-root.*--browser-no-sandbox"):
            await browsers.start("a", binding.identity, explicit=True)
        assert browsers.process is None and browsers.desktop is None


async def test_disabled_sharing_blocks_browser_discovery_before_probing(tmp_path, monkeypatch):
    from a13n_harness_ui import app as app_module
    from a13n_harness_ui.errors import HarnessUiError

    from .test_app import _settings, _write_configuration

    scan, check = AsyncMock(), AsyncMock()
    monkeypatch.setattr(app_module, "scan_browsers", scan)
    monkeypatch.setattr(app_module, "inspect_browser", check)
    async with app_module.open_harness_ui_app(
        _settings(tmp_path / "state"),
        configuration_path=_write_configuration(tmp_path),
        host_mode="webui",
        share_computer=False,
    ) as app:
        with pytest.raises(HarnessUiError, match="sharing"):
            await app.scan_browser_executables()
        with pytest.raises(HarnessUiError, match="sharing"):
            await app.check_browser_configuration(BrowserConfiguration())
    scan.assert_not_awaited()
    check.assert_not_awaited()


@pytest.mark.parametrize("complete", [True, False])
async def test_automatic_vnc_prefers_complete_adapter_and_retains_missing_diagnostics(host, monkeypatch, complete):
    monkeypatch.setattr(
        module,
        "scan_browsers",
        AsyncMock(
            return_value=module.BrowserScan(
                vncs=(
                    module.VncExecutable(kind="x11vnc", path="/x11vnc", source="system"),
                    module.VncExecutable(kind="wayvnc", path="/wayvnc", source="system"),
                )
            )
        ),
    )
    monkeypatch.setattr(module, "find_program", lambda *names: "/sway" if complete and "sway" in names else None)
    setup = await module.inspect_browser(host.model_copy(update={"vnc_executable": None}))
    assert setup.vnc and setup.ready == complete
    assert setup.vnc.kind == ("wayvnc" if complete else "x11vnc")
    assert setup.vnc.xrandr is None
    if not complete:
        assert any(item.name == "Display server" and not item.installed for item in setup.requirements)


@pytest.mark.parametrize("field", ["executable", "vnc_executable"])
async def test_automatic_other_component_does_not_replace_invalid_override(host, monkeypatch, field):
    monkeypatch.setattr(module, "executable_path", lambda path: None if path == "/missing" else path)
    configuration = BrowserConfiguration(**{field: "/missing"})
    setup = await module.inspect_browser(configuration)
    assert not setup.ready
    if field == "executable":
        assert setup.browser is None and setup.vnc is not None
    else:
        assert setup.vnc is None and setup.browser is not None


async def test_automatic_components_still_require_explicit_root_sandbox_opt_out(host, monkeypatch):
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    setup = await module.inspect_browser()
    assert not setup.ready and "--browser-no-sandbox" in setup.message
    assert (await module.inspect_browser(no_sandbox=True)).ready
