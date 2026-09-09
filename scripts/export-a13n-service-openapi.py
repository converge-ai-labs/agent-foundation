"""Export the actual Native routes without opening process resources."""

import argparse
import json
from pathlib import Path

from a13n_service.app import create_app
from a13n_service.gateway.notifications import NotificationSubscription
from a13n_service.run_stream.domain import RunStreamEvent
from a13n_service.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = Path(__file__).resolve().parents[1] / "sdk/typescript/openapi.json"
    schema = create_app(Settings()).openapi()
    schema["paths"] = {path: value for path, value in schema["paths"].items() if path.startswith("/api/v1/")}
    for model in (RunStreamEvent, NotificationSubscription):
        model_schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        definitions = model_schema.pop("$defs", {})
        schema["components"]["schemas"].update(definitions)
        schema["components"]["schemas"][model.__name__] = model_schema
    schema["info"]["version"] = "1"
    content = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    if args.check:
        if not target.exists() or json.loads(target.read_text()) != schema:
            parser.exit(1, "Service OpenAPI changed. Run make sdk-typescript-generate.\n")
    else:
        target.write_text(content)


if __name__ == "__main__":
    main()
