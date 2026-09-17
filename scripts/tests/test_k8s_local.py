"""Credential persistence and bootstrap output must be safe across local reruns."""

import base64
import json
import stat
import subprocess

import pytest

from scripts import k8s_local as k8s


def fresh():
    return k8s.credentials({}, {}, retained=False, email="owner@example.com")


def test_first_start_generates_random_credentials():
    first, second = fresh(), fresh()
    service = first["a13n-service-secrets"]
    assert len(base64.b64decode(service[k8s.MASTER_KEY], validate=True)) == 32
    assert service["A13N_SERVICE_IAM_INITIAL_ADMIN_EMAIL"] == "owner@example.com"
    assert first != second


@pytest.mark.parametrize("source", ["local", "remote", "both"])
def test_restart_reuses_credentials_and_email(source):
    original = fresh()
    local = original if source in {"local", "both"} else {}
    remote = original if source in {"remote", "both"} else {}
    assert k8s.credentials(local, remote, retained=True, email="different@example.com") == original


def test_conflicting_credentials_do_not_overwrite_cluster():
    with pytest.raises(ValueError, match="differ"):
        k8s.credentials(fresh(), fresh(), retained=True, email="owner@example.com")


def test_existing_data_never_gets_new_credentials():
    with pytest.raises(ValueError, match="Existing PVCs"):
        k8s.credentials({}, {}, retained=True, email="owner@example.com")


def test_partial_credentials_never_generate_replacement_key():
    values = fresh()
    del values["a13n-service-secrets"]
    with pytest.raises(ValueError, match="Incomplete"):
        k8s.credentials(values, {}, retained=False, email="owner@example.com")


@pytest.mark.parametrize("key", ["REPLACE_BASE64_32_BYTE_KEY", "", base64.b64encode(b"short").decode()])
def test_invalid_master_key_is_rejected_without_echoing_it(key):
    values = fresh()
    values["a13n-service-secrets"][k8s.MASTER_KEY] = key
    with pytest.raises(ValueError) as caught:
        k8s.credentials(values, {}, retained=False, email="owner@example.com")
    if key:
        assert key not in str(caught.value)


def test_database_password_mismatch_is_rejected():
    values = fresh()
    values["a13n-postgres-secrets"]["POSTGRES_PASSWORD"] = "different"
    with pytest.raises(ValueError, match="matching local database"):
        k8s.credentials(values, {}, retained=True, email="owner@example.com")


@pytest.mark.parametrize("failed_secret", list(k8s.SECRET_FILES))
def test_secret_creation_retry_preserves_files_and_recovers_partial_cluster(tmp_path, monkeypatch, failed_secret):
    remote = {}
    fail_postgres = True

    def kubectl(*args, capture=False, input_text=None):
        if args[:2] == ("get", "secret"):
            values = remote.get(args[2])
            return (
                json.dumps({"data": {k: base64.b64encode(v.encode()).decode() for k, v in values.items()}})
                if values
                else ""
            )
        if args[:2] == ("get", "pvc"):
            return json.dumps({"items": []})
        assert args == ("create", "-f", "-")
        manifest = json.loads(input_text)
        name = manifest["metadata"]["name"]
        if name == failed_secret and fail_postgres:
            raise RuntimeError("simulated interruption")
        assert name not in remote
        remote[name] = manifest["stringData"]
        return ""

    monkeypatch.setattr(k8s, "kubectl", kubectl)
    with pytest.raises(RuntimeError, match="interruption"):
        k8s.prepare_secrets(tmp_path)
    files = {name: (tmp_path / name).read_bytes() for name in k8s.SECRET_FILES.values()}
    fail_postgres = False
    k8s.prepare_secrets(tmp_path)
    k8s.prepare_secrets(tmp_path)
    assert len(remote) == 2
    for name, original in files.items():
        assert (tmp_path / name).read_bytes() == original
        assert stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o600


def test_bootstrap_link_only_extracts_expected_local_invitation():
    link = k8s.ORIGIN + "/invitations/inv_test/accept#token=secret"
    logs = "noise\n" + json.dumps({"message": "Administrator initialization link (single use): " + link})
    assert k8s.bootstrap_link(logs) == link
    assert k8s.bootstrap_link(logs.replace(k8s.ORIGIN, "https://unexpected.example")) is None
    assert k8s.bootstrap_link(json.dumps({"message": "service_started"})) is None


def test_failed_captured_command_does_not_disclose_secrets(monkeypatch):
    monkeypatch.setattr(k8s.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 1, "secret", "secret"))
    with pytest.raises(RuntimeError) as caught:
        k8s.run("kubectl", "create", capture=True, input_text="secret")
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("existing", [False, True])
def test_startup_builds_loads_and_waits_before_displaying_invitation(tmp_path, monkeypatch, capsys, existing):
    commands = []
    link = k8s.ORIGIN + "/invitations/inv_test/accept#token=test"

    def run(*args, **kwargs):
        commands.append(args)
        if args[:3] == ("kind", "get", "clusters"):
            return "a13n-local\n" if existing else ""
        if args[:3] == ("kind", "get", "nodes"):
            return "a13n-local-control-plane\n"
        if args[:2] == ("docker", "inspect"):
            return json.dumps(
                [{"HostConfig": {"PortBindings": {"30080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8080"}]}}}]
            )
        if "logs" in args:
            return json.dumps({"message": "Administrator initialization link (single use): " + link})
        return ""

    monkeypatch.setattr(k8s, "run", run)
    monkeypatch.setattr(k8s.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(k8s, "prepare_secrets", lambda path: commands.append(("prepare-secrets",)))
    k8s.start(tmp_path)
    builds = [c for c in commands if c[:2] == ("docker", "build")]
    load = next(c for c in commands if c[:3] == ("kind", "load", "docker-image"))
    helm = next(c for c in commands if c[0] == "helm")
    assert len(builds) == 2
    images = [c[c.index("-t") + 1] for c in builds]
    assert all(image in load for image in images)
    assert "--wait" in helm and "--kube-context" in helm
    assert f"image.tag={images[0].split(':')[1]}" in helm
    assert f"console.image.tag={images[1].split(':')[1]}" in helm
    assert (
        commands.index(("prepare-secrets",)) < commands.index(builds[0]) < commands.index(load) < commands.index(helm)
    )
    assert any(c[:3] == ("kind", "create", "cluster") for c in commands) != existing
    assert link in capsys.readouterr().out


def test_wrong_port_mapping_stops_before_mutation(tmp_path, monkeypatch):
    commands = []

    def run(*args, **kwargs):
        commands.append(args)
        if args[:3] == ("kind", "get", "clusters"):
            return "a13n-local\n"
        if args[:3] == ("kind", "get", "nodes"):
            return "a13n-local-control-plane\n"
        if args[:2] == ("docker", "inspect"):
            return json.dumps([{"HostConfig": {"PortBindings": {}}}])
        return ""

    monkeypatch.setattr(k8s, "run", run)
    monkeypatch.setattr(k8s.shutil, "which", lambda name: "/bin/" + name)
    with pytest.raises(ValueError, match="mapping"):
        k8s.start(tmp_path)
    assert all(c[0] in {"docker", "kind"} for c in commands)
    assert not any("create" in c or "delete" in c or "build" in c for c in commands)
