"""Exercise the real HarnessUiApp and native HTTP model path with fictional data."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import yaml
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.surfaces import RootOperationStatus

from dev.service.model import model_process

ROOT = Path(__file__).resolve().parents[2]


def write_configuration(root: Path, model_url: str) -> Path:
    documents = {
        "a13n-harness-ui.yaml": {
            "schema_version": "1",
            "defaults": {"agent": "agent-demo"},
            "process": {"pricing_auto_update": False},
        },
        "models/demo.yaml": {
            "schema_version": "1",
            "kind": "model",
            "id": "model-demo",
            "name": "Local scripted model",
            "route": "openai-chat:local-scripted",
            "authentication": {"kind": "api_key", "env": "HARNESS_UI_SMOKE_KEY"},
            "model_configuration": {"base_url": model_url},
        },
        "agents/demo.yaml": {
            "schema_version": "1",
            "kind": "agent",
            "id": "agent-demo",
            "name": "Observation demo",
            "model": "model-demo",
            "instructions": "Exercise the fictional local development workflow.",
            "subagents": [{"markdown": "subagent-reviewer"}],
        },
    }
    for relative, value in documents.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(value))
    (root / "subagents").mkdir()
    (root / "subagents/reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review a fictional release.\n---\nReturn a short fictional review.\n"
    )
    return root / "a13n-harness-ui.yaml"


async def run(root: Path, model_url: str) -> dict[str, object]:
    path = write_configuration(root, model_url)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=root / "data"), pricing_auto_update=False)
    turns = []
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        thread = await app.create_thread()
        for prompt in ("[delegate] Review a fictional release.", "Summarize the fictional review."):
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt=prompt)
            operation = await app.wait_root_operation(receipt.receipt_id, timeout_seconds=60)
            if operation.status is not RootOperationStatus.completed:
                raise RuntimeError(f"Smoke operation did not complete: {operation.status}")
            assert operation.outcome is not None
            turns.append({"run_id": operation.run_id, "output": operation.outcome.execution.output})
        children = await app.query_child_executions(parent_thread_id=thread.thread_id)
        if not children.executions or any(child.persisted_status != "succeeded" for child in children.executions):
            raise RuntimeError("The native async child did not finish and save its checkpoint")
    return {"thread_id": thread.thread_id, "turns": turns, "children": len(children.executions), "data_root": str(root)}


def main() -> None:
    root = ROOT / "var/harness-ui-smoke" / uuid4().hex
    root.mkdir(parents=True)
    # Do not load the developer's instructions, skills or subscription account stores.
    home = root / "home"
    home.mkdir()
    os.environ.update(
        HOME=str(home),
        USERPROFILE=str(home),
        CODEX_HOME=str(home / ".codex"),
        XDG_CONFIG_HOME=str(home / ".config"),
        XDG_DATA_HOME=str(home / ".local/share"),
        HARNESS_UI_SMOKE_KEY="local-scripted-not-a-secret",
    )
    with model_process(port=0) as url:
        result = asyncio.run(run(root, url))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
