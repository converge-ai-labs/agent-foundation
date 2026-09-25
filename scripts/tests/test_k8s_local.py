"""The chart must render valid Service deployments, and the kind launcher must keep credentials safe across reruns."""

import base64
import json
import os
import shutil
import stat
import subprocess

import pytest
import yaml

from scripts import k8s_local as k8s

CHART = k8s.ROOT / "deploy/kubernetes/helm/a13n-service"
helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def fresh():
    return k8s.credentials({}, {}, retained=False)


LOCAL = ("-f", str(k8s.ROOT / "deploy/kubernetes/helm/values-local.yaml"))
# A cluster outside kind: an S3 bucket behind an HTTPS origin.
EXTERNAL = (
    *("--set", "objects.backend=s3", "--set", "objects.bucket=agents"),
    *("--set", "publicUrl=https://agents.example.com"),
)


def render(*arguments: str) -> list[dict]:
    output = subprocess.run(
        ["helm", "template", "a13n", str(CHART), *arguments],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [document for document in yaml.safe_load_all(output) if document]


@helm
@pytest.mark.parametrize(
    "arguments,backend,public_url",
    [(LOCAL, "local", "http://127.0.0.1:8080"), (EXTERNAL, "s3", "https://agents.example.com")],
)
def test_rendered_configuration_and_generated_secret_are_valid_settings(
    arguments, backend, public_url, tmp_path, monkeypatch
):
    # Tooling CI installs only the root workspace; the full workspace validates against the real settings model.
    settings_module = pytest.importorskip("a13n_service.settings")
    crypto = pytest.importorskip("a13n_service.infra.crypto")
    config = next(d for d in render(*arguments) if d["kind"] == "ConfigMap")["data"]["service.toml"]
    path = tmp_path / "service.toml"
    path.write_text(config)
    for name in [name for name in os.environ if name.startswith("A13N_")]:
        monkeypatch.delenv(name)
    for name, value in fresh()[k8s.SERVICE_SECRET].items():
        monkeypatch.setenv(name, value)
    settings = settings_module.load_settings(path)
    crypto.KeyRing(active_key_id=settings.encryption.active_key_id, keys=settings.encryption.keys)
    assert settings.database.auto_migrate is False
    assert settings.objects.backend == backend
    assert settings.server.public_url == public_url


@helm
def test_roles_share_one_image_and_start_after_the_migration_job():
    documents = render(*LOCAL)
    deployments = {d["metadata"]["name"]: d for d in documents if d["kind"] == "Deployment"}
    job = next(d for d in documents if d["kind"] == "Job")
    service = [deployments["a13n-a13n-control"], deployments["a13n-a13n-worker"], job]
    pods = [d["spec"]["template"]["spec"] for d in service]
    assert {c["image"] for pod in pods for c in pod["containers"] + pod.get("initContainers", [])} == {
        "a13n-service:local"
    }
    assert all(pod["enableServiceLinks"] is False for pod in pods)
    assert job["spec"]["template"]["spec"]["containers"][0]["args"][-1] == "migrate"
    for role in ("control", "worker"):
        pod = deployments[f"a13n-a13n-{role}"]["spec"]["template"]["spec"]
        assert pod["containers"][0]["args"][-2:] == ["--role", role]
        assert "migrate --check" in pod["initContainers"][0]["args"][0]
    assert set(deployments) == {"a13n-a13n-control", "a13n-a13n-worker"}
    control = next(d for d in documents if d["kind"] == "Service" and d["metadata"]["name"] == "a13n-a13n-control")
    assert control["spec"]["type"] == "NodePort" and control["spec"]["ports"][0]["nodePort"] == 30080


@helm
def test_every_service_pod_serves_metrics_on_its_own_port():
    documents = render(*LOCAL)
    deployments = {d["metadata"]["name"]: d for d in documents if d["kind"] == "Deployment"}
    for role in ("control", "worker"):
        template = deployments[f"a13n-a13n-{role}"]["spec"]["template"]
        container = template["spec"]["containers"][0]
        assert {"name": "A13N_TELEMETRY__METRICS_PORT", "value": "9464"} in container["env"]
        assert {"name": "metrics", "containerPort": 9464} in container["ports"]
        assert template["metadata"]["annotations"]["prometheus.io/port"] == "9464"
    # The API Service exposes only the API.
    service = next(d for d in documents if d["kind"] == "Service" and d["metadata"]["name"] == "a13n-a13n-control")
    assert [port["name"] for port in service["spec"]["ports"]] == ["http"]
    assert not any(d["kind"] == "PodMonitor" for d in documents)


@helm
def test_service_containers_write_only_temporary_files_and_local_objects():
    for document in render(*LOCAL):
        if document["kind"] not in {"Deployment", "Job"}:
            continue
        pod = document["spec"]["template"]["spec"]
        assert {"name": "tmp", "emptyDir": {}} in pod["volumes"]
        for container in pod["containers"] + pod.get("initContainers", []):
            assert container["securityContext"]["readOnlyRootFilesystem"] is True
            writable = {m["mountPath"] for m in container["volumeMounts"] if not m.get("readOnly")}
            assert writable == {"/tmp", "/app/var/objects"}


@helm
def test_each_role_tolerates_one_disruption_and_autoscales_only_when_enabled():
    def objects(*arguments: str) -> dict[tuple[str, str], dict]:
        return {(d["kind"], d["metadata"]["name"]): d for d in render(*EXTERNAL, *arguments)}

    fixed = objects()
    autoscaled = objects("--set", "roles.worker.autoscaling.enabled=true")
    for role in ("control", "worker"):
        name = f"a13n-a13n-{role}"
        budget = fixed["PodDisruptionBudget", name]["spec"]
        assert budget["maxUnavailable"] == 1
        assert budget["selector"] == fixed["Deployment", name]["spec"]["selector"]
    assert [name for kind, name in fixed if kind == "HorizontalPodAutoscaler"] == []
    assert [name for kind, name in autoscaled if kind == "HorizontalPodAutoscaler"] == ["a13n-a13n-worker"]
    target = autoscaled["HorizontalPodAutoscaler", "a13n-a13n-worker"]["spec"]["scaleTargetRef"]
    assert target == {"apiVersion": "apps/v1", "kind": "Deployment", "name": "a13n-a13n-worker"}
    # The autoscaler owns the worker count; a fixed count would reset it on every upgrade.
    assert "replicas" not in autoscaled["Deployment", "a13n-a13n-worker"]["spec"]
    assert autoscaled["Deployment", "a13n-a13n-control"]["spec"]["replicas"] == 1


@helm
def test_s3_objects_require_a_bucket():
    result = subprocess.run(
        ["helm", "template", "a13n", str(CHART), "--set", "objects.backend=s3"], capture_output=True, text=True
    )
    assert result.returncode != 0 and "objects.bucket is required" in result.stderr


def test_first_start_generates_random_credentials():
    first, second = fresh(), fresh()
    service = first[k8s.SERVICE_SECRET]
    keys = json.loads(service["A13N_ENCRYPTION__KEYS"])
    assert len(base64.b64decode(keys[service["A13N_ENCRYPTION__ACTIVE_KEY_ID"]], validate=True)) == 32
    assert first != second


@pytest.mark.parametrize("source", ["local", "remote", "both"])
def test_restart_reuses_credentials(source):
    original = fresh()
    local = original if source in {"local", "both"} else {}
    remote = original if source in {"remote", "both"} else {}
    assert k8s.credentials(local, remote, retained=True) == original


def test_conflicting_credentials_do_not_overwrite_cluster():
    with pytest.raises(ValueError, match="differ"):
        k8s.credentials(fresh(), fresh(), retained=True)


def test_existing_data_never_gets_new_credentials():
    with pytest.raises(ValueError, match="Existing PVCs"):
        k8s.credentials({}, {}, retained=True)


def test_partial_credentials_never_generate_replacement_key():
    values = fresh()
    del values[k8s.SERVICE_SECRET]
    with pytest.raises(ValueError, match="Incomplete"):
        k8s.credentials(values, {}, retained=False)


@pytest.mark.parametrize(
    "keys,active",
    [
        ('{"primary":"REPLACE_BASE64_32_BYTE_KEY"}', "primary"),
        ('{"primary":"' + base64.b64encode(b"short").decode() + '"}', "primary"),
        ('{"other":"' + base64.b64encode(bytes(32)).decode() + '"}', "primary"),
        ("not-json", "primary"),
        ('["primary"]', "primary"),
    ],
)
def test_invalid_key_ring_is_rejected_without_echoing_it(keys, active):
    values = fresh()
    values[k8s.SERVICE_SECRET]["A13N_ENCRYPTION__KEYS"] = keys
    values[k8s.SERVICE_SECRET]["A13N_ENCRYPTION__ACTIVE_KEY_ID"] = active
    with pytest.raises(ValueError) as caught:
        k8s.credentials(values, {}, retained=False)
    assert keys not in str(caught.value)


def test_database_password_mismatch_is_rejected():
    values = fresh()
    values[k8s.POSTGRES_SECRET]["POSTGRES_PASSWORD"] = "different"
    with pytest.raises(ValueError, match="matching local database"):
        k8s.credentials(values, {}, retained=True)


def test_administrator_is_generated_once_for_fresh_data(tmp_path):
    admin = k8s.administrator(tmp_path, "owner@example.com", retained=False)
    assert admin is not None and admin["EMAIL"] == "owner@example.com" and len(admin["PASSWORD"]) >= 12
    assert stat.S_IMODE((tmp_path / k8s.ADMIN_FILE).stat().st_mode) == 0o600
    assert k8s.administrator(tmp_path, "different@example.com", retained=True) == admin


def test_existing_data_without_administrator_record_is_not_bootstrapped(tmp_path):
    assert k8s.administrator(tmp_path, "owner@example.com", retained=True) is None
    with pytest.raises(ValueError, match="email"):
        k8s.administrator(tmp_path, "not an email", retained=False)


@pytest.mark.parametrize("failed_secret", list(k8s.SECRET_FILES))
def test_secret_creation_retry_preserves_files_and_recovers_partial_cluster(tmp_path, monkeypatch, failed_secret):
    remote = {}
    fail = True

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
        if name == failed_secret and fail:
            raise RuntimeError("simulated interruption")
        assert name not in remote
        remote[name] = manifest["stringData"]
        return ""

    monkeypatch.setattr(k8s, "kubectl", kubectl)
    with pytest.raises(RuntimeError, match="interruption"):
        k8s.prepare(tmp_path)
    names = [*k8s.SECRET_FILES.values(), k8s.ADMIN_FILE]
    files = {name: (tmp_path / name).read_bytes() for name in names}
    fail = False
    first = k8s.prepare(tmp_path)
    assert k8s.prepare(tmp_path) == first
    assert len(remote) == 2
    for name, original in files.items():
        assert (tmp_path / name).read_bytes() == original
        assert stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "returncode,stderr,expected",
    [(0, "", True), (3, "Bootstrap refused: the Service is already initialized", False)],
)
def test_bootstrap_reports_creation_or_existing_initialization(monkeypatch, returncode, stderr, expected):
    calls = []

    def fake(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, returncode, "", stderr)

    monkeypatch.setattr(k8s.subprocess, "run", fake)
    assert k8s.bootstrap({"EMAIL": "owner@example.com", "PASSWORD": "secret-password"}) is expected
    args, kwargs = calls[0]
    assert "secret-password" not in args and kwargs["input"] == "secret-password\n"
    assert args[:3] == ["kubectl", "--context", k8s.CONTEXT]
    assert args[-3:] == ["--email", "owner@example.com", "--password-stdin"]


