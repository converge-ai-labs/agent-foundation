"""Export the browser contract without opening an App, store, or listener."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from a13n_ui.app import open_agent_ui_app
from a13n_ui.settings import AgentUiSettings
from a13n_ui.webui import create_webui, openapi_document


def main() -> None:
    server = create_webui(lambda: open_agent_ui_app(AgentUiSettings()), api_key="schema-export-not-a-listener")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).resolve().parents[1] / "apps/harness-ui/src/openapi.json"
    )
    target = parser.parse_args().output
    target.write_text(json.dumps(openapi_document(server), indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
