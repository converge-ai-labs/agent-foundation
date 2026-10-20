"""JSON and library logging never load the terminal renderer."""

import subprocess
import sys


def test_rich_is_loaded_only_when_pretty_stdout_is_selected() -> None:
    script = """
import sys
from a13n_logging import LogFormat, configure_logging, get_logger
get_logger('a13n.test')
assert 'rich' not in sys.modules
configure_logging(log_format=LogFormat.json, logger_names=['a13n.test'])
assert 'rich' not in sys.modules
configure_logging(log_format=LogFormat.pretty, logger_names=['a13n.test'])
assert 'rich.logging' in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
