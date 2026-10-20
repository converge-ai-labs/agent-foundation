"""Export Service contracts and bundled Skill schemas without opening process resources."""

import argparse
import json
from pathlib import Path

from a13n_service.api_tools import tool_name
from a13n_service.app import build_app
from a13n_service.infra.platform import PLATFORM_GROUPS, PLATFORM_METADATA
from a13n_service.resources.agents.schemas import AgentConfig
from a13n_service.runs.stream import frames_schema
from a13n_service.runs.thread_stream import frames_schema as thread_frames_schema

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "proto/a13n-service"
SKILL_SCHEMA = ROOT / "packages/a13n-service/a13n_service/builtin_skills/platform-operations/schemas/agent-config.json"
PLATFORM_TOOLS = ROOT / "packages/a13n-service/a13n_service/infra/platform_tools.json"


def platform_tools(openapi: dict) -> list[dict]:
    """Every platform operation as the agent tool its MCP name identifies, ordered by group and path."""
    tools = [
        {
            "key": tool_name(operation["operationId"]),
            **operation[PLATFORM_METADATA],
            "method": method.upper(),
            "path": path,
            "summary": operation["summary"],
        }
        for path, item in openapi["paths"].items()
        for method, operation in item.items()
        if isinstance(operation, dict) and PLATFORM_METADATA in operation
    ]
    return sorted(tools, key=lambda tool: (PLATFORM_GROUPS.index(tool["group"]), tool["path"], tool["method"]))


def documents() -> dict[Path, object]:
    openapi = build_app().openapi()
    return {
        TARGET / "openapi.json": openapi,
        TARGET / "session-stream.schema.json": frames_schema(),
        TARGET / "thread-stream.schema.json": thread_frames_schema(),
        PLATFORM_TOOLS: platform_tools(openapi),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--skills-only", action="store_true", help="Prepare only release-bundled Skill schemas")
    args = parser.parse_args()
    changed = []
    outputs = {} if args.skills_only else documents()
    outputs[SKILL_SCHEMA] = AgentConfig.model_json_schema()
    for target, schema in outputs.items():
        if args.check:
            try:
                current = json.loads(target.read_text())
            except (OSError, ValueError):
                current = None
            if current != schema:
                changed.append(str(target))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    if changed:
        parser.exit(1, f"Service contract changed: {', '.join(changed)}. Run make service-contract-generate.\n")


if __name__ == "__main__":
    main()
