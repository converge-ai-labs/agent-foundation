from pathlib import PurePosixPath, PureWindowsPath

import pytest
from a13n_harness.providers.environment.local_envd.configuration import local_envd_path


@pytest.mark.parametrize(
    "native,expected",
    [
        (PurePosixPath("/work/project"), "/work/project"),
        (PureWindowsPath("C:/work/project"), "/C:/work/project"),
        (PureWindowsPath("C:/"), "/C:/"),
        (PureWindowsPath("//server/share/project"), "/UNC/server/share/project"),
        (PureWindowsPath("//server/share/"), "/UNC/server/share/"),
        (PureWindowsPath("//?/C:/work/project"), "/C:/work/project"),
        (PureWindowsPath("//?/UNC/server/share/project"), "/UNC/server/share/project"),
    ],
)
def test_local_envd_path_preserves_device_namespace(native, expected):
    assert local_envd_path(native) == expected


def test_local_envd_path_rejects_relative_paths():
    with pytest.raises(ValueError, match="absolute"):
        local_envd_path(PureWindowsPath("C:relative"))


@pytest.mark.parametrize(
    "device,native",
    [
        ("/C:/work/project", PureWindowsPath("C:/work/project")),
        ("/C:/", PureWindowsPath("C:/")),
        ("/UNC/server/share/", PureWindowsPath("//server/share/")),
        ("/UNC/server/share/project", PureWindowsPath("//server/share/project")),
    ],
)
def test_local_envd_native_path_decodes_windows_namespace(monkeypatch, device, native):
    from a13n_harness.providers.environment.local_envd import configuration

    monkeypatch.setattr(configuration, "Path", PureWindowsPath)
    assert configuration.local_envd_native_path(device) == native


def test_restricted_windows_daemon_environment_keeps_only_runtime_inputs(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from a13n_envd_client.eip.v1 import RestrictedSandbox
    from a13n_harness.providers.environment.local_envd import _daemon
    from a13n_harness.providers.environment.local_envd.configuration import LocalEnvdLaunchConfiguration

    monkeypatch.setattr(_daemon, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setenv("SystemRoot", "C:/Windows")
    monkeypatch.setenv("PRIVATE_TOKEN", "never-copy")
    configuration = LocalEnvdLaunchConfiguration(
        sandbox=RestrictedSandbox(mode="restricted", grants=()),
        trusted_executable_roots=(tmp_path,),
    )
    environment = _daemon._daemon_environment(
        device_id="device-test", runtime_dir=tmp_path, configuration=configuration
    )
    # Windows normalizes os.environ keys to uppercase; POSIX preserves spelling.
    assert {name.upper(): value for name, value in environment.items()}["SYSTEMROOT"] == "C:/Windows"
    assert environment["PATH"] == str(tmp_path)
    assert environment["TEMP"] == str(tmp_path)
    assert "PRIVATE_TOKEN" not in environment
    assert "USERPROFILE" not in environment
