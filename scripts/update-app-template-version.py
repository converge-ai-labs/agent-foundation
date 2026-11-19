"""Advance the Cookiecutter default after a successful stable Service release."""

import argparse
import json
import re
from pathlib import Path


def update(path: Path, version: str) -> bool:
    text = path.read_text()
    current = json.loads(text).get("service_version")
    # Older template revisions still use service_ref. Leave those projects untouched.
    if current is None:
        print("App template has not migrated to service_version yet.")
        return False
    if tuple(map(int, version.split("."))) <= tuple(map(int, current.split("."))):
        return False
    field = re.compile(r'^(  "service_version": )"' + re.escape(current) + r'"(?=,?$)', re.MULTILINE)
    updated, count = field.subn(lambda match: match[1] + json.dumps(version), text)
    if count != 1:
        raise ValueError("Expected one service_version default in cookiecutter.json")
    path.write_text(updated)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("version")
    args = parser.parse_args()
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", args.version):
        parser.error("expected a stable X.Y.Z Service version")
    update(args.path, args.version)


if __name__ == "__main__":
    main()
