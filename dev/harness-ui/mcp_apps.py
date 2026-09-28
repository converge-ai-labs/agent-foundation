"""Run the MCP Apps example in a disposable production WebUI with a scripted HTTP model."""

from __future__ import annotations

import os
import sys
import tempfile
from contextlib import chdir
from pathlib import Path

import yaml

from dev.fixtures.model import model_process

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples/mcp-apps/src/mcp_apps_example"


def write_configuration(root: Path, model_url: str) -> Path:
    documents = {
        "a13n-harness-ui.yaml": {
            "schema_version": "1",
            "defaults": {"agent": "agent-counter"},
            "process": {"pricing_auto_update": False},
            "webui": {"mcp_apps": {"enabled": True, "servers": ["mcp-counter"]}},
        },
        "models/demo.yaml": {
            "schema_version": "1",
            "kind": "model",
            "id": "model-demo",
            "name": "Local scripted model",
            "route": "openai-chat:local-scripted",
            "authentication": {"kind": "api_key", "env": "MCP_APPS_DEMO_KEY"},
            "model_configuration": {"base_url": model_url},
        },
        "agents/counter.yaml": {
            "schema_version": "1",
            "kind": "agent",
            "id": "agent-counter",
            "name": "Counter demo",
            "model": "model-demo",
            "instructions": "Open the counter when asked. This demonstration uses fictional data.",
            "capabilities": [
                {
                    "capability": "ToolPermissionsCapability",
                    "configuration": {"rules": {"mcp/mcp-counter/reset_counter": "ask"}},
                }
            ],
        },
        "mcp/counter.yaml": {
            "schema_version": "1",
            "kind": "mcp_server",
            "id": "mcp-counter",
            "name": "Counter App",
            "transport": {"command": sys.executable, "arguments": [str(EXAMPLE / "server.py")]},
        },
    }
    for relative, value in documents.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return root / "a13n-harness-ui.yaml"


def main() -> None:
    if not (EXAMPLE / "assets/app.html").is_file():
        raise SystemExit("Build the example first: make mcp-apps-example-assets")
    with tempfile.TemporaryDirectory(prefix="a13n-mcp-apps-demo-") as temporary:
        root = Path(temporary)
        home, workspace = root / "home", root / "workspace"
        (home / ".codex").mkdir(parents=True)
        workspace.mkdir()
        environment = {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "CODEX_HOME": str(home / ".codex"),
            "GROK_HOME": str(home / ".grok"),
            "GROK_AUTH_PATH": str(home / ".grok/auth.json"),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local/share"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "MCP_APPS_DEMO_KEY": "local-scripted-not-a-secret",
        }
        previous = {name: os.environ.get(name) for name in (*environment, "A13N_HARNESS_UI_API_KEY")}
        os.environ.update(environment)
        # Use a fresh demo login, never the daily-use listener credential.
        os.environ.pop("A13N_HARNESS_UI_API_KEY", None)
        try:
            with model_process(port=0) as model_url:
                config = write_configuration(root / "config", model_url)
                print(f"Disposable MCP Apps demo: {root}\nSend: [mcp-app] Open the counter.", file=sys.stderr)
                with chdir(workspace):
                    from a13n_harness_ui.cli import cli

                    cli.main(
                        args=[
                            "--no-update-check",
                            "--config",
                            str(config),
                            "--data-root",
                            str(root / "data"),
                            "webui",
                            "--no-share-computer",
                            *sys.argv[1:],
                        ],
                        prog_name="a13n-harness-ui",
                    )
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


if __name__ == "__main__":
    main()