def test_bootstrap_failure_does_not_disclose_output(monkeypatch):
    monkeypatch.setattr(
        k8s.subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 1, "secret", "secret")
    )
    with pytest.raises(RuntimeError) as caught:
        k8s.bootstrap({"EMAIL": "owner@example.com", "PASSWORD": "secret"})
    assert "secret" not in str(caught.value)


def test_failed_captured_command_does_not_disclose_secrets(monkeypatch):
    monkeypatch.setattr(k8s.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 1, "secret", "secret"))
    with pytest.raises(RuntimeError) as caught:
        k8s.run("kubectl", "create", capture=True, input_text="secret")
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("existing", [False, True])
def test_startup_builds_loads_and_waits_before_bootstrapping(tmp_path, monkeypatch, capsys, existing):
    commands = []

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
        return ""

    admin = {"EMAIL": "owner@example.com", "PASSWORD": "secret-password"}
    monkeypatch.setattr(k8s, "run", run)
    monkeypatch.setattr(k8s.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(k8s, "prepare", lambda path: commands.append(("prepare",)) or admin)
    monkeypatch.setattr(k8s, "bootstrap", lambda selected: commands.append(("bootstrap", selected["EMAIL"])) or True)
    k8s.start(tmp_path)
    builds = [c for c in commands if c[:2] == ("docker", "build")]
    load = next(c for c in commands if c[:3] == ("kind", "load", "docker-image"))
    helm = next(c for c in commands if c[0] == "helm")
    assert len(builds) == 1
    image = builds[0][builds[0].index("-t") + 1]
    assert image in load
    assert "--wait" in helm and "--kube-context" in helm and k8s.NAMESPACE in helm
    assert f"image.tag={image.split(':')[1]}" in helm
    order = [commands.index(c) for c in (("prepare",), builds[0], load, helm, ("bootstrap", "owner@example.com"))]
    assert order == sorted(order)
    assert any(c[:3] == ("kind", "create", "cluster") for c in commands) != existing
    output = capsys.readouterr().out
    assert "owner@example.com" in output and "secret-password" not in output


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


@helm
def test_a_packaged_chart_deploys_its_service_release(tmp_path):
    """A source chart deploys the development image; a release packages the chart at its Service version."""

    def images(chart: str) -> set[str]:
        output = subprocess.run(["helm", "template", "a13n", chart], capture_output=True, text=True, check=True).stdout
        pods = [
            d["spec"]["template"]["spec"]
            for d in yaml.safe_load_all(output)
            if d and d["kind"] in {"Deployment", "Job"}
        ]
        return {c["image"] for pod in pods for c in pod["containers"] if c["name"] not in {"redis", "postgresql"}}

    assert images(str(CHART)) == {"ghcr.io/converge-ai-labs/a13n-service:dev"}
    version = "1.2.3-rc.1"
    subprocess.run(
        ["helm", "package", str(CHART), "--version", version, "--app-version", version, "--destination", str(tmp_path)],
        capture_output=True,
        check=True,
    )
    assert images(str(tmp_path / f"a13n-service-{version}.tgz")) == {f"ghcr.io/converge-ai-labs/a13n-service:{version}"}
