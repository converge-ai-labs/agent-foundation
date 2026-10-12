"""Export the Service HTTP contract and session stream frames without opening process resources."""

import argparse
import json
from pathlib import Path

from a13n_service.app import build_app
from a13n_service.runs.stream import frames_schema

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "proto/a13n-service"


def documents() -> dict[str, dict]:
    return {"openapi.json": build_app().openapi(), "session-stream.schema.json": frames_schema()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed = []
    for name, schema in documents().items():
        target = TARGET / name
        if args.check:
            try:
                current = json.loads(target.read_text())
            except (OSError, ValueError):
                current = None
            if current != schema:
                changed.append(name)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    if changed:
        parser.exit(1, f"Service contract changed: {', '.join(changed)}. Run make service-contract-generate.\n")


if __name__ == "__main__":
    main()
