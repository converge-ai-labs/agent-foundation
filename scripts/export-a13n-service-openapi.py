"""Export Service contracts and bundled Skill schemas without opening process resources."""

import argparse
import json
from importlib import import_module
from pathlib import Path

from a13n_service.app import build_app
from a13n_service.distribution import OSS, Distribution
from a13n_service.infra.platform import PLATFORM_GROUPS
from a13n_service.platform_tools import validate_catalog
from a13n_service.resources.agents.schemas import AgentConfig
from a13n_service.runs.stream import frames_schema
from a13n_service.runs.thread_stream import frames_schema as thread_frames_schema

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "proto/a13n-service"
SKILL_SCHEMA = ROOT / "packages/a13n-service/a13n_service/builtin_skills/platform-operations/schemas/agent-config.json"
PLATFORM_TOOLS = ROOT / "packages/a13n-service/a13n_service/infra/platform_tools.json"


def platform_tools(distribution: Distribution = OSS) -> list[dict]:
    """Internal contracts come only from explicit platform tool definitions."""
    definitions = tuple(tool for contribution in distribution.platform_tools for tool in contribution())
    validate_catalog(definitions)
    return sorted(
        [
            {"key": tool.name, "group": tool.group, "effect": tool.effect, "summary": tool.description}
            for tool in definitions
        ],
        key=lambda tool: (PLATFORM_GROUPS.index(tool["group"]), tool["key"]),
    )


def documents(distribution: Distribution = OSS) -> dict[Path, object]:
    openapi = build_app(distribution=distribution).openapi()
    return {
        TARGET / "openapi.json": openapi,
        TARGET / "session-stream.schema.json": frames_schema(),
        TARGET / "thread-stream.schema.json": thread_frames_schema(),
        PLATFORM_TOOLS: platform_tools(distribution),
    }


def load_distribution(reference: str) -> Distribution:
    """Load the explicitly selected artifact descriptor, never scan installed packages."""
    module, separator, attribute = reference.partition(":")
    if not separator or not module or not attribute:
        raise ValueError("Distribution must be module:attribute")
    value = getattr(import_module(module), attribute)
    if callable(value):
        value = value()
    if not isinstance(value, Distribution):
        raise TypeError("Distribution reference must name a Distribution or its zero-argument factory")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--skills-only", action="store_true", help="Prepare only release-bundled Skill schemas")
    parser.add_argument(
        "--distribution",
        default="a13n_service.distribution:OSS",
        help="Fixed artifact Distribution as module:attribute or zero-argument factory",
    )
    args = parser.parse_args()
    distribution = load_distribution(args.distribution)
    changed = []
    outputs = {} if args.skills_only else documents(distribution)
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
