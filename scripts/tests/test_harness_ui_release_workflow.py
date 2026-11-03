from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize("component", ["a13n-harness", "a13n-harness-ui"])
def test_release_shell_steps_parse(component: str) -> None:
    workflow = ROOT / f".github/workflows/release-{component}.yml"
    jobs = yaml.safe_load(workflow.read_text())["jobs"]
    for job in jobs.values():
        for step in job["steps"]:
            if "run" in step:
                result = subprocess.run(["bash", "-n"], input=step["run"], capture_output=True, text=True)
                assert result.returncode == 0, f"{step['name']}: {result.stderr}"
