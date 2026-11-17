"""Locate this project's wheel from its current package metadata."""

import tomllib
from pathlib import Path

from packaging.utils import canonicalize_name
from packaging.version import Version

project = tomllib.loads(Path("pyproject.toml").read_text())["project"]
name = canonicalize_name(project["name"]).replace("-", "_")
version = Version(project["version"])
print(f"dist/{name}-{version}-py3-none-any.whl")
