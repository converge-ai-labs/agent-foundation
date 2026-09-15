"""Verify the manual suite selections, opt-in gates and account isolation without live I/O."""

import argparse
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from .. import ci
from ..conftest import pytest_collection_modifyitems


@pytest.fixture(scope="module")
def collected_suites():
    selections = {}
    for suite in ci.SUITES:
        result = subprocess.run(
            [sys.executable, "-m", "dev.live_tests.ci", suite, "--collect-only"],
            cwd=ci.REPOSITORY,
            env={
                **os.environ,
                "LIVE_TEST_PROVIDERS_CONFIG": "/ci-must-not-read-private-provider-settings.toml",
                "PYTEST_ADDOPTS": "--live-providers -k configured_model",
            },
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        selections[suite] = {
            line for line in result.stdout.splitlines() if line.startswith("dev/live_tests/") and "::" in line
        }
        assert selections[suite], result.stdout
    return selections


def test_reviewed_ci_journeys_collect_once_without_external_or_stress_cases(collected_suites):
    seen = set()
    for suite, cases in collected_suites.items():
        if suite == "smoke":
            continue
        assert not (seen & cases), f"Repeated cases in {suite}: {seen & cases}"
        seen.update(cases)
        assert not any(
            token in case
            for case in cases
            for token in ("/model/", "/providers/", "/performance/", "/infrastructure_tests/", "e2b")
        )
    assert len(collected_suites["core"]) == 21
    assert len(collected_suites["functional"]) == 35
    assert len(seen) == 398
    assert len(collected_suites["smoke"]) == 34
    assert collected_suites["smoke"] <= seen
    assert not any("test_06_steer" in case for case in collected_suites["smoke"])
    files = {case.split("::")[0] for case in seen}
    root = ci.TEST_ROOT
    # All requested control/fault files and every non-cloud Environment module
    # must be represented, including both historical case-54 filenames.
    requested = [
        *root.glob("control/test_4[56789]_*.py"),
        *root.glob("control/test_5[0123456]_*.py"),
        *root.glob("control/test_38_*.py"),
        *root.glob("control/test_40_*.py"),
        *root.glob("run_recovery/test_3[79]_*.py"),
        *root.glob("run_recovery/test_42_*.py"),
        *root.glob("iam/test_41_*.py"),
        *(path for path in root.glob("environment/test_*.py") if "e2b" not in path.name),
    ]
    assert {str(path.relative_to(ci.REPOSITORY)) for path in requested} <= files
    assert not any("test_10_worker_recovery" in case or "test_12_worker_competition" in case for case in seen)


def test_narrowing_environment_selection_cannot_enable_e2b():
    result = subprocess.run(
        [sys.executable, "-m", "dev.live_tests.ci", "environment-service", "--collect-only", "-k", "e2b"],
        cwd=ci.REPOSITORY,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == pytest.ExitCode.NO_TESTS_COLLECTED, result.stdout + result.stderr


def test_external_smoke_collection_never_reads_infrastructure_or_starts_docker():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "dev.live_tests.ci",
            "smoke",
            "--collect-only",
            "--infrastructure=external",
            "--infrastructure-config=/must-not-read-infrastructure.toml",
        ],
        cwd=ci.REPOSITORY,
        env={**os.environ, "DOCKER_HOST": "unix:///must-not-use-docker.sock"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "34 tests collected" in result.stdout


def test_smoke_groups_partition_all_cases_without_overlap(collected_suites):
    seen = set()
    for group, expected in (("core", 20), ("round-two", 4), ("management", 10)):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "dev.live_tests.ci",
                "smoke",
                f"--smoke-group={group}",
                "--collect-only",
                "--infrastructure=external",
                "--infrastructure-config=/must-not-read-infrastructure.toml",
            ],
            cwd=ci.REPOSITORY,
            env={**os.environ, "DOCKER_HOST": "unix:///must-not-use-docker.sock"},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        cases = {line for line in result.stdout.splitlines() if line.startswith("dev/live_tests/") and "::" in line}
        assert len(cases) == expected
        assert not seen & cases
        seen.update(cases)
    assert seen == collected_suites["smoke"]


def test_smoke_group_cannot_silently_replace_another_suite(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("An invalid group must not invoke pytest")

    monkeypatch.setattr(ci.subprocess, "call", forbidden)
    with pytest.raises(SystemExit) as caught:
        ci.main(["core", "--smoke-group=core"])
    assert caught.value.code == 2


@pytest.mark.parametrize("workers,collect", [(1, False), (2, False), (2, True)])
def test_core_parallelism_keeps_collection_offline_and_uses_case_scheduling(workers, collect):
    options = ci.parser().parse_args(
        ["smoke", "--smoke-group=core", f"--workers={workers}", *(["--collect-only"] if collect else [])]
    )
    arguments = ci.pytest_arguments(options)
    parallel = workers > 1 and not collect
    assert arguments[arguments.index("-n") + 1] == (str(workers) if parallel else "0")
    assert ("--dist=load" in arguments) == parallel
    assert ("--max-worker-restart=0" in arguments) == parallel


@pytest.mark.parametrize("selection", [["core"], ["smoke"], ["smoke", "--smoke-group=management"]])
def test_parallelism_cannot_enable_non_core_shared_labs(monkeypatch, selection):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid parallel selection must not invoke pytest")

    monkeypatch.setattr(ci.subprocess, "call", forbidden)
    with pytest.raises(SystemExit) as caught:
        ci.main([*selection, "--workers=2"])
    assert caught.value.code == 2


@pytest.mark.parametrize("worker", [False, True])
@pytest.mark.parametrize("group", ["core", "round-two", "management"])
def test_distributed_collection_checks_core_allowlist_in_controller_and_workers(worker, group):
    config = SimpleNamespace(option=SimpleNamespace(numprocesses=0 if worker else 2), getoption=lambda name: True)
    if worker:
        config.workerinput = {"workerid": "gw0"}
    selection = ci.SMOKE_GROUPS[group][0]
    path, name = selection.split("::")
    item = SimpleNamespace(path=ci.TEST_ROOT / path, originalname=name, nodeid=selection)
    if group == "core":
        pytest_collection_modifyitems(config, [item])
    else:
        with pytest.raises(pytest.UsageError, match="only reviewed Core"):
            pytest_collection_modifyitems(config, [item])


def test_shared_lab_opt_in_rejects_unreviewed_fault_cases():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "dev/live_tests/run_recovery/test_39_run_budgets_and_drain.py",
            "--live-shared-labs",
            "--collect-only",
            "-q",
        ],
        cwd=ci.REPOSITORY,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == pytest.ExitCode.USAGE_ERROR
    assert "unreviewed journey" in result.stderr


def test_ci_environment_drops_ambient_accounts_and_pytest_selection(monkeypatch):
    for key in (
        "A13N_SERVICE_DATABASE_URL",
        "A13N_SERVICE_OBJECT_ENDPOINT_URL",
        "AWS_PROFILE",
        "AWS_ACCESS_KEY_ID",
        "LIVE_TEST_CONFIG",
        "LIVE_TEST_PROVIDERS_CONFIG",
        "LIVE_TEST_MODEL_PRIVATE_ENDPOINT_DOMAINS",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "PYTEST_ADDOPTS",
        "PYTEST_PLUGINS",
    ):
        monkeypatch.setenv(key, "must-not-be-used")
    monkeypatch.setenv("LIVE_TEST_SANDBOX_IMAGE", "a13n-sandbox:ci")
    monkeypatch.setenv("A13N_ENVD_TEST_BINARY", "/owned/a13n-envd")
    environment = ci.clean_environment()
    assert "must-not-be-used" not in environment.values()
    assert environment["LIVE_TEST_SANDBOX_IMAGE"] == "a13n-sandbox:ci"
    assert environment["A13N_ENVD_TEST_BINARY"] == "/owned/a13n-envd"


def test_ci_collect_only_never_starts_the_core_lab_and_preserves_failure(monkeypatch):
    from .. import isolated

    async def forbidden(*args, **kwargs):
        pytest.fail("Collection must not start a lab")

    observed = []

    def collect(command, **kwargs):
        observed.append(command)
        return 2

    monkeypatch.setattr(isolated, "run", forbidden)
    monkeypatch.setattr(ci.subprocess, "call", collect)
    assert ci.main(["core", "--collect-only"]) == 2
    assert "--collect-only" in observed[0]


@pytest.mark.parametrize("value", ["0/3", "4/3", "1/0", "1", "one/three", "1/2/3"])
def test_invalid_shards_fail_before_starting_infrastructure(value):
    with pytest.raises(argparse.ArgumentTypeError, match="one-based"):
        ci.parse_shard(value)


def test_empty_shard_fails_before_invoking_pytest(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("An empty shard must not invoke pytest")

    monkeypatch.setattr(ci.subprocess, "call", forbidden)
    with pytest.raises(SystemExit) as caught:
        ci.main(["core", "--shard=10/10", "--collect-only"])
    assert caught.value.code == 2


def test_ci_creates_missing_basetemp_parent_for_real_pytest(tmp_path, monkeypatch):
    test = tmp_path / "test_native_temp.py"
    test.write_text("def test_workspace(tmp_path):\n    (tmp_path / 'native-proof').write_text('ready')\n")
    monkeypatch.setitem(ci.SUITES, "environment-native", ci.Suite((str(test),), ()))
    basetemp = tmp_path / "runner" / "live-pytest" / "native-1"
    assert not basetemp.parent.exists()
    assert ci.main(["environment-native", f"--basetemp={basetemp}"]) == 0
    assert list(basetemp.rglob("native-proof"))
