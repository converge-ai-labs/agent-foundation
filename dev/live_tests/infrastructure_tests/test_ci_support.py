"""Verify the manual suite selections, opt-in gates and account isolation without live I/O."""

import argparse
import os
import subprocess
import sys

import pytest

from .. import ci


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
        assert not (seen & cases), f"Repeated cases in {suite}: {seen & cases}"
        seen.update(cases)
        assert not any(
            token in case
            for case in cases
            for token in ("/model/", "/providers/", "/performance/", "/infrastructure_tests/", "e2b")
        )
    assert len(collected_suites["core"]) == 21
    assert len(collected_suites["functional"]) == 69
    assert len(seen) == 432
    files = {case.split("::")[0] for case in seen}
    root = ci.TEST_ROOT
    # All requested control/fault files and every non-cloud Environment module
    # must be represented, including both historical case-54 filenames.
    requested = [
        *root.glob("skills/test_*.py"),
        root / "harness_integration/test_24_skill_management.py",
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
