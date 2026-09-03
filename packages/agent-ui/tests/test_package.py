import json
import subprocess
import sys
from importlib.metadata import version

from a13n_ui import __version__


def test_package_exposes_distribution_version() -> None:
    assert __version__ == version("a13n-ui")


def test_cli_import_defers_heavy_document_converters() -> None:
    modules = ("mammoth", "openpyxl", "pptx", "pypdf", "markdownify")
    source = (
        "import json, sys; import a13n_ui.cli; "
        f"print(json.dumps({modules!r} and [name in sys.modules for name in {modules!r}]))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", source],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == [False] * len(modules)
