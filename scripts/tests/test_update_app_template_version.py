import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "update-app-template-version.py"


@pytest.mark.parametrize(
    ("original", "candidate", "expected", "status"),
    [
        ('"service_version": "0.2.9"', "0.2.10", '"service_version": "0.2.10"', 0),
        ('"service_version": "0.2.9"', "0.2.9", '"service_version": "0.2.9"', 0),
        ('"service_version": "0.2.10"', "0.2.9", '"service_version": "0.2.10"', 0),
        ('"service_version": "0.2.9"', "0.3.0-rc.1", '"service_version": "0.2.9"', 2),
        ('"service_ref": "old-source-pin"', "0.3.0", '"service_ref": "old-source-pin"', 0),
    ],
)
def test_update_only_advances_stable_default(tmp_path, original, candidate, expected, status):
    path = tmp_path / "cookiecutter.json"
    prefix = '{\n  "project_name": "unchanged",\n  '
    suffix = ',\n  "_copy_without_render": [".github/workflows/*", "frontend/pnpm-lock.yaml"]\n}\n'
    path.write_text(prefix + original + suffix)
    result = subprocess.run([sys.executable, SCRIPT, path, candidate], check=False)
    assert result.returncode == status
    assert path.read_text() == prefix + expected + suffix
