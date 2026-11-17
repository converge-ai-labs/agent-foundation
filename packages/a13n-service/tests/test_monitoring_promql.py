"""Pinned syntax and alert-boundary checks run in ordinary Service CI, without a live stack."""

import os
import subprocess

from .monitoring_support import dashboard, prometheus_test_files


def test_all_dashboard_promql_and_alert_boundaries(tmp_path):
    prometheus_test_files(tmp_path)
    for args in [
        (
            "check",
            "rules",
            "/config/availability.yaml",
            "/config/capacity.yaml",
            "/config/execution.yaml",
            "/config/query-rules.yaml",
        ),
        ("test", "rules", "/config/rules-test.yml"),
    ]:
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--pull=missing",
                "--network=none",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--entrypoint",
                "/bin/promtool",
                "--mount",
                f"type=bind,src={tmp_path},dst=/config,readonly",
                "prom/prometheus:v3.2.1",
                *args,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr


def test_rate_panels_do_not_round_rare_faults_to_a_fixed_zero():
    rates = [
        p
        for p in dashboard("operations")["panels"]
        if p.get("fieldConfig", {}).get("defaults", {}).get("unit") in {"ops", "reqps", "tps"}
    ]
    assert rates
    assert all("decimals" not in p["fieldConfig"]["defaults"] for p in rates)
