"""Export the Native HTTP and existing streaming wire models without opening process resources."""

import argparse
import json
from pathlib import Path

from a13n_service.app import create_app
from a13n_service.gateway.router import ClientFrame
from a13n_service.run_stream.domain import RunStreamEvent
from a13n_service.settings import Settings
from pydantic import TypeAdapter

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "proto/a13n-service"


def documents() -> dict[str, dict]:
    schema = create_app(Settings()).openapi()
    schema["paths"] = {path: value for path, value in schema["paths"].items() if path.startswith("/api/v1/")}
    schema["info"]["version"] = "1"
    return {
        "openapi.json": schema,
        "notification-client.schema.json": TypeAdapter(ClientFrame).json_schema(),
        "run-stream-event.schema.json": RunStreamEvent.model_json_schema(),
    }


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
