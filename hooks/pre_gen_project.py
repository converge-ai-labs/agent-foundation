"""Validate identifiers before creating a project."""

import keyword
import re
import sys

project_slug = {{ cookiecutter.project_slug | tojson }}
module_name = {{ cookiecutter.module_name | tojson }}
app_id = {{ cookiecutter.app_id | tojson }}
version = {{ cookiecutter.version | tojson }}
service_version = {{ cookiecutter.service_version | tojson }}
project_name = {{ cookiecutter.project_name | tojson }}

stable_version = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"

checks = {
    "project_slug must contain lowercase words separated by hyphens": re.fullmatch(
        r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", project_slug
    ),
    "module_name must be a non-keyword ASCII Python identifier": (
        module_name.isascii() and module_name.isidentifier() and not keyword.iskeyword(module_name)
    ),
    "app_id must be lowercase with dot, underscore or hyphen separators": re.fullmatch(
        r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*", app_id
    ),
    "version must be a stable X.Y.Z version": re.fullmatch(stable_version, version),
    "service_version must be a stable X.Y.Z version": re.fullmatch(stable_version, service_version),
    "project_name must be nonempty and single-line": bool(project_name.strip()) and not any(
        ord(character) < 32 for character in project_name
    ),
}
for message, valid in checks.items():
    if not valid:
        print(f"ERROR: {message}", file=sys.stderr)
        sys.exit(1)
